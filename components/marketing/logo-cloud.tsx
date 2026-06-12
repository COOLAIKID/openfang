import { cn } from "@/lib/utils";

const COMPANIES: { name: string; className: string }[] = [
  { name: "Brightline", className: "font-semibold tracking-tight" },
  { name: "LUMEN GOODS", className: "font-medium tracking-[0.18em] text-sm" },
  { name: "Northwind", className: "font-semibold italic tracking-tight" },
  { name: "Atlas Labs", className: "font-mono font-medium tracking-tight" },
  { name: "CRESTVIEW", className: "font-light tracking-[0.22em] text-sm" },
  { name: "Halcyon", className: "font-semibold tracking-wide" },
  { name: "VANTAGE", className: "font-bold tracking-[0.12em] text-sm" },
  { name: "Meridian", className: "font-medium tracking-tight" },
];

const RESULTS = [
  { value: "+$4.2M", label: "pipeline generated" },
  { value: "38%", label: "avg. reply-rate lift" },
  { value: "12,400+", label: "leads scored weekly" },
  { value: "9 days", label: "avg. time to first meeting" },
];

function MarqueeRow({ ariaHidden = false }: { ariaHidden?: boolean }) {
  return (
    <div
      className="flex shrink-0 items-center gap-16 pr-16"
      aria-hidden={ariaHidden}
    >
      {COMPANIES.map((company) => (
        <span
          key={company.name}
          className={cn(
            "whitespace-nowrap text-lg text-muted-foreground/70",
            company.className
          )}
        >
          {company.name}
        </span>
      ))}
    </div>
  );
}

export function LogoCloud() {
  return (
    <section className="border-y border-border bg-surface py-14">
      <div className="mx-auto max-w-7xl px-6">
        <p className="text-center text-sm font-medium text-muted-foreground">
          Trusted by revenue teams at
        </p>

        <div className="relative mt-7 overflow-hidden [mask-image:linear-gradient(to_right,transparent,black_12%,black_88%,transparent)]">
          <div className="flex w-max animate-marquee">
            <MarqueeRow />
            <MarqueeRow ariaHidden />
          </div>
        </div>

        <div className="mx-auto mt-12 grid max-w-4xl grid-cols-2 gap-x-6 gap-y-8 lg:grid-cols-4">
          {RESULTS.map((stat) => (
            <div key={stat.label} className="text-center">
              <p className="font-mono text-2xl font-semibold tabular-nums tracking-tight text-foreground">
                {stat.value}
              </p>
              <p className="mt-1 text-sm text-muted-foreground">{stat.label}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
