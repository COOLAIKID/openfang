"use client";

import * as React from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  Crosshair,
  Linkedin,
  Mail,
  Radar,
  Search,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  DEMO_BUSINESS,
  generateAuditReport,
  generateCompetitorReport,
  generateLeads,
  generateSequence,
} from "@/lib/demo";
import type { Severity } from "@/lib/types";
import { cn, formatCurrency, formatNumber, initials } from "@/lib/utils";
import { FadeIn } from "@/components/marketing/fade-in";

const TABS = [
  { id: "audit", label: "Audit", icon: Search },
  { id: "competitors", label: "Competitors", icon: Radar },
  { id: "leads", label: "Leads", icon: Crosshair },
  { id: "outreach", label: "Outreach", icon: Mail },
] as const;

type TabId = (typeof TABS)[number]["id"];

const SEVERITY_VARIANT: Record<
  Severity,
  "destructive" | "warning" | "default" | "muted"
> = {
  critical: "destructive",
  high: "warning",
  medium: "default",
  low: "muted",
};

function ScoreRing({ score }: { score: number }) {
  const r = 30;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative h-20 w-20">
      <svg viewBox="0 0 72 72" className="h-20 w-20 -rotate-90">
        <circle
          cx="36"
          cy="36"
          r={r}
          fill="none"
          stroke="hsl(214 32% 91%)"
          strokeWidth="6"
        />
        <circle
          cx="36"
          cy="36"
          r={r}
          fill="none"
          stroke="#2563EB"
          strokeWidth="6"
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - score / 100)}
        />
      </svg>
      <span className="absolute inset-0 flex items-center justify-center font-mono text-xl font-semibold tabular-nums text-foreground">
        {score}
      </span>
    </div>
  );
}

function AuditPanel() {
  const audit = React.useMemo(() => generateAuditReport(DEMO_BUSINESS), []);
  const categories = [
    { label: "UX", score: audit.scorecard.ux },
    { label: "SEO", score: audit.scorecard.seo },
    { label: "CRO", score: audit.scorecard.cro },
    { label: "Messaging", score: audit.scorecard.messaging },
    { label: "Performance", score: audit.scorecard.performance },
  ];

  return (
    <div className="grid gap-4 lg:grid-cols-5">
      <div className="rounded-lg border border-border bg-white p-5 lg:col-span-2">
        <p className="text-sm font-semibold text-foreground">
          Website scorecard
        </p>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">
          {audit.url}
        </p>
        <div className="mt-4 flex items-center gap-5">
          <ScoreRing score={audit.scorecard.overall} />
          <div>
            <p className="text-sm font-medium text-foreground">
              Overall score
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {audit.issues.length} issues found ·{" "}
              <span className="font-medium text-destructive">
                {formatCurrency(audit.total_estimated_monthly_impact, true)}/mo
                at stake
              </span>
            </p>
          </div>
        </div>
        <div className="mt-5 space-y-2.5">
          {categories.map((cat) => (
            <div key={cat.label} className="flex items-center gap-3">
              <span className="w-24 text-xs text-muted-foreground">
                {cat.label}
              </span>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                <div
                  className={cn(
                    "h-full rounded-full",
                    cat.score < 55
                      ? "bg-destructive"
                      : cat.score < 65
                        ? "bg-warning"
                        : "bg-success"
                  )}
                  style={{ width: `${cat.score}%` }}
                />
              </div>
              <span className="w-7 text-right font-mono text-xs tabular-nums text-foreground">
                {cat.score}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-lg border border-border bg-white p-5 lg:col-span-3">
        <div className="flex items-center justify-between">
          <p className="text-sm font-semibold text-foreground">
            Top issues by revenue impact
          </p>
          <Badge variant="muted" className="text-[10px]">
            {audit.pages_analyzed.length} pages analyzed
          </Badge>
        </div>
        <div className="mt-3 space-y-2.5">
          {audit.issues.slice(0, 4).map((issue) => (
            <div
              key={issue.id}
              className="rounded-md border border-border bg-surface px-4 py-3"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge
                      variant={SEVERITY_VARIANT[issue.severity]}
                      className="px-2 py-0 text-[10px] capitalize"
                    >
                      {issue.severity}
                    </Badge>
                    <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                      {issue.category}
                    </span>
                  </div>
                  <p className="mt-1.5 text-sm font-medium text-foreground">
                    {issue.title}
                  </p>
                </div>
                <p className="shrink-0 text-right font-mono text-sm font-semibold tabular-nums text-destructive">
                  {formatCurrency(issue.estimated_monthly_impact, true)}
                  <span className="text-xs font-normal text-muted-foreground">
                    /mo
                  </span>
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function CompetitorsPanel() {
  const report = React.useMemo(
    () => generateCompetitorReport(DEMO_BUSINESS),
    []
  );

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {report.competitors.slice(0, 5).map((comp) => (
        <div
          key={comp.id}
          className="rounded-lg border border-border bg-white p-5"
        >
          <div className="flex items-start justify-between gap-2">
            <div>
              <p className="text-sm font-semibold text-foreground">
                {comp.name}
              </p>
              <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                {comp.positioning}
              </p>
            </div>
            <Badge
              variant={
                comp.threat_level === "high"
                  ? "destructive"
                  : comp.threat_level === "medium"
                    ? "warning"
                    : "muted"
              }
              className="shrink-0 px-2 py-0 text-[10px] capitalize"
            >
              {comp.threat_level} threat
            </Badge>
          </div>
          <div className="mt-4 flex items-center gap-1.5 text-xs text-muted-foreground">
            {comp.traffic_trend === "up" ? (
              <ArrowUp className="h-3.5 w-3.5 text-success" />
            ) : comp.traffic_trend === "down" ? (
              <ArrowDown className="h-3.5 w-3.5 text-destructive" />
            ) : (
              <ArrowRight className="h-3.5 w-3.5" />
            )}
            <span className="font-mono tabular-nums text-foreground">
              {formatNumber(comp.estimated_monthly_traffic, true)}
            </span>
            visits/mo
          </div>
          <div className="mt-3 border-t border-border pt-3">
            <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              Weakness to exploit
            </p>
            <p className="mt-1 text-xs text-foreground">
              {comp.weaknesses[0]}
            </p>
          </div>
        </div>
      ))}
      <div className="flex flex-col justify-center rounded-lg border border-dashed border-primary/30 bg-primary/5 p-5">
        <p className="text-sm font-semibold text-foreground">
          Positioning gap found
        </p>
        <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
          {report.positioning_gaps[0]}
        </p>
        <p className="mt-3 text-xs font-medium text-primary">
          Recommended: {report.recommendations[0].title}
        </p>
      </div>
    </div>
  );
}

function LeadsPanel() {
  const leads = React.useMemo(() => generateLeads(DEMO_BUSINESS, 25), []);
  const pipeline = React.useMemo(
    () =>
      leads.reduce((s, l) => s + l.estimated_deal_value * l.deal_probability, 0),
    [leads]
  );

  return (
    <div className="rounded-lg border border-border bg-white">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-5 py-3">
        <p className="text-sm font-semibold text-foreground">
          AI-scored leads · this week
        </p>
        <p className="text-xs text-muted-foreground">
          Est. pipeline:{" "}
          <span className="font-mono font-semibold tabular-nums text-foreground">
            {formatCurrency(pipeline, true)}
          </span>
        </p>
      </div>
      <div className="divide-y divide-border">
        {leads.slice(0, 6).map((lead) => (
          <div
            key={lead.id}
            className="flex items-center gap-4 px-5 py-3"
          >
            <span className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-[10px] font-semibold text-muted-foreground sm:flex">
              {initials(lead.contact_name)}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-foreground">
                {lead.company}
              </p>
              <p className="truncate text-xs text-muted-foreground">
                {lead.contact_name} · {lead.contact_title}
              </p>
            </div>
            <p className="hidden max-w-[220px] truncate text-xs text-muted-foreground lg:block">
              {lead.score_reasons[0]}
            </p>
            <p className="hidden w-16 text-right font-mono text-xs tabular-nums text-muted-foreground sm:block">
              {Math.round(lead.deal_probability * 100)}%
            </p>
            <span
              className={cn(
                "w-10 shrink-0 rounded-full px-2 py-0.5 text-center font-mono text-xs font-semibold tabular-nums",
                lead.score >= 85
                  ? "bg-success/10 text-success"
                  : lead.score >= 70
                    ? "bg-primary/10 text-primary"
                    : "bg-muted text-muted-foreground"
              )}
            >
              {lead.score}
            </span>
          </div>
        ))}
      </div>
      <div className="border-t border-border px-5 py-2.5 text-center text-xs text-muted-foreground">
        + 19 more leads scored this week
      </div>
    </div>
  );
}

function OutreachPanel() {
  const sequence = React.useMemo(() => generateSequence(DEMO_BUSINESS), []);

  return (
    <div className="grid gap-4 lg:grid-cols-5">
      <div className="rounded-lg border border-border bg-white p-5 lg:col-span-2">
        <p className="text-sm font-semibold text-foreground">
          Sequence: Q2 Mid-Market Push
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          5 steps · email + LinkedIn · personalized per lead
        </p>
        <div className="mt-4 space-y-1.5">
          {sequence.map((step, i) => (
            <div
              key={step.step}
              className={cn(
                "flex items-center gap-3 rounded-md border px-3 py-2.5",
                i === 0
                  ? "border-primary/30 bg-primary/5"
                  : "border-border bg-surface"
              )}
            >
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white text-[10px] font-semibold text-foreground ring-1 ring-border">
                {step.step}
              </span>
              {step.channel === "email" ? (
                <Mail className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              ) : (
                <Linkedin className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              )}
              <p className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
                {step.subject ?? "LinkedIn touch"}
              </p>
              <span className="shrink-0 text-[10px] text-muted-foreground">
                Day {step.delay_days}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-lg border border-border bg-white p-5 lg:col-span-3">
        <div className="flex items-center justify-between">
          <p className="text-sm font-semibold text-foreground">
            Step 1 · Email preview
          </p>
          <Badge variant="success" className="text-[10px]">
            38% reply rate
          </Badge>
        </div>
        <div className="mt-3 rounded-md border border-border bg-surface p-4">
          <p className="text-xs text-muted-foreground">
            Subject:{" "}
            <span className="font-medium text-foreground">
              {sequence[0].subject}
            </span>
          </p>
          <div className="mt-3 whitespace-pre-line border-t border-border pt-3 text-xs leading-relaxed text-foreground">
            {sequence[0].body}
          </div>
        </div>
      </div>
    </div>
  );
}

const PANELS: Record<TabId, React.ComponentType> = {
  audit: AuditPanel,
  competitors: CompetitorsPanel,
  leads: LeadsPanel,
  outreach: OutreachPanel,
};

export function ProductDemo() {
  const [tab, setTab] = React.useState<TabId>("audit");
  const Panel = PANELS[tab];

  return (
    <section id="product" className="scroll-mt-20 bg-surface py-20 sm:py-24">
      <div className="mx-auto max-w-7xl px-6">
        <FadeIn className="mx-auto max-w-2xl text-center">
          <p className="text-sm font-semibold uppercase tracking-wider text-primary">
            See it working
          </p>
          <h2 className="text-balance mt-3 text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
            This is what your AI growth team produces
          </h2>
          <p className="text-balance mt-4 text-lg text-muted-foreground">
            A live look at a real GrowthOS workspace — built for Brightline
            Analytics, a B2B SaaS company in Austin.
          </p>
        </FadeIn>

        <FadeIn delay={0.1} className="mt-12">
          <div className="overflow-hidden rounded-xl border border-border bg-white shadow-elevated">
            <div className="flex items-center justify-between border-b border-border px-4 py-3 sm:px-6">
              <div className="flex gap-1 overflow-x-auto scrollbar-none">
                {TABS.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => setTab(t.id)}
                    className={cn(
                      "flex shrink-0 items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                      tab === t.id
                        ? "bg-secondary text-white"
                        : "text-muted-foreground hover:bg-muted hover:text-foreground"
                    )}
                  >
                    <t.icon className="h-3.5 w-3.5" />
                    {t.label}
                  </button>
                ))}
              </div>
              <Badge variant="muted" className="hidden text-[10px] sm:inline-flex">
                Live demo data
              </Badge>
            </div>

            <div className="bg-surface p-4 sm:p-6">
              <AnimatePresence mode="wait">
                <motion.div
                  key={tab}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -8 }}
                  transition={{ duration: 0.2, ease: "easeOut" }}
                >
                  <Panel />
                </motion.div>
              </AnimatePresence>
            </div>
          </div>
        </FadeIn>
      </div>
    </section>
  );
}
