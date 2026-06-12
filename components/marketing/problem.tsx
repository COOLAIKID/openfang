import { CircleDollarSign, MailX, TrendingDown, UserX } from "lucide-react";
import { FadeIn } from "@/components/marketing/fade-in";

const PAINS = [
  {
    icon: UserX,
    title: "Leads you never see",
    stat: "78%",
    statLabel: "of buyers choose first responder",
    body: "78% of buyers choose the vendor that responds first — most teams never find them. Your best-fit prospects are showing buying signals right now, invisibly.",
  },
  {
    icon: TrendingDown,
    title: "A website that leaks revenue",
    stat: "2.3%",
    statLabel: "average B2B conversion rate",
    body: "The average B2B site converts 2.3% — top performers convert 11%. Every day the gap stays open, qualified visitors leave and buy elsewhere.",
  },
  {
    icon: CircleDollarSign,
    title: "Ad spend going nowhere",
    stat: "$0.76",
    statLabel: "of every ad dollar wasted",
    body: "$0.76 of every ad dollar is wasted on poorly-matched audiences. Without knowing exactly who buys and why, you're paying to reach the wrong people.",
  },
  {
    icon: MailX,
    title: "Follow-up that stops too soon",
    stat: "5+",
    statLabel: "touches needed to close",
    body: "80% of deals need 5+ touches; most reps stop at 2. Deals don't die from rejection — they die from silence after the second email.",
  },
];

export function Problem() {
  return (
    <section className="bg-secondary py-20 text-secondary-foreground sm:py-24">
      <div className="mx-auto max-w-7xl px-6">
        <FadeIn className="mx-auto max-w-2xl text-center">
          <p className="text-sm font-semibold uppercase tracking-wider text-accent">
            The real problem
          </p>
          <h2 className="text-balance mt-3 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
            Your growth problem isn&apos;t effort. It&apos;s visibility.
          </h2>
          <p className="text-balance mt-4 text-lg text-white/60">
            You can&apos;t fix the leak you can&apos;t see, call the lead you
            never found, or beat the competitor you&apos;re not watching.
          </p>
        </FadeIn>

        <div className="mt-14 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {PAINS.map((pain, i) => (
            <FadeIn key={pain.title} delay={i * 0.07}>
              <div className="flex h-full flex-col rounded-xl border border-white/10 bg-white/[0.04] p-6">
                <pain.icon className="h-5 w-5 text-accent" />
                <p className="mt-5 font-mono text-3xl font-semibold tabular-nums tracking-tight text-white">
                  {pain.stat}
                </p>
                <p className="mt-1 text-xs font-medium uppercase tracking-wide text-white/50">
                  {pain.statLabel}
                </p>
                <h3 className="mt-4 text-base font-semibold text-white">
                  {pain.title}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-white/60">
                  {pain.body}
                </p>
              </div>
            </FadeIn>
          ))}
        </div>
      </div>
    </section>
  );
}
