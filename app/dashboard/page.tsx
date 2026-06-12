"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ArrowRight, Check, DollarSign, TrendingUp, Users } from "lucide-react";
import { PageHeader } from "@/components/dashboard/page-header";
import { ScoreRing } from "@/components/dashboard/score-ring";
import { PageSkeleton } from "@/components/dashboard/skeletons";
import { StatCard } from "@/components/dashboard/stat-card";
import {
  useMilestones,
  type MilestoneKey,
} from "@/components/dashboard/milestones";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { businessOrDemo, hasOnboarded } from "@/lib/business-store";
import { generateOverview } from "@/lib/demo";
import type { BusinessInput, OverviewMetrics } from "@/lib/types";
import { cn, formatCurrency, formatNumber, timeAgo } from "@/lib/utils";

const CHECKLIST: { key: MilestoneKey; label: string; href: string }[] = [
  { key: "business", label: "Connect your business", href: "/onboarding" },
  { key: "audit", label: "Run your first website audit", href: "/dashboard/audit" },
  {
    key: "competitors",
    label: "Review competitor analysis",
    href: "/dashboard/competitors",
  },
  { key: "leads", label: "Generate your first lead list", href: "/dashboard/leads" },
  {
    key: "outreach",
    label: "Launch an outreach campaign",
    href: "/dashboard/outreach",
  },
];

function GettingStarted() {
  const milestones = useMilestones();
  const [onboarded, setOnboarded] = useState(false);
  useEffect(() => setOnboarded(hasOnboarded()), []);

  const isDone = (key: MilestoneKey) =>
    key === "business" ? onboarded || milestones.includes(key) : milestones.includes(key);

  const doneCount = CHECKLIST.filter((c) => isDone(c.key)).length;
  const pct = Math.round((doneCount / CHECKLIST.length) * 100);

  return (
    <Card>
      <CardHeader className="pb-4">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">Getting started</CardTitle>
          <span className="text-xs font-medium tabular-nums text-muted-foreground">
            {doneCount}/{CHECKLIST.length} complete
          </span>
        </div>
        <Progress value={pct} className="mt-2 h-1.5" />
      </CardHeader>
      <CardContent className="space-y-1">
        {CHECKLIST.map((item) => {
          const done = isDone(item.key);
          return (
            <Link
              key={item.key}
              href={item.href}
              className={cn(
                "group flex items-center gap-3 rounded-md px-2 py-2 transition-colors hover:bg-muted",
                done && "opacity-70"
              )}
            >
              <span
                className={cn(
                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-full border",
                  done
                    ? "border-success bg-success text-success-foreground"
                    : "border-border bg-background"
                )}
              >
                {done ? <Check className="h-3 w-3" /> : null}
              </span>
              <span
                className={cn(
                  "flex-1 text-[13px] font-medium",
                  done ? "text-muted-foreground line-through" : "text-foreground"
                )}
              >
                {item.label}
              </span>
              {!done ? (
                <ArrowRight className="h-3.5 w-3.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
              ) : null}
            </Link>
          );
        })}
      </CardContent>
    </Card>
  );
}

const AGENT_BADGE: Record<string, "default" | "accent" | "success" | "warning"> = {
  "Lead Discovery": "default",
  Audit: "warning",
  Outreach: "accent",
  "Competitor Intel": "default",
  Growth: "success",
};

export default function OverviewPage() {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const business: BusinessInput | null = useMemo(
    () => (mounted ? businessOrDemo() : null),
    [mounted]
  );
  const data: OverviewMetrics | null = useMemo(
    () => (business ? generateOverview(business) : null),
    [business]
  );

  if (!mounted || !business || !data) return <PageSkeleton />;

  return (
    <div className="animate-fade-up space-y-6">
      <PageHeader
        title="Overview"
        description={`Your growth command center for ${business.name}.`}
      />

      {/* Stat cards + score rings */}
      <div className="grid gap-4 md:grid-cols-3">
        <StatCard
          label="Revenue Opportunity"
          value={formatCurrency(data.revenue_opportunity, true)}
          delta={12.4}
          deltaLabel="vs last month"
          icon={<DollarSign />}
          sparkline={data.forecast.map((f) => f.with_growthos)}
        />
        <StatCard
          label="Pipeline Value"
          value={formatCurrency(data.pipeline_value, true)}
          delta={8.1}
          deltaLabel="vs last month"
          icon={<TrendingUp />}
          sparkline={data.forecast.map((f) => f.baseline)}
        />
        <StatCard
          label="New Leads This Week"
          value={formatNumber(data.new_leads_this_week)}
          delta={data.new_leads_this_week > 6 ? 22.0 : -4.2}
          deltaLabel="vs last week"
          icon={<Users />}
        />
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-center justify-around gap-6 p-6">
          <ScoreRing score={data.conversion_score} label="Conversion Score" />
          <ScoreRing score={data.competitor_score} label="Competitor Score" />
          <ScoreRing score={data.growth_score} label="Growth Score" />
        </CardContent>
      </Card>

      {/* Forecast */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Revenue forecast</CardTitle>
          <CardDescription>
            Projected monthly revenue over the next 6 months — baseline vs with
            GrowthOS recommendations applied.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data.forecast} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                <defs>
                  <linearGradient id="fillGrowthos" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#2563EB" stopOpacity={0.25} />
                    <stop offset="100%" stopColor="#2563EB" stopOpacity={0.02} />
                  </linearGradient>
                  <linearGradient id="fillBaseline" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#94A3B8" stopOpacity={0.2} />
                    <stop offset="100%" stopColor="#94A3B8" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(214 32% 91%)" vertical={false} />
                <XAxis
                  dataKey="month"
                  tickLine={false}
                  axisLine={false}
                  tick={{ fontSize: 12, fill: "#64748B" }}
                />
                <YAxis
                  tickLine={false}
                  axisLine={false}
                  tick={{ fontSize: 12, fill: "#64748B" }}
                  tickFormatter={(v: number) => formatCurrency(v, true)}
                  width={64}
                />
                <Tooltip
                  formatter={(value) => formatCurrency(Number(value))}
                  contentStyle={{
                    borderRadius: 10,
                    border: "1px solid hsl(214 32% 91%)",
                    fontSize: 13,
                    boxShadow: "0 8px 24px -4px rgb(15 23 42 / 0.10)",
                  }}
                />
                <Legend
                  formatter={(value: string) =>
                    value === "with_growthos" ? "With GrowthOS" : "Baseline"
                  }
                  wrapperStyle={{ fontSize: 13 }}
                />
                <Area
                  type="monotone"
                  dataKey="baseline"
                  stroke="#94A3B8"
                  strokeWidth={2}
                  fill="url(#fillBaseline)"
                  name="baseline"
                />
                <Area
                  type="monotone"
                  dataKey="with_growthos"
                  stroke="#2563EB"
                  strokeWidth={2}
                  fill="url(#fillGrowthos)"
                  name="with_growthos"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      {/* Activity + checklist */}
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-4">
            <CardTitle className="text-base">Agent activity</CardTitle>
            <CardDescription>Latest signals from your AI growth team.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-1">
            {data.activity.map((item) => (
              <div
                key={item.id}
                className="flex items-start gap-3 rounded-md px-2 py-2.5 transition-colors hover:bg-muted"
              >
                <Badge
                  variant={AGENT_BADGE[item.agent] ?? "default"}
                  className="mt-0.5 shrink-0 text-[10px]"
                >
                  {item.agent}
                </Badge>
                <p className="flex-1 text-[13px] leading-snug text-foreground">
                  {item.message}
                </p>
                <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                  {timeAgo(item.at)}
                </span>
              </div>
            ))}
          </CardContent>
        </Card>

        <GettingStarted />
      </div>
    </div>
  );
}
