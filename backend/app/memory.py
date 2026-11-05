"""The memory stream.

Retrieval scores each memory on recency, importance, and relevance - the
three-term formula from the Generative Agents paper. Reflection periodically
compresses raw observations into higher-level beliefs, which are themselves
stored as memories and can be retrieved later.
"""
from __future__ import annotations

import json
import math

from . import db, llm
from .config import settings

# Exponential decay applied per tick of age.
RECENCY_DECAY = 0.995


async def remember(
    agent_id: str,
    kind: str,
    content: str,
    tick: int,
    label: str,
    importance: float | None = None,
) -> int:
    if importance is None:
        importance = score_importance(kind, content)
    vec, src = await llm.embed(content)
    cur = db.run(
        "INSERT INTO memories(agent_id, kind, content, importance, created_tick, created_label,"
        " last_access, embedding, embed_src) VALUES(?,?,?,?,?,?,?,?,?)",
        (agent_id, kind, content, importance, tick, label, tick, vec, src),
    )
    db.run(
        "UPDATE agents SET unreflected = unreflected + ? WHERE id = ?",
        (importance, agent_id),
    )
    return int(cur.lastrowid or 0)


def score_importance(kind: str, content: str) -> float:
    """Cheap heuristic so routine observations cost nothing to grade."""
    base = {
        "observation": 2.0,
        "action": 2.5,
        "dialogue": 5.0,
        "reflection": 7.0,
        "event": 7.5,
        "plan": 4.0,
        "trade": 4.0,
    }.get(kind, 3.0)
    lowered = content.lower()
    for word, bump in (
        ("died", 4), ("fire", 3), ("stole", 3), ("love", 3), ("secret", 3),
        ("fight", 2.5), ("gift", 2), ("argu", 2), ("promise", 2), ("betray", 3.5),
    ):
        if word in lowered:
            base += bump
    return max(1.0, min(10.0, base))


async def retrieve(agent_id: str, query: str, tick: int, k: int | None = None) -> list[dict]:
    k = k or settings.retrieval_k
    rows = db.q(
        "SELECT * FROM memories WHERE agent_id = ? ORDER BY created_tick DESC LIMIT 400",
        (agent_id,),
    )
    if not rows:
        return []

    blob, qsrc = await llm.embed(query)
    qvec = llm.unpack(blob)
    scored = []
    for row in rows:
        age = max(0, tick - row["last_access"])
        recency = math.pow(RECENCY_DECAY, age)
        importance = row["importance"] / 10.0
        # Hash and API vectors share a width but not a space. Comparing across
        # them yields noise, so fall back to recency + importance alone.
        comparable = row["embedding"] and row["embed_src"] == qsrc
        relevance = llm.cosine(qvec, llm.unpack(row["embedding"])) if comparable else 0.0
        scored.append((recency + importance + relevance, row))

    scored.sort(key=lambda t: t[0], reverse=True)
    top = scored[:k]
    ids = [r["id"] for _, r in top]
    if ids:
        db.run(
            f"UPDATE memories SET last_access = ? WHERE id IN ({','.join('?' * len(ids))})",
            [tick, *ids],
        )
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "content": r["content"],
            "importance": r["importance"],
            "when": r["created_label"],
            "score": round(s, 3),
        }
        for s, r in top
    ]


async def maybe_reflect(agent: dict, tick: int, label: str) -> list[str]:
    """Compress recent experience into beliefs once enough has happened."""
    if agent["unreflected"] < settings.reflection_threshold:
        return []
    db.run("UPDATE agents SET unreflected = 0 WHERE id = ?", (agent["id"],))

    rows = db.q(
        "SELECT content, created_label FROM memories WHERE agent_id = ? "
        "ORDER BY created_tick DESC LIMIT 40",
        (agent["id"],),
    )
    if len(rows) < 5:
        return []

    recent = "\n".join(f"[{r['created_label']}] {r['content']}" for r in reversed(rows))
    system = (
        "You distil a character's recent experiences into durable beliefs. "
        "Reflect on what matters and what it implies. Reply as JSON: "
        '{"reflections": [{"insight": str, "importance": 1-10}]} with 2-3 entries. '
        "Insights must be specific, first person, and go beyond restating events."
    )
    user = (
        f"You are {agent['name']}. {agent['persona']}\n\n"
        f"Recent memories:\n{recent}\n\n"
        "What have you come to believe? Name people where relevant."
    )
    data = await llm.chat_json(
        settings.reflection_deployment, system, user, temperature=0.8, max_tokens=400
    )

    insights: list[str] = []
    for item in (data.get("reflections") or [])[:3]:
        text = (item.get("insight") or "").strip() if isinstance(item, dict) else str(item).strip()
        if not text:
            continue
        importance = float(item.get("importance", 7)) if isinstance(item, dict) else 7.0
        await remember(
            agent["id"], "reflection", text, tick, label, min(10.0, max(1.0, importance))
        )
        insights.append(text)
    return insights


def recent(agent_id: str, limit: int = 30) -> list[dict]:
    rows = db.q(
        "SELECT id, kind, content, importance, created_label FROM memories "
        "WHERE agent_id = ? ORDER BY id DESC LIMIT ?",
        (agent_id, limit),
    )
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "content": r["content"],
            "importance": r["importance"],
            "when": r["created_label"],
        }
        for r in rows
    ]
