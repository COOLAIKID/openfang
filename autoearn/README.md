# AutoEarn — Autonomous AI Organization

A self-running organization of AI agents whose only purpose is to make money,
running 24/7 on free AI providers (with a local Ollama fallback for when every
cloud model is down). Everything lives on your machine.

## How it's organized

```
        Council (5)            CEO · CFO · CMO · CTO · Strategist
   set strategy, reshape org   — meet every 4h, issue directives
            │
            ▼  directives
   Teams (4 × 3 agents)        Content · Dev · Market · Outreach
   execute the work            — researcher→writer→editor, etc.
            │
            ▼  output
   Quality Control (4)         one QC gate per team
   approve → publish           — reject → back to team (max 3, then escalate)
            │
            ▼
   The world                   WordPress · Medium · Telegram · Reddit · files
```

**21 agents** total. Every agent is a JSON file it can rewrite itself — changing
its goal, model, schedule, tools, budget, or even spawning/killing other agents.
Nothing about *what* to do is hardcoded; behavior emerges from each agent's goal,
its tools, and the messages flowing through a shared bus.

## The agent loop
Each agent ticks on its own interval and runs: **observe** (read its messages +
org state) → **reason** (LLM picks one tool action) → **act** (run the tool) →
**reflect** (LLM decides whether to modify itself).

## AI providers (free, tried in order)
1. Groq · 2. Google Gemini · 3. Hugging Face · 4. Mistral · 5. **Ollama (local fallback)**

An agent can request a specific model (e.g. `"groq/llama-3.3-70b-versatile"` or
`"ollama/mistral"`); the cascade fills in behind it.

## Run it

```bash
pip install -r requirements.txt

# Put at least one key in config.toml, or pass it inline:
GROQ_API_KEY=... python main.py
# (no cloud key? install Ollama and it'll use that locally)

# Dashboard + control API:
open http://127.0.0.1:4200
```

Reset the starting org at any time with `python seed_agents.py --force`.

## Dashboard / API
- `GET /` — live org chart, message bus feed, activity log, total revenue
- `GET /api/agents` · `GET /api/revenue` · `GET /api/messages` · `GET /api/logs`
- `POST /api/agents/{name}/trigger` — run an agent now
- `PUT /api/agents/{name}` — edit a definition live · `POST /api/agents` — spawn one

## Layout
- `core/` — AI client, agent loop, message bus, tools, self-modification tools, scheduler
- `council/`, `teams/`, `qc/` — agent definition files (live; agents edit these)
- `output/` — everything agents produce (articles, code, proposals, signals)
- `dashboard/` — FastAPI app + Alpine.js single-page dashboard

## Safety rails
Guard rails protect the host, not the agents' autonomy: a max agent count
(`MAX_AGENTS=60`) and a minimum run interval (`5 min`). Publishing tools that
lack credentials become no-ops that return a readable error, so agents simply
route around whatever you haven't configured.
