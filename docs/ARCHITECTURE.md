# GrowthOS — System Architecture

## 1. Application architecture

```
┌────────────────────────────── Vercel ──────────────────────────────┐
│                                                                    │
│  Next.js 15 App Router                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │ (marketing)  │  │  dashboard   │  │  app/api (Node runtime)  │  │
│  │ landing page │  │  product UI  │  │  workflow + data routes  │  │
│  └──────────────┘  └──────┬───────┘  └────────────┬─────────────┘  │
│                           │ fetch                 │                │
│                           └────────────► ┌────────▼─────────┐      │
│                                          │ lib/workflows/*  │      │
│                                          │ multi-step agent │      │
│                                          │ pipelines        │      │
│                                          └──┬──────────┬────┘      │
└─────────────────────────────────────────────┼──────────┼───────────┘
                                              │          │
                            ┌─────────────────▼──┐  ┌────▼──────────────┐
                            │ OpenRouter         │  │ Supabase          │
                            │ model routing:     │  │ Postgres + RLS    │
                            │  Claude   strategy │  │ Auth (Google)     │
                            │  Gemini   extract  │  │ workflow_runs,    │
                            │  DeepSeek generate │  │ leads, campaigns… │
                            └────────────────────┘  └───────────────────┘
```

**Three degradation modes** (every layer is null-safe):

| Mode | Supabase | OpenRouter | Behavior |
|---|---|---|---|
| Full | ✓ | ✓ | Live AI, persisted runs, auth gates dashboard |
| AI-only | ✗ | ✓ | Live AI, localStorage profile, open dashboard |
| Demo | ✗ | ✗ | Deterministic engine (`lib/demo.ts`), fully explorable |

This is both the local-dev story and the production resilience story: an AI
provider outage degrades a workflow to the deterministic engine instead of a
500.

## 2. Database schema

`supabase/migrations/0001_init.sql` — all tables have row-level security
scoped to `auth.uid()`.

- **profiles** — 1:1 with `auth.users` (auto-created by trigger); plan,
  onboarding state, activation milestones.
- **businesses** — the growth-plan subject: website, industry, location,
  revenue goal, target customer.
- **workflow_runs** — one row per agent execution: `kind`
  (audit/competitors/leads/outreach/opportunities), `status`, `steps` (jsonb
  progress), `result` (typed report jsonb), `model_usage`.
- **icps / leads** — generated ICPs and scored prospects (score, reasons,
  deal probability, estimated value, pipeline status).
- **campaigns / campaign_leads** — outreach sequences (jsonb steps) + stats.
- **opportunities** — prioritized growth opportunities with impact/effort/value.
- **events** — append-only product + outreach tracking events.

Reports are stored as validated jsonb matching `lib/types.ts`; relational
tables (leads, opportunities) are additionally materialized for querying,
filtering, and analytics.

## 3. API architecture

All routes validate input with zod and return typed JSON from `lib/types.ts`.

| Route | Method | Purpose |
|---|---|---|
| `/api/workflows/audit` | POST | BusinessInput → `AuditReport` |
| `/api/workflows/competitors` | POST | BusinessInput → `CompetitorReport` |
| `/api/workflows/leads` | POST | BusinessInput → `LeadReport` (ICP + scored leads) |
| `/api/workflows/outreach` | POST | `{business, lead?}` → `{sequence: SequenceStep[]}` |
| `/api/workflows/opportunities` | POST | BusinessInput → `OpportunityReport` |
| `/api/overview` | GET | `OverviewMetrics` for dashboard home |
| `/api/analytics` | GET | `AnalyticsData` for analytics center |
| `/api/business` | GET/POST | Persist/fetch business profile |
| `/api/campaigns` | GET | Campaign list + stats |
| `/api/health` | GET | `{ok, ai, db}` capability probe |

Workflow routes run on the Node runtime with `maxDuration = 120` and persist
results best-effort (`lib/workflows/persist.ts`) — persistence failures never
fail a user-facing request.

## 4. AI workflow architecture

`lib/ai/router.ts` routes each **task class** to the best-fit model via
OpenRouter, with an automatic fallback chain per class:

| Task class | Primary | Fallbacks | Used for |
|---|---|---|---|
| `strategy` | Claude Sonnet | Gemini Pro → DeepSeek | Audits, competitive strategy, opportunity prioritization |
| `extraction` | Gemini Flash | DeepSeek → Claude | ICP building, lead scoring, classification |
| `generation` | DeepSeek | Gemini Flash → Claude | Email/LinkedIn sequence copy at volume |

`completeJson()` enforces strict-JSON outputs with a tolerant parser
(fence/prose stripping) and schema hints; workflow modules coerce and clamp
model output into the canonical types, then fall back to the deterministic
engine on any unrecoverable error.

**Pipelines** (`lib/workflows/`):

- **Audit** — real HTML fetch of the homepage + up to 2 key internal pages
  (`lib/ai/scrape.ts` extracts titles, headings, CTAs, meta, OG/structured
  data, alt coverage, load time) → strategy model produces scored issues with
  per-issue `$ /month` impact scaled to the revenue goal → post-processing
  computes scorecard and totals.
- **Competitors** — strategy model produces market summary, 5 competitor
  profiles (traffic, positioning, offers, strengths/weaknesses, threat),
  positioning gaps, and impact-ranked recommendations.
- **Leads** — extraction model builds the ICP → generation model produces
  ICP-matched prospect list → extraction model scores each lead (0–100 with
  reasons, deal probability, estimated value) → pipeline value computed.
- **Outreach** — generation model writes a 5-step sequence (3 emails + 2
  LinkedIn, day 0/3/5/9/14) personalized from lead fields, value-first,
  <120-word emails, subject lines <55 chars.
- **Opportunities** — strategy model produces 8 impact-scored opportunities
  across Pricing/Funnel/SEO/Market/Product/Outbound with estimated annual
  value tied to the revenue goal.

## 5. Component hierarchy

```
app/layout.tsx (fonts, metadata)
├── (marketing)/layout → Navbar · Footer
│   └── page → Hero(+animated dashboard preview) → LogoCloud → Problem
│              → Solution → ProductDemo(interactive tabs, real demo data)
│              → Features → RoiCalculator → Testimonials → Pricing(PLANS)
│              → Faq → FinalCta · StickyCta · ExitIntentModal
├── (auth)/layout → login · signup        app/auth/callback (OAuth exchange)
├── onboarding (4-step wizard → saveBusiness → animated build → /dashboard)
└── dashboard/layout (sidebar + topbar shell)
    ├── page (overview: stat cards, score rings, forecast chart,
    │         activity feed, gamified activation checklist)
    ├── audit · competitors · leads · outreach · opportunities
    │   (each: report view + AgentRunner → POST /api/workflows/<kind>)
    ├── analytics (recharts: funnel, pipeline, trend, sources, campaigns)
    └── settings (profile, plan, AI status, danger zone)
```

Shared dashboard primitives: `StatCard`, `ScoreRing`, `AgentRunner`
(animated multi-step execution panel), `SeverityBadge`, `PageHeader`,
milestone store (`markMilestone`/`useMilestones`).

## 6. Security

- RLS on every table, scoped to `auth.uid()`; no service-role key in
  request paths.
- Middleware-level auth gating of `/dashboard` when Supabase is configured.
- zod validation on all mutating routes; AI outputs treated as untrusted and
  coerced into typed shapes.
- Audit scraper sends an identified User-Agent and has a hard 15s timeout.
