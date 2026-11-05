> **Hollowmere** — a small society of LLMs living in a village, with sprites,
> memories, grudges, jobs, and a currency they will absolutely squabble over.

Six villagers, each backed by **its own Microsoft Foundry deployment**. They
plan their days, walk to places, work, eat, go broke, talk to each other,
form opinions, and reflect on what they've learned. You watch — and occasionally
speak to them as a voice from the sky.

```
┌──────────────────────────────┐  ┌──────────────────┐
│  Phaser 3 village (browser)  │  │ Chronicle        │
│  sprites · tweened movement  │  │ Inspector        │
│  speech bubbles · day/night  │  │ Relations matrix │
└──────────────┬───────────────┘  │ Model usage      │
               │ WebSocket        └──────────────────┘
┌──────────────┴───────────────────────────────────────┐
│ FastAPI tick loop                                    │
│  needs · pathfinding · economy · conversation        │
│  memory stream (SQLite + embeddings) · reflection    │
└──────────────┬───────────────────────────────────────┘
               │  one deployment per villager
        Microsoft Foundry
```

## Quick start

It runs with **zero cloud configuration** in mock mode, so you can see the
village move before you spend a token.

```powershell
# backend
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env        # leave FOUNDRY_* blank for mock mode
uvicorn app.main:app --port 8000

# frontend (second terminal)
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>.

If port 8000 is already taken, run `uvicorn app.main:app --port 8010` and
change the two targets in `frontend/vite.config.ts` to match.

## Wiring up Foundry

Edit `backend/.env`:

```ini
FOUNDRY_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
FOUNDRY_API_KEY=<key>
FOUNDRY_DEFAULT_DEPLOYMENT=gpt-5.4-mini
FOUNDRY_REFLECTION_DEPLOYMENT=gpt-5
```

Then give each villager a different model — this is the whole point:

```ini
FOUNDRY_DEPLOYMENT_SELA=gpt-5.4-nano   # farmer   — says little; small model shines
FOUNDRY_DEPLOYMENT_IVEN=gpt-5.4-mini   # clerk    — procedural, rule-following
FOUNDRY_DEPLOYMENT_MARA=gpt-5.6-luna   # baker    — sharp, keeps score
FOUNDRY_DEPLOYMENT_DOV=gpt-5           # smith    — blunt
FOUNDRY_DEPLOYMENT_ORIN=grok-4.7       # merchant — must out-negotiate everyone
FOUNDRY_DEPLOYMENT_TESS=gpt-6.1-sol    # scribe   — drives the plot
```

**Match model capability to how talkative the persona is.** A small model
given a terse character reads as *in character*; given a scheming one it reads
as broken. Orin and Tess are the manipulator and the interrogator, so they get
the strong tier — the contrast against Sela's nano is the actual experiment.

Anything unset falls back to `FOUNDRY_DEFAULT_DEPLOYMENT`. Classic
`*.openai.azure.com` endpoints work too — just set `FOUNDRY_API_VERSION`.

### Deployments that reject standard parameters

The gpt-5/6 reasoning family rejects `max_tokens` (wants
`max_completion_tokens`) and refuses any `temperature` but the default. Rather
than hard-coding a model list that goes stale, the client **learns each
deployment's shape from its own error messages** and caches it, so the quirk
costs one rejected call ever. The Models tab shows what it learned as badges.

### Embeddings

```ini
FOUNDRY_EMBEDDING_DEPLOYMENT=text-embedding-3-small
```

Leave it blank and memory retrieval uses a free local hashing vector — workable,
but it ranks by word overlap, not meaning.

Two things are worth knowing:

- **Embeddings are not served from a project endpoint.** If `FOUNDRY_ENDPOINT`
  ends in `/api/projects/<project>`, chat completions work there but
  `/embeddings` answers **404 with an empty body**. The client strips the
  project path and calls the resource root automatically.
- Vectors are requested at **256 dimensions** (`text-embedding-3-small` is
  Matryoshka-trained, so the short slice keeps most of the quality) to match
  the width of the local fallback and keep the pure-Python cosine cheap — it
  runs over every stored memory on every decision.

Because both paths are now the same width, a vector's *source* is the only way
to tell them apart, and comparing an API vector to a hash vector yields noise
rather than a low score. Each row is therefore tagged `embed_src`, and when the
sources differ relevance is forced to `0.0` — retrieval degrades to recency and
importance instead of ranking on garbage.

### What it costs

At default speed (2s/tick, 6 villagers) expect **40–60 calls/min**, dominated
by decisions at ~1,200–1,500 input tokens each — roughly **4M input tokens per
hour**.

| Tier | ~Cost per hour of sim |
|---|---|
| gpt-5.4-nano | ~$0.20 |
| gpt-5.4-mini | ~$1 — the sweet spot |
| gpt-5 / gpt-5.6-luna | ~$5 |
| gpt-6.1-sol / grok-4.7 | ~$10+ |

The **Models** tab shows who runs on what plus live call counts, token totals
and **estimated spend per deployment**. Rates live in `backend/prices.json` —
edit it to match your deployment names, or unlisted models get costed at a
default rate and flagged `est`.

**Avoid reasoning models** (DeepSeek-R1, o-series) for villagers: dialogue
wants one snappy line under 30 words, and you pay for thinking tokens nobody
reads.

### Models without JSON mode

Many open-weight serverless deployments reject `response_format`. The client
detects this once per deployment, retries with JSON requested in the prompt,
and lets the salvage parser in `llm.parse_json` handle the result — the Models
tab flags those with `JSON*`.

If a call fails outright the villager falls back to a *scripted* response, which
would otherwise look convincingly alive. That's counted as a **fallback** and
raised as a warning in the Models tab, so you never mistake a broken deployment
for a quiet villager.

## How it works

**The LLM is never asked to navigate.** It picks a *named location* ("Tavern
Bar"); the world resolves that to a tile and BFS-paths the sprite there. This
one constraint is what keeps the sim coherent.

| System | Where | Notes |
|---|---|---|
| Tick loop | `app/simulation.py` | 1 tick = 10 in-world minutes = 2 real seconds |
| Memory stream | `app/memory.py` | Scored on recency × importance × relevance |
| Reflection | `app/memory.py` | Fires once accumulated importance crosses a threshold; writes beliefs back as memories |
| Daily plans | `app/simulation.py` | Generated each in-world morning, steer the day's decisions |
| Conversations | `app/simulation.py` | Triggered by proximity + social need; each side digests the transcript into a memory and an affinity delta |
| Economy | `app/simulation.py` | Wages per job, prices at vendors, coins paid to whoever works that building |
| Needs | `app/simulation.py` | hunger / energy / social / fun, decaying every tick |
| Map | `backend/village.json` | Buildings, zones, props, and interactable locations |

**Cost control.** Villagers only call a model when idle and out of plan; routine
walking, eating and earning are pure local sim. `MAX_CONCURRENT_LLM` caps
parallel calls, `DECISION_COOLDOWN_TICKS` throttles re-planning, and memory
retrieval uses a free local hashing vector unless you set
`FOUNDRY_EMBEDDING_DEPLOYMENT`.

## Playing god

- **Click any villager** → Inspector: needs, coins, today's plan, every memory
  with its importance score, and what they privately think of everyone else.
- **Event** (target "Everyone") injects a high-importance memory into all six
  and clears their cooldowns. Try `A fire breaks out in the Workshop.` or
  `Someone has been stealing from the Bakery overnight.`
- **Speak to one villager** and they answer a voice with no body — and then
  remember that it happened, which tends to leak into later conversations.
- **Relations** tab is a directed affinity matrix. Asymmetry is the interesting
  part: Orin can adore Sela while Sela quietly writes him off.

## Things worth trying

- Give the same persona to two different models and see which one holds character.
- Set `DIALOGUE_TURNS=12` and let an argument actually develop.
- Inject a scarcity event (`The harvest has failed.`) and watch the economy
  decide who starves.
- Point one villager at a reasoning model and the rest at a small one.

## Layout

```
backend/
  village.json         map: buildings, zones, props, locations
  app/
    config.py          env-driven settings
    llm.py             Foundry client + embeddings + mock mode
    world.py           collision grid, BFS pathfinding, location lookup
    db.py              SQLite schema
    memory.py          memory stream, retrieval, reflection
    prompts.py         every prompt, in one file
    seed.py            the six villagers
    simulation.py      tick loop
    main.py            FastAPI + WebSocket
frontend/
  src/scenes/VillageScene.ts   map rendering, sprites, bubbles, day/night
  src/ui/panel.ts              chronicle, inspector, relations, usage
  public/assets/               optional CC0 art (see README there)
```

Inspired by Park et al., *Generative Agents: Interactive Simulacra of Human
Behavior* (2023).
