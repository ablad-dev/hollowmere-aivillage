"""Configuration loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "village.db"
VILLAGE_PATH = ROOT / "village.json"
PRICES_PATH = ROOT / "prices.json"


def _load_prices() -> dict:
    """USD per 1M tokens, keyed by deployment name."""
    try:
        import json

        raw = json.loads(PRICES_PATH.read_text(encoding="utf-8"))
        return {k: v for k, v in raw.items() if isinstance(v, dict) and "in" in v}
    except Exception:  # noqa: BLE001 - pricing is cosmetic, never fatal
        return {"__default__": {"in": 0.15, "out": 0.60}}


def _b(key: str, default: bool) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _i(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except ValueError:
        return default


def _f(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, default))
    except ValueError:
        return default


@dataclass
class Settings:
    # --- Microsoft Foundry ---
    endpoint: str = os.getenv("FOUNDRY_ENDPOINT", "").rstrip("/")
    api_key: str = os.getenv("FOUNDRY_API_KEY", "")
    api_version: str = os.getenv("FOUNDRY_API_VERSION", "")
    embedding_deployment: str = os.getenv("FOUNDRY_EMBEDDING_DEPLOYMENT", "")
    # Deployment used when an agent does not name its own.
    default_deployment: str = os.getenv("FOUNDRY_DEFAULT_DEPLOYMENT", "gpt-4o-mini")
    # Larger/slower model used for reflections + daily planning.
    reflection_deployment: str = os.getenv(
        "FOUNDRY_REFLECTION_DEPLOYMENT", os.getenv("FOUNDRY_DEFAULT_DEPLOYMENT", "gpt-4o-mini")
    )

    # Runs the whole sim with a scripted fake LLM. No cloud calls, no cost.
    mock_llm: bool = _b("MOCK_LLM", False)

    # USD per 1M tokens, keyed by deployment name (see prices.json).
    prices: dict = field(default_factory=_load_prices)

    # --- Simulation clock ---
    # One tick advances the in-world clock by `minutes_per_tick`.
    minutes_per_tick: int = _i("MINUTES_PER_TICK", 10)
    seconds_per_tick: float = _f("SECONDS_PER_TICK", 2.0)
    start_hour: int = _i("START_HOUR", 7)

    # --- Cost control ---
    max_concurrent_llm: int = _i("MAX_CONCURRENT_LLM", 4)
    max_concurrent_embed: int = _i("MAX_CONCURRENT_EMBED", 8)
    llm_timeout: float = _f("LLM_TIMEOUT", 45.0)
    # Agent re-plans its next action at most this often (in ticks).
    decision_cooldown_ticks: int = _i("DECISION_COOLDOWN_TICKS", 2)
    dialogue_turns: int = _i("DIALOGUE_TURNS", 6)
    # Reflect once accumulated importance since last reflection crosses this.
    reflection_threshold: float = _f("REFLECTION_THRESHOLD", 40.0)
    retrieval_k: int = _i("RETRIEVAL_K", 8)

    # --- Needs ---
    # Per-tick decay. Needs run 0 (desperate) .. 100 (satisfied).
    decay: dict = field(
        default_factory=lambda: {
            "hunger": _f("DECAY_HUNGER", 0.9),
            "energy": _f("DECAY_ENERGY", 0.6),
            "social": _f("DECAY_SOCIAL", 0.7),
            "fun": _f("DECAY_FUN", 0.5),
        }
    )

    @property
    def live(self) -> bool:
        return bool(self.endpoint and self.api_key) and not self.mock_llm


settings = Settings()
