"use client";

/**
 * AgentRunner — the shared "run an AI agent" affordance.
 *
 * Renders a Run/Refresh button; on click it shows an animated multi-step
 * progress panel (steps tick spinner → check on ~700ms intervals) while the
 * real fetch runs in parallel. Resolves with the fetched JSON, or with the
 * provided demo fallback on ANY fetch error so the UI never breaks.
 */

import { useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Check, Loader2, RefreshCw, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import type { WorkflowKind } from "@/lib/types";
import { cn } from "@/lib/utils";

const DEFAULT_STEPS: Record<WorkflowKind, string[]> = {
  audit: [
    "Crawling site pages",
    "Analyzing UX & messaging",
    "Scoring SEO signals",
    "Detecting conversion leaks",
    "Estimating revenue impact",
    "Compiling report",
  ],
  competitors: [
    "Identifying market players",
    "Crawling competitor sites",
    "Analyzing positioning & pricing",
    "Mapping strengths & weaknesses",
    "Finding positioning gaps",
    "Compiling recommendations",
  ],
  leads: [
    "Refining your ICP",
    "Scanning company databases",
    "Detecting buying signals",
    "Scoring lead fit",
    "Estimating deal values",
    "Building your lead list",
  ],
  outreach: [
    "Researching prospect",
    "Finding personalization hooks",
    "Drafting email sequence",
    "Writing LinkedIn touches",
    "Optimizing send timing",
  ],
  opportunities: [
    "Reviewing audit & market data",
    "Modeling pricing levers",
    "Analyzing funnel performance",
    "Sizing market expansion plays",
    "Ranking by impact vs effort",
  ],
};

const AGENT_NAMES: Record<WorkflowKind, string> = {
  audit: "Audit Agent",
  competitors: "Competitor Intel Agent",
  leads: "Lead Discovery Agent",
  outreach: "Outreach Agent",
  opportunities: "Growth Agent",
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function AgentRunner<T>({
  kind,
  endpoint,
  payload,
  onComplete,
  fallback,
  steps,
  hasRun = false,
  runLabel,
  className,
  disabled = false,
}: {
  kind: WorkflowKind;
  endpoint: string;
  payload: unknown;
  onComplete: (result: T) => void;
  /** Demo generator invoked on any fetch failure. */
  fallback: () => T;
  steps?: string[];
  hasRun?: boolean;
  runLabel?: string;
  className?: string;
  disabled?: boolean;
}) {
  const [running, setRunning] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);
  const runningRef = useRef(false);

  const stepList = steps ?? DEFAULT_STEPS[kind];

  async function run() {
    if (runningRef.current) return;
    runningRef.current = true;
    setRunning(true);
    setStepIndex(0);

    const request: Promise<T> = (async () => {
      try {
        const res = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return (await res.json()) as T;
      } catch {
        return fallback();
      }
    })();

    // Tick the steps regardless of how fast the fetch resolves.
    for (let i = 0; i < stepList.length; i++) {
      await sleep(700);
      setStepIndex(i + 1);
    }

    let result: T;
    try {
      result = await request;
    } catch {
      result = fallback();
    }
    await sleep(300);
    runningRef.current = false;
    setRunning(false);
    onComplete(result);
  }

  const label = running
    ? "Running..."
    : runLabel ?? (hasRun ? "Refresh analysis" : "Run analysis");

  return (
    <div className={cn("space-y-3", className)}>
      <Button onClick={run} disabled={running || disabled} className="gap-2">
        {running ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : hasRun ? (
          <RefreshCw className="h-4 w-4" />
        ) : (
          <Sparkles className="h-4 w-4" />
        )}
        {label}
      </Button>

      <AnimatePresence>
        {running ? (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.25 }}
            className="overflow-hidden"
          >
            <Card className="w-full p-4 sm:w-80">
              <div className="mb-3 flex items-center gap-2">
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-60" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
                </span>
                <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  {AGENT_NAMES[kind]} working
                </p>
              </div>
              <ul className="space-y-2.5">
                {stepList.map((step, i) => {
                  const done = i < stepIndex;
                  const active = i === stepIndex;
                  return (
                    <motion.li
                      key={step}
                      initial={{ opacity: 0, x: -8 }}
                      animate={{ opacity: done || active ? 1 : 0.45, x: 0 }}
                      transition={{ delay: i * 0.06, duration: 0.2 }}
                      className="flex items-center gap-2.5 text-[13px]"
                    >
                      {done ? (
                        <span className="flex h-4 w-4 items-center justify-center rounded-full bg-success/15">
                          <Check className="h-3 w-3 text-success" />
                        </span>
                      ) : active ? (
                        <Loader2 className="h-4 w-4 animate-spin text-primary" />
                      ) : (
                        <span className="h-4 w-4 rounded-full border border-border" />
                      )}
                      <span
                        className={cn(
                          done
                            ? "text-muted-foreground"
                            : active
                              ? "font-medium text-foreground"
                              : "text-muted-foreground"
                        )}
                      >
                        {step}
                      </span>
                    </motion.li>
                  );
                })}
              </ul>
            </Card>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
