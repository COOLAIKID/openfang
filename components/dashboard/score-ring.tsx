"use client";

import { cn } from "@/lib/utils";

function scoreColor(score: number): string {
  if (score < 50) return "hsl(var(--destructive))";
  if (score < 70) return "hsl(var(--warning))";
  return "hsl(var(--success))";
}

export function ScoreRing({
  score,
  size = 88,
  strokeWidth = 7,
  label,
  className,
}: {
  score: number;
  size?: number;
  strokeWidth?: number;
  label?: string;
  className?: string;
}) {
  const clamped = Math.max(0, Math.min(100, Math.round(score)));
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - clamped / 100);
  const color = scoreColor(clamped);

  return (
    <div className={cn("flex flex-col items-center gap-2", className)}>
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="hsl(var(--muted))"
            strokeWidth={strokeWidth}
          />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            style={{ transition: "stroke-dashoffset 0.8s ease" }}
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span
            className="font-semibold tabular-nums text-foreground"
            style={{ fontSize: size * 0.26 }}
          >
            {clamped}
          </span>
        </div>
      </div>
      {label ? (
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
      ) : null}
    </div>
  );
}
