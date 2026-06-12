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

## AI providers (17, free-first, tried in priority order)
Groq · Gemini · OpenRouter · Together · Cerebras · DeepSeek · SambaNova ·
Mistral · Fireworks · Perplexity · Cohere · Hugging Face · OpenAI · Anthropic,
then the local floor: **Ollama · LM Studio · llama.cpp**.

Each provider is its own pluggable module in `core/providers/`. Add a key under
`[ai]` in `config.toml` and it joins the cascade automatically; drop a new file in
the package to add a provider. An agent can request a specific model (e.g.
`"groq/llama-3.3-70b-versatile"` or `"ollama/mistral"`) and the cascade fills in
behind it.

## Connectors (15 platforms)
`core/connectors/` integrates the org with the outside world:
- **Publish:** WordPress · Ghost · Dev.to · Hashnode · Medium
- **Social:** Telegram · Reddit · Discord · Slack · Mastodon · X/Twitter
- **Commerce / payouts:** Gumroad · Shopify · Stripe · Lemon Squeezy

Agents use them through the `publish`, `post_social`, and `check_sales` tools.
Unconfigured connectors return a readable "not configured" message instead of
failing, so the org routes around whatever you haven't set up.

## Toolkit (45 agent tools total)
`core/toolkit/` adds research (Wikipedia, RSS, Hacker News, Google Trends,
Reddit), finance (FX, crypto SMA/RSI signals), content analysis (readability,
keyword density, slugs, meta descriptions), and sandboxed file I/O — on top of
the core messaging, self-modification, and skill tools.

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

## Dashboard
A clean, ChatGPT-style web UI at `http://127.0.0.1:4200` with four tabs:

- **Chat** — talk to the organization (or any specific agent). Plain messages get
  a conversational reply grounded in live org state; messages starting with `/`
  run **slash commands** (see below). Per-agent selector, chat history persists.
- **Organization** — the full org chart (Council / Teams / QC) with live status,
  run counts, and a per-agent detail modal (run now, chat with).
- **Skills** — install, run, and remove Claude skills.
- **Activity** — revenue breakdown, live message-bus feed, and activity log.

### Slash commands (work even with no AI key)
```
/help                         list all commands
/agents                       list every agent + run stats
/revenue                      total and per-source revenue
/trigger <agent>              run an agent immediately
/spawn <name> <role> <team> <goal...>   create a new agent
/kill <agent>                 disable an agent
/directive <team> <text...>   send a directive into the org
/skills                       list installed skills
/skill install <source>       install a skill (path | git URL | .zip URL)
/skill run <name> <input...>  run a skill
/skill info|remove <name>     inspect / delete a skill
/clear                        clear chat history
```

## Skills — any Claude skill works
A skill is a folder with a `SKILL.md` (frontmatter `name` + `description`, body =
instructions) — the standard Claude format. Install at runtime from a **local
path, git URL, or .zip URL** via the Skills tab, the `/skill install` command, or
the CTO agent's `install_skill` tool. Agents invoke skills through the
`use_skill` tool, so the org can grow new capabilities on the fly. A starter
`seo-article` skill ships in `skills/`.

## API
- `GET /api/agents` · `GET /api/revenue` · `GET /api/messages` · `GET /api/logs`
- `POST /api/chat` `{message, agent}` · `GET /api/chat/history` · `DELETE /api/chat`
- `GET /api/skills` · `POST /api/skills/install` `{source}` · `POST /api/skills/{name}/run` · `DELETE /api/skills/{name}`
- `POST /api/agents/{name}/trigger` — run an agent now
- `PUT /api/agents/{name}` — edit a definition live · `POST /api/agents` — spawn one

## Layout
- `core/` — AI client, agent loop, message bus, tools, self-modification tools,
  skills engine, chat/slash-commands, scheduler
- `core/providers/` — pluggable AI providers (17, incl. local fallbacks)
- `core/connectors/` — outbound platform integrations (15)
- `core/toolkit/` — research / finance / content / files tool library
- `council/`, `teams/`, `qc/` — agent definition files (live; agents edit these)
- `skills/` — installed Claude skills (13 ship by default)
- `output/` — everything agents produce (articles, code, proposals, signals)
- `dashboard/` — FastAPI app + Alpine.js ChatGPT-style single-page dashboard
- `tests/` — pytest suite (run `pytest`)

## Autonomy
There are **no policy guard rails**. The organization runs indefinitely and
grows, shrinks, or stops **only by council decision** — nothing in the code caps
the agent count or halts the org automatically. The single exception is a
1-minute technical floor on run interval, because the scheduler needs a positive
interval to function. Publishing tools that lack credentials become no-ops that
return a readable error, so agents simply route around whatever you haven't
configured.
