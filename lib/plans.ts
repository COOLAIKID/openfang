/** Pricing plans — single source of truth for the pricing page and billing. */

export interface Plan {
  id: "starter" | "growth" | "pro" | "enterprise";
  name: string;
  monthly: number | null; // null = custom
  annual: number | null; // per month, billed annually
  tagline: string;
  cta: string;
  highlighted?: boolean;
  features: string[];
  limits: {
    audits_per_month: number;
    leads_per_month: number;
    campaigns: number;
    seats: number;
  };
}

export const PLANS: Plan[] = [
  {
    id: "starter",
    name: "Starter",
    monthly: 99,
    annual: 79,
    tagline: "For founders validating their growth engine",
    cta: "Start Free Trial",
    features: [
      "Full AI website audit (monthly)",
      "Competitor tracking (3 competitors)",
      "250 AI-scored leads / month",
      "2 active outreach campaigns",
      "Email sequence generation",
      "Growth opportunity feed",
    ],
    limits: { audits_per_month: 1, leads_per_month: 250, campaigns: 2, seats: 1 },
  },
  {
    id: "growth",
    name: "Growth",
    monthly: 299,
    annual: 239,
    tagline: "For teams building a repeatable pipeline",
    cta: "Start Free Trial",
    highlighted: true,
    features: [
      "Everything in Starter",
      "Weekly AI audits + continuous monitoring",
      "Competitor tracking (10 competitors)",
      "1,500 AI-scored leads / month",
      "10 active campaigns + LinkedIn sequences",
      "Deal probability + revenue forecasting",
      "3 team seats",
    ],
    limits: { audits_per_month: 4, leads_per_month: 1500, campaigns: 10, seats: 3 },
  },
  {
    id: "pro",
    name: "Pro",
    monthly: 699,
    annual: 559,
    tagline: "For revenue teams scaling outbound",
    cta: "Start Free Trial",
    features: [
      "Everything in Growth",
      "Unlimited audits + real-time alerts",
      "Unlimited competitor tracking",
      "5,000 AI-scored leads / month",
      "Unlimited campaigns + A/B sequence testing",
      "Pricing & funnel optimization agent",
      "10 team seats + priority support",
    ],
    limits: { audits_per_month: 999, leads_per_month: 5000, campaigns: 999, seats: 10 },
  },
  {
    id: "enterprise",
    name: "Enterprise",
    monthly: null,
    annual: null,
    tagline: "For organizations that run on pipeline",
    cta: "Talk to Sales",
    features: [
      "Everything in Pro",
      "Custom lead volumes & data sources",
      "SSO / SAML + role-based access",
      "Dedicated growth strategist",
      "Custom AI model routing & fine-tuning",
      "SLA + security review support",
    ],
    limits: { audits_per_month: 9999, leads_per_month: 99999, campaigns: 9999, seats: 999 },
  },
];
