import {
  Crosshair,
  Mail,
  Radar,
  Search,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { FadeIn } from "@/components/marketing/fade-in";

function AuditVisual() {
  const rows = [
    { label: "CRO", score: 49, color: "bg-destructive" },
    { label: "SEO", score: 58, color: "bg-warning" },
    { label: "Messaging", score: 61, color: "bg-warning" },
    { label: "UX", score: 64, color: "bg-success" },
  ];
  return (
    <div className="space-y-2.5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-foreground">
          Website scorecard
        </span>
        <span className="font-mono text-xs font-semibold text-foreground">
          58/100
        </span>
      </div>
      {rows.map((row) => (
        <div key={row.label} className="flex items-center gap-2.5">
          <span className="w-20 text-[11px] text-muted-foreground">
            {row.label}
          </span>
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
            <div
              className={`h-full rounded-full ${row.color}`}
              style={{ width: `${row.score}%` }}
            />
          </div>
          <span className="w-6 text-right font-mono text-[11px] tabular-nums text-foreground">
            {row.score}
          </span>
        </div>
      ))}
      <p className="pt-1 text-[11px] text-muted-foreground">
        Top issue: CTA below the fold ·{" "}
        <span className="font-medium text-destructive">$11,400/mo impact</span>
      </p>
    </div>
  );
}

function CompetitorVisual() {
  const rows = [
    { name: "Apex Metrics", traffic: "212K/mo", threat: "High", trend: "down" },
    { name: "Funnelwise", traffic: "98K/mo", threat: "Medium", trend: "up" },
    { name: "Convertly", traffic: "44K/mo", threat: "Low", trend: "flat" },
  ];
  return (
    <div className="space-y-2">
      {rows.map((row) => (
        <div
          key={row.name}
          className="flex items-center justify-between rounded-md border border-border bg-white px-3 py-2"
        >
          <div className="flex items-center gap-2">
            {row.trend === "up" ? (
              <TrendingUp className="h-3.5 w-3.5 text-success" />
            ) : (
              <TrendingDown className="h-3.5 w-3.5 text-muted-foreground" />
            )}
            <span className="text-xs font-medium text-foreground">
              {row.name}
            </span>
          </div>
          <div className="flex items-center gap-2.5">
            <span className="font-mono text-[11px] text-muted-foreground">
              {row.traffic}
            </span>
            <Badge
              variant={
                row.threat === "High"
                  ? "destructive"
                  : row.threat === "Medium"
                    ? "warning"
                    : "muted"
              }
              className="px-2 py-0 text-[10px]"
            >
              {row.threat}
            </Badge>
          </div>
        </div>
      ))}
    </div>
  );
}

function ProspectingVisual() {
  const rows = [
    { company: "Brightpeak Goods", contact: "VP of Growth", score: 94 },
    { company: "Atlaswave Commerce", contact: "CMO", score: 89 },
    { company: "Cedarleaf Organics", contact: "Head of Marketing", score: 86 },
  ];
  return (
    <div className="space-y-2">
      {rows.map((row) => (
        <div
          key={row.company}
          className="flex items-center justify-between rounded-md border border-border bg-white px-3 py-2"
        >
          <div>
            <p className="text-xs font-medium text-foreground">{row.company}</p>
            <p className="text-[11px] text-muted-foreground">{row.contact}</p>
          </div>
          <span className="rounded-full bg-success/10 px-2 py-0.5 font-mono text-[11px] font-semibold tabular-nums text-success">
            {row.score}
          </span>
        </div>
      ))}
      <p className="pt-0.5 text-[11px] text-muted-foreground">
        Signal: new growth leader hired in last 60 days
      </p>
    </div>
  );
}

function OutreachVisual() {
  return (
    <div className="rounded-md border border-border bg-white p-3">
      <p className="text-[11px] text-muted-foreground">
        Step 1 of 5 · Email · Day 0
      </p>
      <p className="mt-1.5 text-xs font-medium text-foreground">
        Subject: Brightpeak&apos;s conversion rate — quick observation
      </p>
      <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
        Hi Sarah — I was looking at Brightpeak&apos;s site and noticed your
        primary CTA sits below the fold on mobile. For a brand your size
        that&apos;s usually worth 15–25% of conversions…
      </p>
      <div className="mt-2.5 flex gap-2">
        <Badge variant="success" className="px-2 py-0 text-[10px]">
          38% reply rate
        </Badge>
        <Badge variant="muted" className="px-2 py-0 text-[10px]">
          Personalized per lead
        </Badge>
      </div>
    </div>
  );
}

function OptimizationVisual() {
  const rows = [
    { title: "Fix mobile signup drop-off", impact: 92, value: "$186K/yr" },
    { title: "Introduce annual billing", impact: 88, value: "$158K/yr" },
    { title: "Own comparison keywords", impact: 84, value: "$112K/yr" },
  ];
  return (
    <div className="space-y-2">
      {rows.map((row) => (
        <div
          key={row.title}
          className="flex items-center justify-between rounded-md border border-border bg-white px-3 py-2"
        >
          <div className="flex items-center gap-2.5">
            <span className="font-mono text-[11px] font-semibold tabular-nums text-primary">
              {row.impact}
            </span>
            <span className="text-xs font-medium text-foreground">
              {row.title}
            </span>
          </div>
          <span className="font-mono text-[11px] tabular-nums text-success">
            {row.value}
          </span>
        </div>
      ))}
    </div>
  );
}

const AGENTS = [
  {
    icon: Search,
    name: "AI Audit",
    headline: "Find every dollar your website is leaking",
    body: "A full UX, SEO, CRO, and messaging audit of your site in minutes — every issue scored by revenue impact, with the exact fix attached.",
    visual: <AuditVisual />,
  },
  {
    icon: Radar,
    name: "AI Competitor Intel",
    headline: "Know your competitors' next move before they make it",
    body: "Continuous tracking of competitor traffic, pricing, and positioning — distilled into the gaps you can win and the threats to defuse.",
    visual: <CompetitorVisual />,
  },
  {
    icon: Crosshair,
    name: "AI Prospecting",
    headline: "Wake up to a list of buyers ready to hear from you",
    body: "AI finds companies that match your best customers, scores them on real buying signals, and explains why each one is worth your time.",
    visual: <ProspectingVisual />,
  },
  {
    icon: Mail,
    name: "AI Outreach",
    headline: "Outreach that reads like you wrote it yourself",
    body: "Multi-step email and LinkedIn sequences, personalized from each lead's actual website and signals — never a template blast.",
    visual: <OutreachVisual />,
  },
  {
    icon: TrendingUp,
    name: "AI Optimization",
    headline: "A growth roadmap that re-prioritizes itself weekly",
    body: "Pricing, funnel, SEO, and market opportunities ranked by impact and effort — so your team always works on the highest-leverage thing.",
    visual: <OptimizationVisual />,
  },
];

export function Solution() {
  return (
    <section className="py-20 sm:py-24">
      <div className="mx-auto max-w-7xl px-6">
        <FadeIn className="mx-auto max-w-2xl text-center">
          <p className="text-sm font-semibold uppercase tracking-wider text-primary">
            The solution
          </p>
          <h2 className="text-balance mt-3 text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
            One platform. Five AI growth agents.
          </h2>
          <p className="text-balance mt-4 text-lg text-muted-foreground">
            GrowthOS replaces a growth agency, a sales team, a CRO consultant,
            an SEO consultant, and a market research team — for a fraction of
            the cost.
          </p>
        </FadeIn>

        <div className="mt-16 space-y-6">
          {AGENTS.map((agent, i) => (
            <FadeIn key={agent.name} delay={Math.min(i * 0.05, 0.15)}>
              <div className="grid items-center gap-8 rounded-xl border border-border bg-white p-6 shadow-card sm:p-8 lg:grid-cols-2 lg:gap-12">
                <div className={i % 2 === 1 ? "lg:order-2" : undefined}>
                  <div className="flex items-center gap-3">
                    <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10">
                      <agent.icon className="h-5 w-5 text-primary" />
                    </span>
                    <span className="text-sm font-semibold text-primary">
                      {agent.name}
                    </span>
                  </div>
                  <h3 className="text-balance mt-4 text-xl font-semibold tracking-tight text-foreground sm:text-2xl">
                    {agent.headline}
                  </h3>
                  <p className="mt-3 leading-relaxed text-muted-foreground">
                    {agent.body}
                  </p>
                </div>
                <div
                  className={
                    i % 2 === 1
                      ? "rounded-lg border border-border bg-surface p-4 lg:order-1"
                      : "rounded-lg border border-border bg-surface p-4"
                  }
                >
                  {agent.visual}
                </div>
              </div>
            </FadeIn>
          ))}
        </div>
      </div>
    </section>
  );
}
