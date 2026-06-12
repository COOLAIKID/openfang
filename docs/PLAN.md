# GrowthOS — MVP Build Plan & Scaling Plan

## MVP build plan (status: shipped in this repo)

**Phase 1 — Foundation** ✅
Design tokens, UI primitive library, canonical domain types, Supabase schema
with RLS, OpenRouter model-routing layer with fallback chains, deterministic
demo/fallback engine, null-safe Supabase clients + auth middleware.

**Phase 2 — Conversion surface** ✅
Landing page (hero with animated product preview, social proof, problem/
solution, interactive product demo on real data, features, ROI calculator,
4-tier pricing, objection-handling FAQ, final CTA), sticky CTA, exit-intent
capture, onboarding wizard with activation theater.

**Phase 3 — Product core** ✅
Dashboard shell + overview (scores, forecast, activity, gamified activation
checklist), five AI agents (Audit, Competitor Intel, Lead Discovery, Outreach,
Growth Opportunities) with animated execution and report UIs, analytics
center, settings.

**Phase 4 — Backend** ✅
Workflow API routes (validated, degradation-safe), multi-step AI pipelines
with real website scraping, best-effort persistence of runs/leads/
opportunities, auth (email + Google OAuth) with demo-mode fallback.

## Launch checklist (operator tasks)

1. Supabase project → run migration → enable Google provider → set envs.
2. OpenRouter key with spend limit; verify `/api/health` shows `ai: true`.
3. Vercel: set envs, Pro plan for 120s workflow functions, custom domain.
4. Wire payments (Stripe Checkout against `lib/plans.ts`; gate via
   `profiles.plan` + plan limits).
5. Analytics (Vercel Analytics / PostHog) + error tracking (Sentry).

## Scaling plan

**0 → 1K users (now):** current architecture as-is. Serverless workflows,
Supabase free→pro tier. Cost ceiling per audit ≈ 1 strategy call.

**1K → 10K users:**
- Move workflow execution to a queue (Supabase queues / Inngest / QStash):
  API returns a `workflow_runs` id immediately; client subscribes via
  Supabase Realtime on the row's `steps`/`status` — the UI already renders
  step-level progress.
- Response caching for audits of identical URLs (hash of signals) and
  competitor reports per industry+region (24h TTL).
- Real data providers behind the existing workflow seams: lead enrichment
  (Apollo/People Data Labs), traffic estimates (Similarweb), SERP data
  (DataForSEO). Each maps onto the already-typed report shapes.
- Email sending via Resend/SendGrid with webhook events → `events` table;
  campaign stats become real.

**10K+ users:**
- Split workflow workers into a dedicated service (same TypeScript modules,
  containerized) with per-tenant rate limits and model-spend budgets in
  `model_usage`.
- Postgres: partition `events`, read replicas for analytics, or stream to
  ClickHouse for the analytics center.
- Model routing v2: dynamic routing on price/latency/quality telemetry per
  task class; A/B prompts with outcome tracking (reply rates close the loop —
  the "continuously updates recommendations" flywheel).
- Multi-seat orgs: introduce `organizations` + membership tables; RLS swaps
  `user_id` for `org_id` scoping (schema kept deliberately org-ready).

## Revenue & growth loops built into the product

- **Activation:** onboarding wizard ends in an "agents working" moment;
  checklist + milestones drive the first five value events.
- **Conversion:** ROI calculator anchors price against modeled gains;
  exit-intent captures pre-signup emails; pricing anchored by Enterprise.
- **Retention:** weekly re-audits and competitor change alerts
  (workflow_runs cadence) create a recurring "what changed" habit.
- **Virality:** shareable audit/example reports (public read-only run pages)
  are the natural next feature — every shared report is a lead magnet.
