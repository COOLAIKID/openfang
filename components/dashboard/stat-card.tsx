"use client";

import type { ReactNode } from "react";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

function Sparkline({ points }: { points: number[] }) {
  if (points.length < 2) return null;
  const w = 96;
  const h = 28;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const coords = points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * w;
      const y = h - 2 - ((p - min) / range) * (h - 4);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg width={w} height={h} className="overflow-visible" aria-hidden>
      <polyline
        points={coords}
        fill="none"
        stroke="hsl(var(--primary))"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={0.85}
      />
    </svg>
  );
}

export function StatCard({
  label,
  value,
  delta,
  deltaLabel,
  sparkline,
  icon,
  className,
}: {
  label: string;
  value: string;
  delta?: number; // percent; positive = up
  deltaLabel?: string;
  sparkline?: number[];
  icon?: ReactNode;
  className?: string;
}) {
  const up = (delta ?? 0) >= 0;
  return (
    <Card className={cn("p-5", className)}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[13px] font-medium text-muted-foreground">{label}</p>
          <p className="mt-1.5 text-2xl font-semibold tabular-nums tracking-tight text-foreground">
            {value}
          </p>
          {delta !== undefined ? (
            <p className="mt-1.5 flex items-center gap-1 text-xs">
              <span
                className={cn(
                  "inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 font-medium tabular-nums",
                  up
                    ? "bg-success/10 text-success"
                    : "bg-destructive/10 text-destructive"
                )}
              >
                {up ? (
                  <ArrowUpRight className="h-3 w-3" />
                ) : (
                  <ArrowDownRight className="h-3 w-3" />
                )}
                {Math.abs(delta).toFixed(1)}%
              </span>
              {deltaLabel ? (
                <span className="text-muted-foreground">{deltaLabel}</span>
              ) : null}
            </p>
          ) : null}
        </div>
        <div className="flex flex-col items-end gap-2">
          {icon ? (
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary/10 text-primary [&_svg]:h-4 [&_svg]:w-4">
              {icon}
            </div>
          ) : null}
          {sparkline ? <Sparkline points={sparkline} /> : null}
        </div>
      </div>
    </Card>
  );
}
