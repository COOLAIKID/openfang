"use client";

import * as React from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  ArrowRight,
  BarChart3,
  Crosshair,
  LayoutDashboard,
  Lock,
  Mail,
  Radar,
  Search,
  Settings,
  Star,
  TrendingUp,
  Users,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/** rAF count-up with cubic ease-out. Returns the animated integer value. */
function useCountUp(target: number, duration = 1600, delay = 300) {
  const [value, setValue] = React.useState(0);

  React.useEffect(() => {
    let raf = 0;
    let start: number | null = null;
    const timeout = window.setTimeout(() => {
      const tick = (now: number) => {
        if (start === null) start = now;
        const p = Math.min((now - start) / duration, 1);
        const eased = 1 - Math.pow(1 - p, 3);
        setValue(Math.round(target * eased));
        if (p < 1) raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
    }, delay);
    return () => {
      window.clearTimeout(timeout);
      cancelAnimationFrame(raf);
    };
  }, [target, duration, delay]);

  return value;
}

const SIDEBAR_ITEMS = [
  { icon: LayoutDashboard, label: "Overview", active: true },
  { icon: Search, label: "Audit" },
  { icon: Radar, label: "Competitors" },
  { icon: Crosshair, label: "Leads" },
  { icon: Mail, label: "Outreach" },
  { icon: BarChart3, label: "Analytics" },
  { icon: Settings, label: "Settings" },
];

const ACTIVITY = [
  {
    agent: "Lead Discovery",
    message: "Found 11 new high-fit prospects matching your ICP",
    time: "2m ago",
    color: "bg-primary",
  },
  {
    agent: "Audit",
    message: "Detected a new conversion leak on your pricing page",
    time: "26m ago",
    color: "bg-warning",
  },
  {
    agent: "Outreach",
    message: "3 replies in 'Q2 Mid-Market Push' — 2 positive",
    time: "1h ago",
    color: "bg-success",
  },
  {
    agent: "Competitor Intel",
    message: "Apex Metrics changed pricing — analysis updated",
    time: "3h ago",
    color: "bg-accent",
  },
];

function StatCard({
  label,
  value,
  delta,
}: {
  label: string;
  value: string;
  delta: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-white p-3 text-left">
      <p className="text-[11px] font-medium text-muted-foreground">{label}</p>
      <p className="mt-1 font-mono text-base font-semibold tabular-nums tracking-tight text-foreground sm:text-lg">
        {value}
      </p>
      <p className="mt-0.5 flex items-center gap-1 text-[10px] font-medium text-success">
        <TrendingUp className="h-3 w-3" />
        {delta}
      </p>
    </div>
  );
}

function DashboardPreview() {
  const revenue = useCountUp(214_000);
  const pipeline = useCountUp(486);
  const growthScore = useCountUp(78, 1600, 500);
  const leads = useCountUp(1_240, 1600, 400);

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-surface shadow-elevated">
      {/* Browser chrome */}
      <div className="flex items-center gap-3 border-b border-border bg-white px-4 py-2.5">
        <div className="flex gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-[#FF5F57]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#FEBC2E]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#28C840]" />
        </div>
        <div className="flex flex-1 items-center justify-center">
          <div className="flex items-center gap-1.5 rounded-md bg-muted px-3 py-1 text-[11px] text-muted-foreground">
            <Lock className="h-3 w-3" />
            app.growthos.app/dashboard
          </div>
        </div>
        <div className="w-12" aria-hidden />
      </div>

      <div className="flex">
        {/* Sidebar */}
        <aside className="hidden w-44 shrink-0 border-r border-border bg-white p-3 sm:block">
          <div className="flex items-center gap-2 px-2 pb-3">
            <span className="flex h-5 w-5 items-center justify-center rounded bg-secondary text-[9px] font-bold text-white">
              G
            </span>
            <span className="text-xs font-semibold text-foreground">
              GrowthOS
            </span>
          </div>
          <nav className="space-y-0.5">
            {SIDEBAR_ITEMS.map((item) => (
              <div
                key={item.label}
                className={cn(
                  "flex items-center gap-2 rounded-md px-2 py-1.5 text-[11px] font-medium",
                  item.active
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground"
                )}
              >
                <item.icon className="h-3.5 w-3.5" />
                {item.label}
              </div>
            ))}
          </nav>
        </aside>

        {/* Main panel */}
        <div className="min-w-0 flex-1 p-4 text-left sm:p-5">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-semibold text-foreground">
                Good morning, Brightline
              </p>
              <p className="text-[11px] text-muted-foreground">
                Your AI growth team found 4 new opportunities overnight.
              </p>
            </div>
            <Badge variant="success" className="hidden sm:inline-flex">
              5 agents active
            </Badge>
          </div>

          <div className="mt-4 grid grid-cols-2 gap-2.5 lg:grid-cols-4">
            <StatCard
              label="Revenue opportunity"
              value={`$${revenue.toLocaleString("en-US")}`}
              delta="+12.4% this month"
            />
            <StatCard
              label="Pipeline value"
              value={`$${pipeline}K`}
              delta="+$58K this week"
            />
            <StatCard
              label="Growth score"
              value={`${growthScore}/100`}
              delta="+9 pts in 30 days"
            />
            <StatCard
              label="Leads scored"
              value={leads.toLocaleString("en-US")}
              delta="+186 this week"
            />
          </div>

          <div className="mt-3 grid gap-2.5 lg:grid-cols-5">
            {/* Forecast chart */}
            <div className="rounded-lg border border-border bg-white p-3 lg:col-span-3">
              <div className="flex items-center justify-between">
                <p className="text-[11px] font-medium text-foreground">
                  Revenue forecast
                </p>
                <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
                  <span className="flex items-center gap-1">
                    <span className="h-1.5 w-1.5 rounded-full bg-primary" />
                    With GrowthOS
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="h-1.5 w-1.5 rounded-full bg-border" />
                    Baseline
                  </span>
                </div>
              </div>
              <svg
                viewBox="0 0 300 90"
                className="mt-2 h-24 w-full"
                preserveAspectRatio="none"
                aria-hidden
              >
                <defs>
                  <linearGradient id="heroArea" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#2563EB" stopOpacity="0.18" />
                    <stop offset="100%" stopColor="#2563EB" stopOpacity="0" />
                  </linearGradient>
                </defs>
                <path
                  d="M0,72 C40,70 60,66 100,62 C140,58 180,56 220,52 C250,49 280,47 300,45"
                  fill="none"
                  stroke="hsl(214 32% 85%)"
                  strokeWidth="1.5"
                />
                <path
                  d="M0,70 C40,64 70,56 110,46 C150,36 200,28 245,20 C265,16 285,13 300,10 L300,90 L0,90 Z"
                  fill="url(#heroArea)"
                />
                <path
                  d="M0,70 C40,64 70,56 110,46 C150,36 200,28 245,20 C265,16 285,13 300,10"
                  fill="none"
                  stroke="#2563EB"
                  strokeWidth="2"
                  strokeLinecap="round"
                />
                <circle cx="300" cy="10" r="3" fill="#2563EB" />
              </svg>
              <div className="flex justify-between text-[9px] text-muted-foreground">
                {["Jun", "Jul", "Aug", "Sep", "Oct", "Nov"].map((m) => (
                  <span key={m}>{m}</span>
                ))}
              </div>
            </div>

            {/* Activity feed */}
            <div className="rounded-lg border border-border bg-white p-3 lg:col-span-2">
              <p className="text-[11px] font-medium text-foreground">
                Agent activity
              </p>
              <div className="mt-2 space-y-2">
                {ACTIVITY.map((a) => (
                  <div key={a.message} className="flex items-start gap-2">
                    <span
                      className={cn("mt-1 h-1.5 w-1.5 shrink-0 rounded-full", a.color)}
                    />
                    <div className="min-w-0">
                      <p className="truncate text-[10px] font-medium text-foreground">
                        {a.agent}
                        <span className="ml-1.5 font-normal text-muted-foreground">
                          {a.time}
                        </span>
                      </p>
                      <p className="truncate text-[10px] text-muted-foreground">
                        {a.message}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function Hero() {
  return (
    <section className="relative overflow-hidden">
      <div
        className="bg-grid mask-fade-bottom pointer-events-none absolute inset-0"
        aria-hidden
      />
      <div className="relative mx-auto max-w-7xl px-6 pb-16 pt-20 sm:pb-24 sm:pt-28">
        <div className="mx-auto max-w-3xl text-center">
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease: "easeOut" }}
          >
            <Badge className="border border-primary/15 bg-primary/5 px-3 py-1">
              The Revenue Operating System
            </Badge>
          </motion.div>

          <motion.h1
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease: "easeOut", delay: 0.08 }}
            className="text-balance mt-6 text-4xl font-semibold tracking-tight text-foreground sm:text-6xl"
          >
            Find More Customers. Close More Deals. Grow Faster.
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease: "easeOut", delay: 0.16 }}
            className="text-balance mx-auto mt-5 max-w-2xl text-lg text-muted-foreground"
          >
            AI analyzes your business, competitors, website, and market to
            build a complete growth system in minutes.
          </motion.p>

          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease: "easeOut", delay: 0.24 }}
            className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row"
          >
            <Button size="xl" asChild>
              <Link href="/signup">
                Get My Growth Plan
                <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>
            <Button size="xl" variant="outline" asChild>
              <Link href="/dashboard">See Example Report</Link>
            </Button>
          </motion.div>

          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.5, delay: 0.36 }}
            className="mt-6 flex flex-col items-center justify-center gap-2 text-sm text-muted-foreground sm:flex-row sm:gap-5"
          >
            <p>No credit card required · 14-day free trial · Cancel anytime</p>
            <span className="hidden h-1 w-1 rounded-full bg-border sm:block" />
            <p className="flex items-center gap-1.5">
              <span className="flex items-center gap-0.5 text-warning">
                {Array.from({ length: 5 }).map((_, i) => (
                  <Star key={i} className="h-3.5 w-3.5 fill-current" />
                ))}
              </span>
              <span className="font-medium text-foreground">4.9/5</span>
              from 600+ growth teams
            </p>
          </motion.div>
        </div>

        <motion.div
          initial={{ opacity: 0, y: 32 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, ease: "easeOut", delay: 0.4 }}
          className="mx-auto mt-14 max-w-5xl [perspective:2000px] sm:mt-20"
        >
          <div className="[transform:rotateX(2deg)] transition-transform duration-700 hover:[transform:rotateX(0deg)]">
            <DashboardPreview />
          </div>
        </motion.div>
      </div>
    </section>
  );
}
