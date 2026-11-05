"""Microsoft Foundry client.

Every agent can point at a different deployment, so the village becomes a
side-by-side comparison of models reacting to identical situations.

Falls back to a deterministic mock when no endpoint is configured, so the
simulation is runnable before any cloud resources exist.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import struct
from typing import Any

from .config import settings

log = logging.getLogger("village.llm")

_semaphore = asyncio.Semaphore(settings.max_concurrent_llm)
# Embeddings get their own budget: two fire on every decision (one to store the
# outcome, one to build the retrieval query), and they must not queue behind
# slow dialogue calls.
_embed_semaphore = asyncio.Semaphore(settings.max_concurrent_embed)
_client: Any = None
_embed_client: Any = None

# Learned per-deployment API quirks. Newer reasoning-family deployments reject
# `temperature` and require `max_completion_tokens`; many open-weight ones
# reject `response_format`. We pay to discover each quirk exactly once.
_caps: dict[str, dict[str, Any]] = {}

# Per-deployment accounting, surfaced in the UI.
usage: dict[str, dict[str, Any]] = {}

JSON_NUDGE = (
    "\n\nRespond with raw JSON only. No prose, no markdown fences, no explanation."
)


# Reasoning deployments bill hidden thinking tokens against the completion
# budget. A village prompt needs ~100 tokens of answer but the model may think
# for several hundred first, so once we know a deployment reasons we hand it a
# much larger ceiling and ask it to think briefly.
REASONING_HEADROOM = 1600


def caps_for(deployment: str) -> dict[str, Any]:
    return _caps.setdefault(
        deployment,
        {
            "json": True,
            "temperature": True,
            "tokens_param": "max_tokens",
            "reasoning": False,
            "effort": None,
        },
    )


def _adapt(deployment: str, caps: dict[str, Any], exc: Exception) -> str | None:
    """Inspect an API error and relax one unsupported parameter.

    Returns a label for what was changed, or None if the error isn't one we
    know how to work around.
    """
    msg = str(exc).lower()
    unsupported = any(
        s in msg for s in ("unsupported", "not supported", "does not support", "invalid")
    )

    if caps["tokens_param"] == "max_tokens" and "max_completion_tokens" in msg:
        caps["tokens_param"] = "max_completion_tokens"
        return "max_completion_tokens"
    if caps["temperature"] and "temperature" in msg and unsupported:
        caps["temperature"] = False
        return "fixed-temperature"
    if caps["json"] and "response_format" in msg:
        caps["json"] = False
        _slot(deployment)["json_mode"] = "prompted"
        return "prompted-json"
    if caps["effort"] and "reasoning_effort" in msg:
        # The deployment reasons but won't be told how hard. Headroom alone
        # still gets us an answer.
        caps["effort"] = None
        return "default-reasoning-effort"
    return None


def _slot(deployment: str) -> dict[str, Any]:
    return usage.setdefault(
        deployment,
        {
            "calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "fallbacks": 0,
            "json_mode": "native",
        },
    )


def _record(deployment: str, prompt_tokens: int, completion_tokens: int) -> None:
    slot = _slot(deployment)
    slot["calls"] += 1
    slot["prompt_tokens"] += prompt_tokens
    slot["completion_tokens"] += completion_tokens


def cost_for(deployment: str, prompt_tokens: int, completion_tokens: int) -> float:
    rate = settings.prices.get(deployment) or settings.prices.get("__default__", {})
    return (
        prompt_tokens * rate.get("in", 0.0) + completion_tokens * rate.get("out", 0.0)
    ) / 1_000_000


def usage_report() -> dict[str, dict[str, Any]]:
    out = {}
    for name, slot in usage.items():
        rate = settings.prices.get(name) or settings.prices.get("__default__", {})
        caps = caps_for(name)
        out[name] = {
            **slot,
            "rate_in": rate.get("in", 0.0),
            "rate_out": rate.get("out", 0.0),
            "priced": name in settings.prices,
            "quirks": [
                q
                for q in (
                    "prompted-json" if not caps["json"] else "",
                    "fixed-temperature" if not caps["temperature"] else "",
                    "max_completion_tokens"
                    if caps["tokens_param"] != "max_tokens"
                    else "",
                    "reasoning" if caps["reasoning"] else "",
                )
                if q
            ],
            "cost": round(
                cost_for(name, slot["prompt_tokens"], slot["completion_tokens"]), 4
            ),
        }
    return out


def get_client():
    """Lazily build an OpenAI-compatible client pointed at Foundry."""
    global _client
    if _client is not None:
        return _client
    if not settings.live:
        return None

    endpoint = settings.endpoint
    # Classic Azure OpenAI resources (and anyone who pins an api-version) use
    # the AzureOpenAI surface; Foundry projects expose an OpenAI /v1 route.
    if settings.api_version or ".openai.azure.com" in endpoint:
        from openai import AsyncAzureOpenAI

        _client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=settings.api_key,
            api_version=settings.api_version or "2024-10-21",
            timeout=settings.llm_timeout,
        )
    else:
        from openai import AsyncOpenAI

        base = endpoint if endpoint.endswith("/openai/v1") else f"{endpoint}/openai/v1"
        _client = AsyncOpenAI(
            base_url=base,
            api_key=settings.api_key,
            timeout=settings.llm_timeout,
        )
    return _client


# Foundry *project* endpoints look like
# https://<res>.services.ai.azure.com/api/projects/<project>
_PROJECT_PATH = re.compile(r"/api/projects/[^/]+/?$")


def get_embed_client():
    """Client for the embeddings route.

    A Foundry project endpoint proxies /chat/completions but *not*
    /embeddings, which answers 404 with an empty body - no error payload to
    adapt to. Embeddings are served from the resource root, so strip the
    project path when there is one and reuse the chat client when there isn't.
    """
    global _embed_client
    if _embed_client is not None:
        return _embed_client
    chat_client = get_client()
    if chat_client is None:
        return None

    endpoint = settings.endpoint.rstrip("/")
    root = _PROJECT_PATH.sub("", endpoint)
    if root == endpoint:
        return chat_client

    from openai import AsyncOpenAI

    log.info("Embeddings routed to resource root %s (project path stripped)", root)
    _embed_client = AsyncOpenAI(
        base_url=f"{root}/openai/v1",
        api_key=settings.api_key,
        timeout=settings.llm_timeout,
    )
    return _embed_client


async def chat(
    deployment: str,
    system: str,
    user: str,
    *,
    temperature: float = 0.9,
    max_tokens: int = 300,
    json_mode: bool = False,
) -> str:
    deployment = deployment or settings.default_deployment
    client = get_client()
    if client is None:
        return _mock_chat(system, user, json_mode=json_mode)

    caps = caps_for(deployment)

    def build() -> dict[str, Any]:
        # When a deployment can't be told to emit JSON, ask for it in the prompt
        # and let parse_json salvage the result.
        use_format = json_mode and caps["json"]
        prompt = system if (use_format or not json_mode) else system + JSON_NUDGE
        budget = max_tokens + REASONING_HEADROOM if caps["reasoning"] else max_tokens
        kw: dict[str, Any] = {
            "model": deployment,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user},
            ],
            caps["tokens_param"]: budget,
        }
        if use_format:
            kw["response_format"] = {"type": "json_object"}
        if caps["temperature"]:
            kw["temperature"] = temperature
        if caps["effort"]:
            kw["reasoning_effort"] = caps["effort"]
        return kw

    content = ""
    async with _semaphore:
        # Learning the shape costs one attempt per quirk (4 max), plus one to
        # succeed, plus headroom for a lost race.
        for _ in range(7):
            snapshot = dict(caps)
            try:
                resp = await client.chat.completions.create(**build())
            except Exception as exc:  # noqa: BLE001 - one agent must not kill the sim
                # A concurrent call for this deployment may have learned the
                # quirk while our request was already in flight. Our request
                # used the stale shape, so the error is real but _adapt would
                # see the cap already relaxed and call it unadaptable. Rebuild
                # against the fresh caps instead of falling back to a mock.
                if caps != snapshot:
                    log.info("%s: retrying with refreshed capabilities", deployment)
                    continue
                changed = _adapt(deployment, caps, exc)
                if not changed:
                    log.warning("LLM call failed on %s: %s", deployment, exc)
                    _slot(deployment)["fallbacks"] += 1
                    return _mock_chat(system, user, json_mode=json_mode)
                log.info("%s: retrying with %s (%s)", deployment, changed, exc)
                continue

            # Billed whether or not the reply was usable.
            u = getattr(resp, "usage", None)
            _record(
                deployment,
                getattr(u, "prompt_tokens", 0) or 0,
                getattr(u, "completion_tokens", 0) or 0,
            )
            choice = resp.choices[0]
            content = (choice.message.content or "").strip()
            if content:
                break

            # An empty body that stopped on `length` means the whole budget went
            # to hidden reasoning tokens. Silently returning "" here is what
            # makes an agent look alive while never deciding anything, so learn
            # the quirk and buy the model room to think.
            if getattr(choice, "finish_reason", "") == "length" and not caps["reasoning"]:
                caps["reasoning"] = True
                caps["effort"] = "low"
                log.info(
                    "%s: empty reply, retrying with reasoning headroom", deployment
                )
                continue
            if caps != snapshot:
                continue
            break

    if not content:
        log.warning("LLM call on %s returned no usable content", deployment)
        _slot(deployment)["fallbacks"] += 1
        return _mock_chat(system, user, json_mode=json_mode)
    return content


async def chat_json(deployment: str, system: str, user: str, **kw) -> dict:
    raw = await chat(deployment, system, user, json_mode=True, **kw)
    return parse_json(raw)


def parse_json(raw: str) -> dict:
    """Models wander outside strict JSON; salvage what we can."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw).rstrip("`").strip()
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"value": value}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {}


# --------------------------------------------------------------------------
# Embeddings
# --------------------------------------------------------------------------

EMBED_DIM = 256


async def embed(text: str) -> tuple[bytes, str]:
    """Return (packed float vector, source tag).

    Requests `dimensions=EMBED_DIM` so real embeddings stay the same width as
    the local fallback. text-embedding-3-small is Matryoshka-trained, so a
    256-dim slice keeps most of the quality while cutting the stored vector
    6x and keeping the pure-Python cosine in `memory.retrieve` cheap - it runs
    over a few hundred memories on every single decision.

    Because both paths are now the same width, a vector's *source* is the only
    way to tell them apart. Comparing an API vector against a hash vector
    produces noise, not a low score, so callers must not mix them.
    """
    client = get_embed_client()
    if client is not None and settings.embedding_deployment:
        name = settings.embedding_deployment
        caps = caps_for(name)
        kwargs: dict[str, Any] = {"model": name, "input": text[:8000]}
        if caps.get("dimensions", True):
            kwargs["dimensions"] = EMBED_DIM
        try:
            async with _embed_semaphore:
                try:
                    resp = await client.embeddings.create(**kwargs)
                except Exception as exc:  # noqa: BLE001
                    if "dimensions" not in kwargs or "dimensions" not in str(exc).lower():
                        raise
                    log.info("%s rejected `dimensions`; using native width", name)
                    caps["dimensions"] = False
                    kwargs.pop("dimensions")
                    resp = await client.embeddings.create(**kwargs)

            u = getattr(resp, "usage", None)
            _record(name, getattr(u, "prompt_tokens", 0) or 0, 0)
            vec = resp.data[0].embedding
            return struct.pack(f"{len(vec)}f", *vec), "api"
        except Exception as exc:  # noqa: BLE001
            log.warning("Embedding failed, using local hash vector: %s", exc)
            _slot(name)["fallbacks"] += 1
    return _hash_embed(text), "hash"


def _hash_embed(text: str) -> bytes:
    """Deterministic bag-of-words hashing vector.

    Keeps semantic-ish retrieval working with zero cloud calls. Good enough to
    rank "did I talk to Marcus" above "I ate bread".
    """
    vec = [0.0] * EMBED_DIM
    for token in re.findall(r"[a-z0-9']+", text.lower()):
        h = int.from_bytes(hashlib.md5(token.encode()).digest()[:4], "little")
        vec[h % EMBED_DIM] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return struct.pack(f"{EMBED_DIM}f", *[v / norm for v in vec])


def unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


# --------------------------------------------------------------------------
# Mock mode
# --------------------------------------------------------------------------

_MOCK_LINES = [
    "I should probably keep an eye on that.",
    "Honestly? I'm just here for the bread.",
    "Someone has been moving my things.",
    "Do you ever feel like the sky is only painted on?",
    "I'll trade you, but I drive a hard bargain.",
    "That is the third strange thing today.",
]


def _mock_chat(system: str, user: str, *, json_mode: bool) -> str:
    rng = random.Random(hashlib.md5((system + user).encode()).hexdigest())
    if not json_mode:
        return rng.choice(_MOCK_LINES)

    if '"plan"' in system or "schedule" in system.lower():
        return json.dumps(
            {
                "plan": [
                    {"time": "08:00", "activity": "eat breakfast", "location": "Bakery"},
                    {"time": "10:00", "activity": "work", "location": "Market"},
                    {"time": "13:00", "activity": "socialise", "location": "Tavern"},
                    {"time": "18:00", "activity": "relax", "location": "Park"},
                    {"time": "22:00", "activity": "sleep", "location": "Cottage A"},
                ]
            }
        )
    if "reflect" in system.lower():
        return json.dumps(
            {"reflections": [{"insight": "The village is smaller than it looks.", "importance": 5}]}
        )
    locations = re.findall(r"- ([A-Za-z ]+?) \(", user)
    return json.dumps(
        {
            "action": rng.choice(["work", "eat", "socialise", "wander", "rest"]),
            "location": rng.choice(locations) if locations else "Plaza",
            "thought": rng.choice(_MOCK_LINES),
        }
    )
