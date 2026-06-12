import { Badge } from "@/components/ui/badge";
import type { Severity } from "@/lib/types";
import { cn } from "@/lib/utils";

const STYLES: Record<Severity, string> = {
  critical: "bg-destructive/10 text-destructive",
  high: "bg-warning/15 text-amber-700",
  medium: "bg-primary/10 text-primary",
  low: "bg-muted text-muted-foreground",
};

const LABELS: Record<Severity, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
};

export function SeverityBadge({
  severity,
  className,
}: {
  severity: Severity;
  className?: string;
}) {
  return (
    <Badge
      variant="outline"
      className={cn("border-transparent", STYLES[severity], className)}
    >
      {LABELS[severity]}
    </Badge>
  );
}
