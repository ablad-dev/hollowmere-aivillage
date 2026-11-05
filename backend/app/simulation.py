"""The tick loop: needs, movement, work, conversation, reflection.

Design note: the LLM is only consulted when a villager genuinely has a choice
to make (idle with no plan step, starting a conversation, closing out the day).
Walking, eating, earning and decaying are all cheap local simulation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any, Callable

from . import db, llm, memory, prompts, seed
from .config import settings
from .world import Location, world

log = logging.getLogger("village.sim")

MOVE_TILES_PER_TICK = 4
WAGE = {"baker": 4, "merchant": 5, "farmer": 3, "smith": 5, "clerk": 4, "scribe": 3}
ACTION_TICKS = {
    "work": 18, "eat": 3, "sleep": 48, "socialise": 6,
    "relax": 6, "shop": 2, "wander": 4, "talk": 0,
}
# An action is only meaningful at a location that supports it. "wander" is
# deliberately unconstrained.
ACTION_KIND = {
    "work": "work", "eat": "eat", "sleep": "sleep", "shop": "trade",
    "socialise": "social", "relax": "fun",
}


class Clock:
    def __init__(self, tick: int):
        self.tick = tick

    @property
    def minutes(self) -> int:
        return settings.start_hour * 60 + self.tick * settings.minutes_per_tick

    @property
    def day(self) -> int:
        return self.minutes // 1440 + 1

    @property
    def hour(self) -> int:
        return (self.minutes % 1440) // 60

    @property
    def minute(self) -> int:
        return self.minutes % 60

    @property
    def label(self) -> str:
        return f"Day {self.day}, {self.hour:02d}:{self.minute:02d}"

    @property
    def is_night(self) -> bool:
        return self.hour >= 22 or self.hour < 6


class Simulation:
    def __init__(self, broadcast: Callable[[dict], Any] | None = None):
        self.broadcast = broadcast or (lambda _m: None)
        self.tick = 0
        self.running = False
        self.paused = False
        self.agents: dict[str, dict] = {}
        self.convo_cooldown: dict[frozenset, int] = {}
        self.pending: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self.feed: list[dict] = []

    # ------------------------------------------------------------------ setup
    def load(self) -> None:
        db.conn()
        if not db.q("SELECT 1 FROM agents LIMIT 1"):
            self._seed()
        self.tick = db.get_state("tick", 0)
        for row in db.q("SELECT * FROM agents"):
            a = dict(row)
            a["plan"] = json.loads(a.pop("plan_json") or "[]")
            a.update(path=[], trail=[], busy=False, cooldown=0, action_until=0,
                     speech="", partner=None)
            a["location"] = self._location_name(a["x"], a["y"])
            a["area"] = world.area_at(a["x"], a["y"])
            self.agents[a["id"]] = a
        self.feed = [
            {"tick": r["tick"], "label": r["label"], "kind": r["kind"], "text": r["text"]}
            for r in db.q("SELECT * FROM chronicle ORDER BY id DESC LIMIT 60")
        ][::-1]

    def _seed(self) -> None:
        for v in seed.resolved():
            bed = world.locations.get(v["home"])
            x, y = (bed.x, bed.y) if bed else (24, 20)
            db.run(
                "INSERT INTO agents(id,name,persona,deployment,model_label,color,sprite,job,home,"
                "x,y,coins) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (v["id"], v["name"], v["persona"], v["deployment"], v["model_label"], v["color"],
                 v["sprite"], v["job"], v["home"], x, y, 25),
            )
        log.info("Seeded %d villagers", len(seed.VILLAGERS))

    def reset(self) -> None:
        db.reset()
        self.tick = 0
        self.agents.clear()
        self.convo_cooldown.clear()
        self.feed.clear()
        self.load()

    # ------------------------------------------------------------------ loop
    async def run(self) -> None:
        self.running = True
        while self.running:
            if not self.paused:
                try:
                    await self.step()
                except Exception:  # noqa: BLE001 - a bad tick must not kill the village
                    log.exception("tick failed")
            await asyncio.sleep(settings.seconds_per_tick)

    async def stop(self) -> None:
        self.running = False
        for t in list(self._tasks):
            t.cancel()

    async def step(self) -> None:
        self.tick += 1
        clock = Clock(self.tick)

        for agent in self.agents.values():
            agent["trail"] = []
            agent["cooldown"] = max(0, agent["cooldown"] - 1)
            self._decay(agent, clock)
            if agent["busy"]:
                continue
            if agent["path"]:
                self._advance(agent)
                if not agent["path"]:
                    self._spawn(self._on_arrival(agent, clock))
                continue
            self._perform(agent, clock)

        self._maybe_converse(clock)

        for agent in self.agents.values():
            if agent["busy"] or agent["path"] or agent["id"] in self.pending:
                continue
            if agent["plan_day"] != clock.day and 6 <= clock.hour < 11:
                self._spawn(self._make_plan(agent, clock))
            elif agent["action"] in ("idle", "") or self.tick >= agent["action_until"]:
                if agent["cooldown"] == 0 and not self._survive(agent, clock):
                    self._spawn(self._decide(agent, clock))

        self._persist()
        db.set_state("tick", self.tick)
        await self._emit_tick(clock)

    # ------------------------------------------------------------- mechanics
    def _decay(self, a: dict, clock: Clock) -> None:
        sleeping = a["action"] == "sleep"
        for need, rate in settings.decay.items():
            mult = 0.35 if sleeping else 1.0
            a[need] = max(0.0, a[need] - rate * mult)

        loc = world.resolve(a["target"]) if a["target"] else None
        at_target = bool(loc and (a["x"], a["y"]) == (loc.x, loc.y))

        if a["action"] == "sleep" and at_target:
            a["energy"] = min(100.0, a["energy"] + 3.0)
            if a["energy"] >= 98 and clock.hour >= 6:
                a["action_until"] = self.tick
        elif a["action"] == "eat" and at_target:
            a["hunger"] = min(100.0, a["hunger"] + 30.0)
        elif a["action"] == "work" and at_target and loc and loc.job == a["job"]:
            a["coins"] += WAGE.get(a["job"] or "", 3)
            a["fun"] = max(0.0, a["fun"] - 0.4)
        elif a["action"] in ("relax", "wander") and at_target:
            a["fun"] = min(100.0, a["fun"] + 6.0)
        elif a["action"] == "socialise" and at_target:
            company = [o for o in self.agents.values() if o["id"] != a["id"] and self._near(a, o, 2)]
            a["social"] = min(100.0, a["social"] + (9.0 if company else 1.0))
            a["fun"] = min(100.0, a["fun"] + 2.0)

        for need, threshold, note in (
            ("hunger", 12, "I am genuinely hungry and it is starting to colour everything."),
            ("energy", 12, "I am exhausted. I cannot keep this up."),
            ("social", 12, "Nobody has spoken to me in far too long."),
        ):
            if a[need] < threshold and random.random() < 0.05:
                self._spawn(memory.remember(a["id"], "observation", note, self.tick, clock.label, 5))

    def _advance(self, a: dict) -> None:
        for _ in range(MOVE_TILES_PER_TICK):
            if not a["path"]:
                break
            a["x"], a["y"] = a["path"].pop(0)
            a["trail"].append([a["x"], a["y"]])
        a["area"] = world.area_at(a["x"], a["y"])
        a["location"] = self._location_name(a["x"], a["y"])

    def _perform(self, a: dict, clock: Clock) -> None:
        """Charge for consumption the moment an action begins at its location."""
        if a.get("_charged") or a["action"] not in ("eat", "shop"):
            return
        loc = world.resolve(a["target"])
        if not loc or (a["x"], a["y"]) != (loc.x, loc.y) or not loc.price:
            return
        if a["coins"] >= loc.price:
            a["coins"] -= loc.price
            self._pay_vendor(loc, a, clock)
        else:
            a["hunger"] = max(0.0, a["hunger"] - 2)
            self._spawn(memory.remember(
                a["id"], "trade", f"I could not afford anything at {loc.name}. I am out of coin.",
                self.tick, clock.label, 6))
        a["_charged"] = True

    def _pay_vendor(self, loc: Location, buyer: dict, clock: Clock) -> None:
        work_here = next(
            (l for l in world.locations.values() if l.area == loc.area and l.job), None
        )
        if not work_here:
            return
        vendor = next((o for o in self.agents.values() if o["job"] == work_here.job), None)
        if not vendor or vendor["id"] == buyer["id"]:
            return
        vendor["coins"] += loc.price
        if random.random() < 0.3:
            self._spawn(memory.remember(
                vendor["id"], "trade", f"{buyer['name']} bought from me at {loc.name}.",
                self.tick, clock.label, 3))

    def _near(self, a: dict, b: dict, radius: int = 1) -> bool:
        return abs(a["x"] - b["x"]) <= radius and abs(a["y"] - b["y"]) <= radius

    def _location_name(self, x: int, y: int) -> str:
        for loc in world.locations.values():
            if (loc.x, loc.y) == (x, y):
                return loc.name
        return ""

    def _goto(self, a: dict, loc: Location) -> None:
        a["target"] = loc.name
        a["path"] = world.path((a["x"], a["y"]), (loc.x, loc.y))
        a["_charged"] = False

    # -------------------------------------------------------------- decisions
    def _survive(self, a: dict, clock: Clock) -> bool:
        """Bypass the model when a need is critical.

        Keeps the village alive regardless of how well a given deployment
        follows instructions, and costs nothing.
        """
        if a["hunger"] < 15:
            action, loc = "eat", self._best_food(a)
        elif a["energy"] < 12:
            action, loc = "sleep", world.locations.get(a["home"])
        else:
            return False
        if not loc:
            return False

        a["action"] = action
        a["thought"] = "I cannot think about anything else right now."
        a["action_until"] = self.tick + ACTION_TICKS.get(action, 6)
        a["cooldown"] = settings.decision_cooldown_ticks
        self._goto(a, loc)
        self._chronicle(clock, "action", f"{a['name']} → {action} @ {loc.name} (desperate)")
        return True

    def _best_food(self, a: dict) -> Location | None:
        options = world.by_kind("eat")
        if not options:
            return None
        affordable = [l for l in options if l.price <= a["coins"]]
        pool = affordable or options
        return min(pool, key=lambda l: abs(l.x - a["x"]) + abs(l.y - a["y"]))

    async def _decide(self, a: dict, clock: Clock) -> None:
        self.pending.add(a["id"])
        try:
            plan_now = self._plan_step(a, clock)
            nearby = [o["name"] for o in self.agents.values()
                      if o["id"] != a["id"] and self._near(a, o, 4)]
            query = f"{clock.label}. I am at {a['area']}. {plan_now}. Who and what matters now?"
            mems = await memory.retrieve(a["id"], query, self.tick)

            data = await llm.chat_json(
                a["deployment"], prompts.DECIDE_SYSTEM,
                prompts.decide_user(a, clock.label, mems, nearby, plan_now),
                temperature=0.95, max_tokens=220,
            )
            action = str(data.get("action", "wander")).lower().strip()
            if action not in prompts.ACTIONS:
                action = "wander"
            thought = str(data.get("thought", "")).strip()[:180]
            loc = world.resolve(data.get("location"))

            # A model may name a location that cannot support the action it
            # chose (working at a fireplace, eating at an anvil). Redirect to
            # somewhere the action actually does something.
            kind = ACTION_KIND.get(action)
            if kind and (not loc or kind not in loc.kinds):
                loc = self._fallback(a, action) or loc
            elif not loc:
                loc = self._fallback(a, action)

            if clock.is_night and action != "sleep" and a["energy"] < 35:
                action, loc = "sleep", world.locations.get(a["home"]) or loc

            a["action"] = action
            a["thought"] = thought
            a["action_until"] = self.tick + ACTION_TICKS.get(action, 6)
            a["cooldown"] = settings.decision_cooldown_ticks
            if loc:
                self._goto(a, loc)

            await memory.remember(
                a["id"], "action",
                f"I decided to {action} at {loc.name if loc else 'nowhere in particular'}."
                + (f" ({thought})" if thought else ""),
                self.tick, clock.label,
            )
            self._chronicle(clock, "action", f"{a['name']} → {action} @ {loc.name if loc else '?'}")
            await self._maybe_reflect(a, clock)
        finally:
            self.pending.discard(a["id"])

    def _fallback(self, a: dict, action: str) -> Location | None:
        kind = {"work": "work", "eat": "eat", "sleep": "sleep", "shop": "trade",
                "socialise": "social", "relax": "fun", "wander": "fun"}.get(action, "fun")
        if action == "work" and a["job"]:
            own = next((l for l in world.locations.values() if l.job == a["job"]), None)
            if own:
                return own
        if action == "sleep":
            return world.locations.get(a["home"])
        options = world.by_kind(kind)
        return random.choice(options) if options else None

    def _plan_step(self, a: dict, clock: Clock) -> str:
        now = clock.hour * 60 + clock.minute
        current = ""
        for step in a["plan"]:
            try:
                h, m = str(step.get("time", "00:00")).split(":")
                when = int(h) * 60 + int(m)
            except ValueError:
                continue
            if when <= now:
                current = f"{step.get('activity', '')} at {step.get('location', '')}"
        return current

    async def _make_plan(self, a: dict, clock: Clock) -> None:
        self.pending.add(a["id"])
        try:
            mems = await memory.retrieve(a["id"], "What is unresolved in my life?", self.tick)
            data = await llm.chat_json(
                settings.reflection_deployment, prompts.PLAN_SYSTEM,
                prompts.plan_user(a, clock.day, mems), temperature=0.85, max_tokens=450,
            )
            steps = [s for s in (data.get("plan") or []) if isinstance(s, dict)][:8]
            if steps:
                a["plan"] = steps
                a["plan_day"] = clock.day
                summary = "; ".join(
                    f"{s.get('time')} {s.get('activity')}" for s in steps[:5]
                )
                await memory.remember(
                    a["id"], "plan", f"My plan for day {clock.day}: {summary}",
                    self.tick, clock.label, 4,
                )
                self._chronicle(clock, "plan", f"{a['name']} planned the day.")
            else:
                a["plan_day"] = clock.day
        finally:
            self.pending.discard(a["id"])

    async def _on_arrival(self, a: dict, clock: Clock) -> None:
        loc = world.resolve(a["target"])
        if not loc:
            return
        others = [o["name"] for o in self.agents.values()
                  if o["id"] != a["id"] and self._near(a, o, 2)]
        note = f"I arrived at {loc.name}."
        if others:
            note += f" {', '.join(others)} {'are' if len(others) > 1 else 'is'} here."
        await memory.remember(a["id"], "observation", note, self.tick, clock.label)

    async def _maybe_reflect(self, a: dict, clock: Clock) -> None:
        row = db.q1("SELECT unreflected FROM agents WHERE id = ?", (a["id"],))
        a["unreflected"] = row["unreflected"] if row else 0
        insights = await memory.maybe_reflect(a, self.tick, clock.label)
        for text in insights:
            self._chronicle(clock, "reflection", f"{a['name']} realised: {text}")

    # ------------------------------------------------------------- dialogue
    def _maybe_converse(self, clock: Clock) -> None:
        if clock.is_night:
            return
        free = [a for a in self.agents.values()
                if not a["busy"] and not a["path"] and a["id"] not in self.pending
                and a["action"] != "sleep"]
        random.shuffle(free)
        for i, a in enumerate(free):
            for b in free[i + 1:]:
                if a["busy"] or b["busy"]:
                    continue
                if not self._near(a, b, 2):
                    continue
                pair = frozenset((a["id"], b["id"]))
                if self.convo_cooldown.get(pair, 0) > self.tick:
                    continue
                urge = 0.25 + (100 - min(a["social"], b["social"])) / 180
                if random.random() > urge:
                    continue
                self.convo_cooldown[pair] = self.tick + 30
                self._spawn(self._converse(a, b, clock))
                break

    async def _converse(self, a: dict, b: dict, clock: Clock) -> None:
        a["busy"] = b["busy"] = True
        a["partner"], b["partner"] = b["id"], a["id"]
        a["action"] = b["action"] = "talk"
        place = a["location"] or a["area"]
        transcript: list[str] = []
        try:
            mems = {
                a["id"]: await memory.retrieve(a["id"], f"{b['name']}", self.tick, 6),
                b["id"]: await memory.retrieve(b["id"], f"{a['name']}", self.tick, 6),
            }
            speaker, listener = a, b
            for turn in range(settings.dialogue_turns):
                rel = self._relationship(speaker["id"], listener["id"])
                line = await llm.chat(
                    speaker["deployment"], prompts.DIALOGUE_SYSTEM,
                    prompts.dialogue_user(
                        speaker, listener["name"], place, clock.label,
                        mems[speaker["id"]], rel, transcript, opener=(turn == 0),
                    ),
                    temperature=1.0, max_tokens=90,
                )
                line = line.strip().strip('"').split("\n")[0][:220]
                if not line:
                    break
                transcript.append(f"{speaker['name']}: {line}")
                speaker["speech"] = line
                await self._emit({
                    "type": "speech", "agent_id": speaker["id"], "name": speaker["name"],
                    "text": line, "location": place,
                })
                self._chronicle(clock, "dialogue", f"{speaker['name']}: {line}")
                speaker, listener = listener, speaker
                await asyncio.sleep(1.2)

            if transcript:
                db.run(
                    "INSERT INTO conversations(tick,label,a_id,b_id,location,lines)"
                    " VALUES(?,?,?,?,?,?)",
                    (self.tick, clock.label, a["id"], b["id"], place, json.dumps(transcript)),
                )
                await asyncio.gather(
                    self._digest(a, b, transcript, clock),
                    self._digest(b, a, transcript, clock),
                )
                for who in (a, b):
                    who["social"] = min(100.0, who["social"] + 30)
                    who["fun"] = min(100.0, who["fun"] + 8)
        finally:
            a["busy"] = b["busy"] = False
            a["partner"] = b["partner"] = None
            a["speech"] = b["speech"] = ""
            a["action"] = b["action"] = "idle"
            a["action_until"] = b["action_until"] = self.tick
            a["cooldown"] = b["cooldown"] = 1

    async def _digest(self, me: dict, other: dict, transcript: list[str], clock: Clock) -> None:
        data = await llm.chat_json(
            me["deployment"], prompts.DIGEST_SYSTEM,
            prompts.digest_user(me, other["name"], transcript),
            temperature=0.7, max_tokens=250,
        )
        takeaway = str(data.get("takeaway") or f"I spoke with {other['name']}.")[:300]
        delta = float(data.get("affinity_delta", 0) or 0)
        delta = max(-20.0, min(20.0, delta))
        impression = str(data.get("impression") or "")[:240]
        importance = float(data.get("importance", 5) or 5)

        await memory.remember(
            me["id"], "dialogue", f"Talking with {other['name']}: {takeaway}",
            self.tick, clock.label, max(1.0, min(10.0, importance)),
        )
        rel = self._relationship(me["id"], other["id"])
        affinity = max(-100.0, min(100.0, rel["affinity"] + delta))
        db.run(
            "INSERT INTO relationships(agent_id,other_id,affinity,summary,encounters)"
            " VALUES(?,?,?,?,1) ON CONFLICT(agent_id,other_id) DO UPDATE SET"
            " affinity = excluded.affinity, summary = excluded.summary,"
            " encounters = relationships.encounters + 1",
            (me["id"], other["id"], affinity, impression or rel["summary"]),
        )
        await self._maybe_reflect(me, clock)

    def _relationship(self, me: str, other: str) -> dict:
        row = db.q1(
            "SELECT affinity, summary, encounters FROM relationships"
            " WHERE agent_id = ? AND other_id = ?", (me, other),
        )
        if row:
            return dict(row)
        return {"affinity": 0.0, "summary": "", "encounters": 0}

    # ------------------------------------------------------- player actions
    async def inject(self, text: str, targets: list[str] | None = None,
                     importance: float = 8.0) -> list[str]:
        clock = Clock(self.tick)
        ids = targets or list(self.agents)
        for aid in ids:
            if aid in self.agents:
                await memory.remember(aid, "event", text, self.tick, clock.label, importance)
                self.agents[aid]["cooldown"] = 0
                self.agents[aid]["action_until"] = self.tick
        self._chronicle(clock, "event", f"[The sky speaks] {text}")
        await self._emit({"type": "event", "text": text, "targets": ids, "label": clock.label})
        return ids

    async def ask(self, agent_id: str, text: str) -> str:
        agent = self.agents.get(agent_id)
        if not agent:
            return ""
        clock = Clock(self.tick)
        mems = await memory.retrieve(agent_id, text, self.tick)
        reply = await llm.chat(
            agent["deployment"],
            prompts.DIALOGUE_SYSTEM + " A voice with no body is speaking to you. React honestly.",
            f"{prompts.identity(agent)}\n\nTime: {clock.label}. You are at {agent['area']}.\n"
            + "What you remember:\n"
            + "\n".join(f"- {m['content']}" for m in mems)
            + f"\n\nThe voice says: {text}\n\nYour reply:",
            temperature=1.0, max_tokens=120,
        )
        reply = reply.strip().strip('"')[:220]
        await memory.remember(
            agent_id, "event",
            f"A voice from nowhere said: \"{text}\". I answered: \"{reply}\"",
            self.tick, clock.label, 9,
        )
        agent["speech"] = reply
        await self._emit({"type": "speech", "agent_id": agent_id, "name": agent["name"],
                          "text": reply, "location": agent["area"]})
        self._chronicle(clock, "dialogue", f"{agent['name']} (to the sky): {reply}")
        return reply

    # -------------------------------------------------------------- plumbing
    def _chronicle(self, clock: Clock, kind: str, text: str) -> None:
        db.run("INSERT INTO chronicle(tick,label,kind,text) VALUES(?,?,?,?)",
               (self.tick, clock.label, kind, text))
        entry = {"tick": self.tick, "label": clock.label, "kind": kind, "text": text}
        self.feed.append(entry)
        self.feed = self.feed[-200:]
        self._spawn(self._emit({"type": "chronicle", **entry}))

    def _persist(self) -> None:
        c = db.conn()
        for a in self.agents.values():
            c.execute(
                "UPDATE agents SET x=?,y=?,coins=?,hunger=?,energy=?,social=?,fun=?,action=?,"
                "thought=?,target=?,plan_json=?,plan_day=? WHERE id=?",
                (a["x"], a["y"], a["coins"], a["hunger"], a["energy"], a["social"], a["fun"],
                 a["action"], a["thought"], a["target"], json.dumps(a["plan"]), a["plan_day"],
                 a["id"]),
            )
        c.commit()

    def snapshot_agent(self, a: dict) -> dict:
        return {
            "id": a["id"], "name": a["name"], "color": a["color"], "sprite": a["sprite"],
            "job": a["job"], "model": a["model_label"], "x": a["x"], "y": a["y"],
            "trail": a["trail"], "action": a["action"], "thought": a["thought"],
            "target": a["target"], "area": a["area"], "location": a["location"],
            "coins": a["coins"], "speech": a["speech"], "partner": a["partner"],
            "needs": {k: round(a[k], 1) for k in ("hunger", "energy", "social", "fun")},
        }

    def snapshot(self) -> dict:
        clock = Clock(self.tick)
        return {
            "type": "tick",
            "tick": self.tick,
            "clock": {"label": clock.label, "day": clock.day, "hour": clock.hour,
                      "minute": clock.minute, "night": clock.is_night},
            "paused": self.paused,
            "agents": [self.snapshot_agent(a) for a in self.agents.values()],
            "usage": llm.usage_report(),
            "live": settings.live,
        }

    async def _emit_tick(self, clock: Clock) -> None:
        await self._emit(self.snapshot())

    async def _emit(self, message: dict) -> None:
        result = self.broadcast(message)
        if asyncio.iscoroutine(result):
            await result

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


sim = Simulation()
