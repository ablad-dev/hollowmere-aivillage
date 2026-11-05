"""FastAPI app: static world data, agent inspector, god-voice, WebSocket feed."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import db, llm, memory
from .config import settings
from .simulation import Clock, sim
from .world import world

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("village.api")


class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def join(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def leave(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def send(self, message: dict) -> None:
        if not self.clients:
            return
        payload = json.dumps(message)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.leave(ws)


hub = Hub()
sim.broadcast = hub.send

app = FastAPI(title="Hollowmere", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

_loop_task: asyncio.Task | None = None


@app.on_event("startup")
async def _startup() -> None:
    global _loop_task
    sim.load()
    mode = "Foundry" if settings.live else "MOCK (no Foundry endpoint configured)"
    log.info("Hollowmere online - LLM mode: %s", mode)
    _loop_task = asyncio.ensure_future(sim.run())


@app.on_event("shutdown")
async def _shutdown() -> None:
    await sim.stop()
    if _loop_task:
        _loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _loop_task


# ---------------------------------------------------------------- REST
@app.get("/api/world")
async def get_world() -> dict:
    return {
        "world": world.to_dict(),
        "agents": [sim.snapshot_agent(a) for a in sim.agents.values()],
        "clock": sim.snapshot()["clock"],
        "feed": sim.feed[-60:],
        "live": settings.live,
    }


@app.get("/api/agent/{agent_id}")
async def get_agent(agent_id: str) -> dict:
    agent = sim.agents.get(agent_id)
    if not agent:
        raise HTTPException(404, "no such villager")
    rels = db.q(
        "SELECT other_id, affinity, summary, encounters FROM relationships WHERE agent_id = ?",
        (agent_id,),
    )
    names = {a["id"]: a["name"] for a in sim.agents.values()}
    return {
        **sim.snapshot_agent(agent),
        "persona": agent["persona"],
        "deployment": agent["deployment"],
        "plan": agent["plan"],
        "memories": memory.recent(agent_id, 40),
        "relationships": [
            {
                "id": r["other_id"],
                "name": names.get(r["other_id"], r["other_id"]),
                "affinity": round(r["affinity"], 1),
                "summary": r["summary"],
                "encounters": r["encounters"],
            }
            for r in rels
        ],
    }


@app.get("/api/relationships")
async def get_relationships() -> dict:
    names = {a["id"]: a["name"] for a in sim.agents.values()}
    rows = db.q("SELECT agent_id, other_id, affinity, encounters FROM relationships")
    return {
        "nodes": [
            {"id": a["id"], "name": a["name"], "color": a["color"], "model": a["model_label"]}
            for a in sim.agents.values()
        ],
        "edges": [
            {
                "from": r["agent_id"], "to": r["other_id"],
                "fromName": names.get(r["agent_id"]), "toName": names.get(r["other_id"]),
                "affinity": round(r["affinity"], 1), "encounters": r["encounters"],
            }
            for r in rows
        ],
    }


@app.get("/api/chronicle")
async def get_chronicle(limit: int = 120) -> dict:
    rows = db.q("SELECT tick,label,kind,text FROM chronicle ORDER BY id DESC LIMIT ?", (limit,))
    return {"entries": [dict(r) for r in rows][::-1]}


@app.get("/api/usage")
async def get_usage() -> dict:
    report = llm.usage_report()
    per_agent = {a["name"]: a["model_label"] for a in sim.agents.values()}
    return {
        "deployments": report,
        "agents": per_agent,
        "live": settings.live,
        "total_cost": round(sum(d["cost"] for d in report.values()), 4),
        "total_calls": sum(d["calls"] for d in report.values()),
    }


class EventIn(BaseModel):
    text: str
    targets: list[str] | None = None
    importance: float = 8.0


@app.post("/api/event")
async def post_event(body: EventIn) -> dict:
    ids = await sim.inject(body.text, body.targets, body.importance)
    return {"ok": True, "targets": ids}


class AskIn(BaseModel):
    agent_id: str
    text: str


@app.post("/api/ask")
async def post_ask(body: AskIn) -> dict:
    if body.agent_id not in sim.agents:
        raise HTTPException(404, "no such villager")
    return {"reply": await sim.ask(body.agent_id, body.text)}


class ControlIn(BaseModel):
    action: str  # pause | resume | reset | speed
    seconds_per_tick: float | None = None


@app.post("/api/control")
async def post_control(body: ControlIn) -> dict:
    if body.action == "pause":
        sim.paused = True
    elif body.action == "resume":
        sim.paused = False
    elif body.action == "reset":
        sim.reset()
    elif body.action == "speed" and body.seconds_per_tick:
        settings.seconds_per_tick = max(0.25, min(10.0, body.seconds_per_tick))
    else:
        raise HTTPException(400, "unknown control action")
    return {"ok": True, "paused": sim.paused, "seconds_per_tick": settings.seconds_per_tick}


# ---------------------------------------------------------------- WebSocket
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await hub.join(ws)
    try:
        await ws.send_text(json.dumps({
            "type": "init",
            "world": world.to_dict(),
            "agents": [sim.snapshot_agent(a) for a in sim.agents.values()],
            "clock": sim.snapshot()["clock"],
            "feed": sim.feed[-60:],
            "live": settings.live,
        }))
        while True:
            await ws.receive_text()  # client is read-only; REST handles commands
    except WebSocketDisconnect:
        pass
    finally:
        hub.leave(ws)
