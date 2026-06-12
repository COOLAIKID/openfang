# GrowthOS — The Revenue Operating System

An AI Growth Operating System for businesses. Enter your website, industry, and
revenue goal — GrowthOS audits your site, analyzes competitors, finds and scores
high-value prospects, writes personalized outreach sequences, forecasts revenue,
and continuously surfaces prioritized growth opportunities.

It's a growth agency, sales team, CRO consultant, SEO consultant, and market
research team — for a fraction of the cost.

## Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 15 (App Router), React 19, TypeScript, Tailwind CSS, Framer Motion, shadcn/ui-style components |
| Backend | Next.js API routes (Node runtime) |
| Database & Auth | Supabase (PostgreSQL, RLS, Google OAuth) |
| AI | OpenRouter with model routing — Claude (strategy), Gemini Flash (extraction), DeepSeek (generation) — with automatic fallback chains |
| Infra | Vercel |

## Quick start

```bash
npm install
cp .env.example .env.local   # optional — see below
npm run dev                  # http://localhost:3000
```

**Zero-config demo mode:** with no env vars set, the entire product runs on a
deterministic demo engine — landing page, onboarding, and every dashboard agent
work end-to-end with realistic data. This is also the graceful-degradation path
if a provider is down in production.

**Full mode:**

1. **Supabase** — create a project, run `supabase/migrations/0001_init.sql`
   (SQL editor or `supabase db push`), enable the Google provider under
   Authentication → Providers, and set `NEXT_PUBLIC_SUPABASE_URL` +
   `NEXT_PUBLIC_SUPABASE_ANON_KEY`.
2. **OpenRouter** — set `OPENROUTER_API_KEY`. One key routes to Claude, Gemini,
   and DeepSeek. Model choices are overridable via `OPENROUTER_MODEL_*` vars.

## Deploy to Vercel

```bash
vercel
```

Set the env vars from `.env.example` in the Vercel dashboard. Workflow routes
declare `maxDuration = 120` for long AI runs (Pro plan recommended).

## Project structure

```
app/
  (marketing)/        Landing page (hero, demo, ROI calc, pricing, FAQ, …)
  (auth)/             Login / signup (Supabase + Google OAuth, demo fallback)
  auth/callback/      OAuth code exchange
  onboarding/         4-step business profile wizard
  dashboard/          Product: overview, audit, competitors, leads,
                      outreach, opportunities, analytics, settings
  api/                Workflow + data routes (see docs/ARCHITECTURE.md)
components/
  ui/                 shadcn-style primitives
  marketing/          Landing page sections
  dashboard/          Product UI building blocks
lib/
  ai/                 OpenRouter model router + website signal scraper
  workflows/          Multi-step AI agent pipelines
  supabase/           Browser/server clients (null-safe demo mode)
  demo.ts             Deterministic demo/fallback data engine
  types.ts            Canonical domain types (single source of truth)
supabase/migrations/  PostgreSQL schema with RLS
docs/                 Architecture, design system, build & scaling plans
```

## Verification

```bash
npm run typecheck   # strict TypeScript
npm run build       # production build
```

See `docs/ARCHITECTURE.md` for the full system design and `docs/PLAN.md` for
the MVP → scale roadmap.
