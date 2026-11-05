"""Prompt construction. Kept in one place so you can tune the village's soul."""
from __future__ import annotations

from .world import world

ACTIONS = ["work", "eat", "sleep", "socialise", "relax", "shop", "wander"]


def _needs_line(a: dict) -> str:
    def band(v: float) -> str:
        return "desperate" if v < 20 else "low" if v < 45 else "fine" if v < 75 else "good"

    return (
        f"hunger {a['hunger']:.0f}/100 ({band(a['hunger'])}), "
        f"energy {a['energy']:.0f}/100 ({band(a['energy'])}), "
        f"social {a['social']:.0f}/100 ({band(a['social'])}), "
        f"fun {a['fun']:.0f}/100 ({band(a['fun'])})"
    )


def identity(a: dict) -> str:
    return (
        f"You are {a['name']}, the {a['job']} of {world.name}.\n"
        f"{a['persona']}\n"
        "Needs run 0-100; lower is more urgent. You are a person, not an assistant."
    )


# ---------------------------------------------------------------- decisions
DECIDE_SYSTEM = (
    "You choose a villager's next action in a life simulation. "
    'Reply with JSON only: {"action": str, "location": str, "thought": str}. '
    f'"action" must be one of {ACTIONS}. '
    '"location" must be copied exactly from the location list. '
    '"thought" is one short sentence of inner monologue, in first person. '
    "Let your personality and your most urgent need drive the choice. "
    "Do not always pick the obvious thing; people are inconsistent."
)


def decide_user(a: dict, clock: str, memories: list[dict], nearby: list[str], plan_now: str) -> str:
    mem = "\n".join(f"- [{m['when']}] {m['content']}" for m in memories) or "- (nothing yet)"
    near = ", ".join(nearby) if nearby else "nobody"
    return (
        f"{identity(a)}\n\n"
        f"Time: {clock}\n"
        f"You are at: {a['area']} ({a['location'] or 'open ground'})\n"
        f"Needs: {_needs_line(a)}\n"
        f"Coins: {a['coins']}\n"
        f"Nearby: {near}\n"
        f"Today's plan says: {plan_now or 'nothing in particular'}\n\n"
        f"What you remember right now:\n{mem}\n\n"
        f"Places you can go:\n{world.catalogue()}\n\n"
        "Choose your next action and where to do it."
    )


# ------------------------------------------------------------------- plans
PLAN_SYSTEM = (
    "You write a villager's plan for the day. Reply as JSON: "
    '{"plan": [{"time": "HH:MM", "activity": str, "location": str}]} with 5-7 entries '
    "in chronological order from waking to sleeping. "
    '"location" must be copied exactly from the location list. '
    "Reflect the character's job, personality, and unresolved business."
)


def plan_user(a: dict, day: int, memories: list[dict]) -> str:
    mem = "\n".join(f"- {m['content']}" for m in memories) or "- (nothing yet)"
    return (
        f"{identity(a)}\n\n"
        f"It is the morning of day {day}.\n"
        f"Needs: {_needs_line(a)}\nCoins: {a['coins']}\n\n"
        f"On your mind:\n{mem}\n\n"
        f"Places:\n{world.catalogue()}\n\n"
        "Write your plan for today."
    )


# --------------------------------------------------------------- dialogue
DIALOGUE_SYSTEM = (
    "You speak as one villager in a conversation. Reply with ONE line of natural speech, "
    "under 30 words, no quotation marks, no narration, no stage directions. "
    "Stay in character. Pursue what you actually want from this person. "
    "You may lie, deflect, flirt, bargain, or end the conversation."
)


def dialogue_user(
    a: dict, other_name: str, place: str, clock: str, memories: list[dict],
    relationship: dict, transcript: list[str], opener: bool,
) -> str:
    mem = "\n".join(f"- {m['content']}" for m in memories) or "- (nothing specific)"
    rel = (
        f"You have met {other_name} {relationship['encounters']} time(s). "
        f"Affinity {relationship['affinity']:+.0f} (-100 hostile .. +100 devoted). "
        f"{relationship['summary'] or 'No strong impression yet.'}"
    )
    convo = "\n".join(transcript) if transcript else "(you speak first)"
    return (
        f"{identity(a)}\n\n"
        f"Time: {clock}. You are at {place} with {other_name}.\n"
        f"Needs: {_needs_line(a)}\nCoins: {a['coins']}\n"
        f"{rel}\n\n"
        f"What you recall about {other_name} and recent events:\n{mem}\n\n"
        f"Conversation so far:\n{convo}\n\n"
        + ("Open the conversation." if opener else f"Reply to {other_name}.")
    )


# ------------------------------------------------------- post-conversation
DIGEST_SYSTEM = (
    "You summarise a conversation from one participant's point of view. Reply as JSON: "
    '{"takeaway": str, "affinity_delta": -20..20, "impression": str, "importance": 1-10}. '
    '"takeaway" is one first-person sentence of what they will remember. '
    '"impression" is a short updated view of the other person.'
)


def digest_user(a: dict, other_name: str, transcript: list[str]) -> str:
    return (
        f"You are {a['name']}. {a['persona']}\n\n"
        f"Conversation with {other_name}:\n" + "\n".join(transcript) + "\n\n"
        "What do you take away from that, and how do you feel about them now?"
    )
