/**
 * GrowthOS demo data engine.
 *
 * Generates deterministic, realistic data seeded from the business profile so
 * the entire product is fully explorable before connecting Supabase or
 * OpenRouter, and so AI workflows degrade gracefully if a provider is down.
 * The same generators power the interactive landing-page demo.
 */

import { hashString, seededRandom } from "@/lib/utils";
import type {
  AnalyticsData,
  AuditIssue,
  AuditReport,
  BusinessInput,
  Campaign,
  Competitor,
  CompetitorReport,
  GrowthOpportunity,
  ICP,
  Lead,
  LeadReport,
  LeadStatus,
  OpportunityReport,
  OverviewMetrics,
  SequenceStep,
} from "@/lib/types";

export const DEMO_BUSINESS: BusinessInput = {
  name: "Brightline Analytics",
  website_url: "https://brightline-analytics.com",
  industry: "B2B SaaS — Marketing Analytics",
  location: "Austin, TX",
  revenue_goal: 2_400_000,
  target_customer: "Mid-market e-commerce brands ($5M–$50M revenue)",
};

function rng(seedKey: string) {
  return seededRandom(hashString(seedKey));
}

function pick<T>(r: () => number, arr: T[]): T {
  return arr[Math.floor(r() * arr.length)];
}

function int(r: () => number, min: number, max: number): number {
  return Math.floor(r() * (max - min + 1)) + min;
}

const iso = (daysAgo: number) =>
  new Date(Date.now() - daysAgo * 86_400_000).toISOString();

// ---------------------------------------------------------------- audit ----

const AUDIT_ISSUES: Omit<AuditIssue, "id" | "estimated_monthly_impact">[] = [
  {
    category: "CRO",
    severity: "critical",
    title: "Primary CTA below the fold on mobile",
    description:
      "62% of traffic is mobile, but the primary call-to-action only appears after 2.5 screen-heights of scrolling. Visitors decide in under 5 seconds.",
    recommendation:
      "Move a single high-contrast CTA into the hero and add a sticky mobile CTA bar. A/B test 'Get My Growth Plan' vs. current copy.",
    effort: "low",
  },
  {
    category: "Messaging",
    severity: "high",
    title: "Headline describes features, not outcomes",
    description:
      "The hero headline leads with product capabilities instead of the revenue outcome buyers are searching for. Bounce rate on the homepage is likely elevated because visitors can't map the product to their problem.",
    recommendation:
      "Rewrite the headline around the #1 customer outcome. Formula: {End result} + {timeframe} + {objection handled}.",
    effort: "low",
  },
  {
    category: "CRO",
    severity: "high",
    title: "No social proof above the fold",
    description:
      "First-time visitors see zero trust signals before being asked to act. B2B buyers need 3–5 trust touches before converting.",
    recommendation:
      "Add a customer logo strip directly under the hero CTA and one outcome-focused testimonial with a real name, title, and metric.",
    effort: "low",
  },
  {
    category: "SEO",
    severity: "high",
    title: "Missing meta descriptions on key commercial pages",
    description:
      "Pricing and solution pages have no meta descriptions, so search engines auto-generate snippets that don't sell. CTR from search is left to chance on the highest-intent pages.",
    recommendation:
      "Write benefit-led meta descriptions (140–155 chars) for every commercial page, each ending in an implicit CTA.",
    effort: "low",
  },
  {
    category: "SEO",
    severity: "medium",
    title: "Thin content on high-intent comparison keywords",
    description:
      "Competitors rank for '{category} alternatives' and 'best {category} tools' queries with dedicated comparison pages. These keywords convert 3–5x better than informational terms.",
    recommendation:
      "Publish 4 comparison pages targeting bottom-of-funnel queries where competitors currently own the SERP.",
    effort: "medium",
  },
  {
    category: "UX",
    severity: "medium",
    title: "Navigation offers 9 choices, diluting the path to conversion",
    description:
      "Hick's Law: every additional nav item reduces the probability a visitor takes the action you want. Top-performing SaaS sites use 4–5 items plus one CTA.",
    recommendation:
      "Collapse nav to Product, Pricing, Customers, Resources + one primary CTA button.",
    effort: "low",
  },
  {
    category: "CRO",
    severity: "medium",
    title: "Pricing page hides plans behind 'Contact Sales'",
    description:
      "73% of B2B buyers want to see pricing before talking to sales. Hiding all plans drives high-intent visitors to competitors that publish prices.",
    recommendation:
      "Publish at least entry and mid-tier pricing. Keep 'Contact Sales' only for enterprise.",
    effort: "medium",
  },
  {
    category: "Performance",
    severity: "medium",
    title: "Largest Contentful Paint over 3.5s on mobile",
    description:
      "Every additional second of load time reduces conversions by ~7%. The hero image is unoptimized and render-blocking scripts delay first paint.",
    recommendation:
      "Serve hero media as compressed WebP/AVIF with priority hints; defer non-critical third-party scripts.",
    effort: "medium",
  },
  {
    category: "Messaging",
    severity: "medium",
    title: "No risk-reversal anywhere in the funnel",
    description:
      "There's no trial, guarantee, or 'cancel anytime' language. Risk-averse buyers have no safe next step, so they leave to 'think about it' and never return.",
    recommendation:
      "Add explicit risk-reversal at every CTA: free trial, money-back window, or no-commitment pilot.",
    effort: "low",
  },
  {
    category: "UX",
    severity: "low",
    title: "Forms ask for 7 fields before delivering any value",
    description:
      "Each form field above 4 costs roughly 10% of completions. Phone number and company size can be enriched automatically instead of asked upfront.",
    recommendation:
      "Cut signup to email + name. Enrich firmographics in the background; ask for the rest after first value delivery.",
    effort: "low",
  },
  {
    category: "SEO",
    severity: "low",
    title: "Images missing alt text across the site",
    description:
      "41 images have no alt attributes — lost relevance signals and an accessibility gap that can affect enterprise procurement reviews.",
    recommendation:
      "Add descriptive, keyword-aware alt text to all non-decorative images.",
    effort: "low",
  },
  {
    category: "Performance",
    severity: "low",
    title: "No caching headers on static assets",
    description:
      "Repeat visitors re-download every asset. Returning traffic — your warmest audience — gets the slowest experience.",
    recommendation:
      "Set immutable cache-control headers on hashed static assets via your CDN.",
    effort: "low",
  },
];

export function generateAuditReport(business: BusinessInput): AuditReport {
  const r = rng(`audit:${business.website_url}`);
  const issueCount = int(r, 8, AUDIT_ISSUES.length);
  const shuffled = [...AUDIT_ISSUES].sort(() => r() - 0.5).slice(0, issueCount);

  const monthlyGoal = business.revenue_goal / 12;
  const issues: AuditIssue[] = shuffled
    .map((issue, i) => {
      const weight =
        issue.severity === "critical"
          ? 0.06
          : issue.severity === "high"
            ? 0.035
            : issue.severity === "medium"
              ? 0.018
              : 0.007;
      return {
        ...issue,
        id: `iss_${i + 1}`,
        estimated_monthly_impact: Math.round(
          (monthlyGoal * weight * (0.7 + r() * 0.6)) / 50
        ) * 50,
      };
    })
    .sort((a, b) => b.estimated_monthly_impact - a.estimated_monthly_impact);

  const score = (base: number) => Math.min(94, Math.max(31, base + int(r, -8, 8)));
  const scorecard = {
    ux: score(64),
    seo: score(58),
    cro: score(49),
    messaging: score(61),
    performance: score(67),
    overall: 0,
  };
  scorecard.overall = Math.round(
    (scorecard.ux + scorecard.seo + scorecard.cro + scorecard.messaging + scorecard.performance) / 5
  );

  return {
    url: business.website_url,
    analyzed_at: new Date().toISOString(),
    scorecard,
    summary: `${business.name} has a solid foundation but is leaking revenue at three points: weak above-the-fold conversion architecture, missing bottom-of-funnel SEO coverage, and outcome-free messaging. Fixing the top ${Math.min(
      4,
      issues.length
    )} issues below is the fastest path toward the ${(
      business.revenue_goal / 1_000_000
    ).toFixed(1)}M revenue goal — most are low-effort changes with compounding returns.`,
    issues,
    quick_wins: [
      "Move the primary CTA above the fold and make it outcome-led",
      "Add a logo strip + one metric-driven testimonial under the hero",
      "Write meta descriptions for pricing and solution pages",
      "Add risk-reversal copy ('free 14-day trial, no card') beside every CTA",
    ],
    total_estimated_monthly_impact: issues.reduce(
      (s, i) => s + i.estimated_monthly_impact,
      0
    ),
    pages_analyzed: [
      { url: business.website_url, title: "Homepage" },
      { url: `${business.website_url}/pricing`, title: "Pricing" },
      { url: `${business.website_url}/product`, title: "Product" },
      { url: `${business.website_url}/about`, title: "About" },
      { url: `${business.website_url}/blog`, title: "Blog" },
    ],
  };
}

// ---------------------------------------------------------- competitors ----

const COMPETITOR_SEEDS = [
  {
    name: "Apex Metrics",
    positioning: "Enterprise-grade analytics for data teams",
    pricing_strategy: "High-anchor enterprise pricing, annual contracts only",
    strengths: ["Strong enterprise brand", "Deep integration catalog"],
    weaknesses: ["6-week implementation", "No self-serve tier", "Slow support"],
  },
  {
    name: "Funnelwise",
    positioning: "The easiest funnel analytics for marketers",
    pricing_strategy: "Freemium with aggressive in-product upsells",
    strengths: ["Fast onboarding", "Big content engine"],
    weaknesses: ["Shallow reporting", "Churn complaints on G2", "No API"],
  },
  {
    name: "Convertly",
    positioning: "CRO platform with built-in A/B testing",
    pricing_strategy: "Mid-market per-seat pricing",
    strengths: ["Strong CRO feature set", "Loyal agency channel"],
    weaknesses: ["Dated UI", "Weak attribution", "No AI roadmap shipped"],
  },
  {
    name: "SignalPath",
    positioning: "Revenue attribution for B2B pipelines",
    pricing_strategy: "Usage-based, expensive at scale",
    strengths: ["Accurate attribution model", "Strong RevOps community"],
    weaknesses: ["Steep learning curve", "Pricing unpredictability"],
  },
  {
    name: "GrowthPulse",
    positioning: "All-in-one growth dashboard for SMBs",
    pricing_strategy: "Low-cost monthly plans",
    strengths: ["Cheap entry point", "Simple UX"],
    weaknesses: ["Not credible upmarket", "Limited data depth", "No SSO"],
  },
];

export function generateCompetitorReport(business: BusinessInput): CompetitorReport {
  const r = rng(`competitors:${business.name}`);
  const competitors: Competitor[] = COMPETITOR_SEEDS.map((seed, i) => {
    const traffic = int(r, 18, 220) * 1000;
    return {
      id: `comp_${i + 1}`,
      name: seed.name,
      website: `https://${seed.name.toLowerCase().replace(/\s+/g, "")}.com`,
      positioning: seed.positioning,
      estimated_monthly_traffic: traffic,
      traffic_trend: pick(r, ["up", "down", "flat"] as const),
      pricing_strategy: seed.pricing_strategy,
      key_offers: [
        pick(r, ["Free trial", "Free audit", "Freemium tier", "Pilot program"]),
        pick(r, ["Annual discount 20%", "Onboarding included", "Migration service"]),
      ],
      strengths: seed.strengths,
      weaknesses: seed.weaknesses,
      threat_level: traffic > 120_000 ? "high" : traffic > 60_000 ? "medium" : "low",
    };
  }).sort((a, b) => b.estimated_monthly_traffic - a.estimated_monthly_traffic);

  return {
    analyzed_at: new Date().toISOString(),
    market_summary: `The ${business.industry} market around ${business.location} is consolidating into two camps: heavyweight enterprise suites with slow implementations, and lightweight tools that can't go upmarket. Nobody owns the middle — fast time-to-value with credible depth. That's the positioning gap ${business.name} should claim, and it should be stated explicitly in the hero.`,
    competitors,
    positioning_gaps: [
      "No competitor promises a concrete time-to-value ('insights in 7 days')",
      "Nobody publishes transparent mid-market pricing — a trust wedge",
      "Zero competitors lead with ROI guarantees or performance-based pilots",
      "AI-assisted recommendations are on roadmaps but unshipped across the field",
    ],
    recommendations: [
      {
        title: "Claim the speed-to-value position",
        description:
          "Every enterprise competitor takes 4–6 weeks to implement. Lead all messaging with 'live in days, not quarters' and back it with an onboarding SLA.",
        impact: "high",
      },
      {
        title: "Publish transparent pricing",
        description:
          "Be the only credible player with public pricing. This wins the 73% of buyers who shortlist only vendors that show prices.",
        impact: "high",
      },
      {
        title: "Target competitor churn with comparison pages",
        description:
          "Funnelwise and Convertly both show churn complaints in public reviews. Ship '{Competitor} alternative' pages addressing their top 3 weaknesses.",
        impact: "medium",
      },
      {
        title: "Ship one visible AI capability before Q3",
        description:
          "All five competitors have AI on their roadmap but nothing live. First-mover proof (even one workflow) becomes the demo moment that closes deals.",
        impact: "medium",
      },
    ],
  };
}

// ----------------------------------------------------------------- leads ----

const FIRST = ["Sarah", "Marcus", "Elena", "David", "Priya", "James", "Rachel", "Tom", "Aisha", "Daniel", "Maya", "Chris", "Laura", "Kevin", "Nina", "Alex", "Jordan", "Sam", "Dana", "Omar"];
const LAST = ["Chen", "Rodriguez", "Kim", "Patel", "Novak", "Johnson", "Okafor", "Larsen", "Garcia", "Thompson", "Ali", "Becker", "Sato", "Murphy", "Haddad", "Klein", "Ng", "Rivera", "Walsh", "Ferraro"];
const TITLES = ["VP of Growth", "Head of Marketing", "CEO", "CMO", "Director of Demand Gen", "VP of Sales", "Founder", "Head of E-commerce", "COO", "VP Marketing"];
const COMPANY_A = ["North", "Blue", "Ever", "Bright", "Swift", "Clear", "Prime", "Bold", "True", "Peak", "Iron", "Silver", "Atlas", "Nova", "Summit", "Cedar", "Harbor", "Vista", "Crown", "Pioneer"];
const COMPANY_B = ["peak Goods", "wave Commerce", "green Supply", "stone Brands", "line Retail", "field Outfitters", "rock Labs", "leaf Organics", "bay Trading", "ridge Co", "gate Direct", "port Collective", "crest Home", "view Apparel", "spring Wellness", "haven Living", "forge Gear", "bloom Beauty", "stream Goods", "light Nutrition"];
const SIZES = ["11–50", "51–200", "201–500", "501–1000"];
const CITIES = ["Austin, TX", "Denver, CO", "Chicago, IL", "Atlanta, GA", "Seattle, WA", "Boston, MA", "Nashville, TN", "San Diego, CA", "Portland, OR", "Miami, FL"];
const STATUSES: LeadStatus[] = ["new", "new", "new", "contacted", "contacted", "replied", "qualified", "meeting", "won", "lost"];

export function generateICP(business: BusinessInput): ICP {
  return {
    id: "icp_1",
    name: "Primary ICP — High-intent mid-market",
    industry: business.target_customer || business.industry,
    company_size: "50–500 employees",
    region: `${business.location} + remote-first North America`,
    pain_points: [
      "Can't attribute revenue to marketing spend",
      "Conversion rates flat despite rising traffic",
      "Sales and marketing operate on different numbers",
      "Manual reporting eats 10+ hours/week",
    ],
    buying_triggers: [
      "New VP of Growth or CMO in the last 90 days",
      "Recently raised Series A/B funding",
      "Hiring for demand gen or RevOps roles",
      "Switched or churned from a competitor tool",
    ],
    decision_makers: ["VP of Growth", "CMO", "Head of Marketing", "CEO (under 100 employees)"],
  };
}

export function generateLeads(business: BusinessInput, count = 25): Lead[] {
  const r = rng(`leads:${business.name}:${business.target_customer}`);
  const leads: Lead[] = [];
  const used = new Set<string>();

  for (let i = 0; i < count; i++) {
    let company = "";
    do {
      company = `${pick(r, COMPANY_A)}${pick(r, COMPANY_B)}`;
    } while (used.has(company));
    used.add(company);

    const first = pick(r, FIRST);
    const last = pick(r, LAST);
    const score = int(r, 42, 98);
    const dealValue = int(r, 8, 90) * 1000;
    const domain = company.toLowerCase().replace(/[^a-z]/g, "");

    const reasons: string[] = [];
    if (score > 85) reasons.push("Strong ICP fit: industry + size match");
    if (r() > 0.5) reasons.push("New growth leader hired in last 60 days");
    if (r() > 0.55) reasons.push("Active hiring for demand gen roles");
    if (r() > 0.6) reasons.push("Recently raised funding round");
    if (r() > 0.65) reasons.push("Tech stack signals tool-switching intent");
    if (reasons.length === 0) reasons.push("Partial ICP fit: size match, adjacent industry");

    leads.push({
      id: `lead_${i + 1}`,
      company,
      website: `https://${domain}.com`,
      industry: pick(r, ["E-commerce", "DTC Retail", "Consumer Goods", "Marketplace", "Subscription Commerce"]),
      company_size: pick(r, SIZES),
      location: pick(r, CITIES),
      contact_name: `${first} ${last}`,
      contact_title: pick(r, TITLES),
      contact_email: `${first.toLowerCase()}.${last.toLowerCase()}@${domain}.com`,
      linkedin_url: `https://linkedin.com/in/${first.toLowerCase()}-${last.toLowerCase()}`,
      score,
      score_reasons: reasons.slice(0, 3),
      deal_probability: Math.round((score / 100) * (0.35 + r() * 0.3) * 100) / 100,
      estimated_deal_value: dealValue,
      status: pick(r, STATUSES),
      created_at: iso(int(r, 0, 21)),
    });
  }
  return leads.sort((a, b) => b.score - a.score);
}

export function generateLeadReport(business: BusinessInput): LeadReport {
  const leads = generateLeads(business);
  return {
    generated_at: new Date().toISOString(),
    icp: generateICP(business),
    leads,
    total_pipeline_value: leads.reduce(
      (s, l) => s + l.estimated_deal_value * l.deal_probability,
      0
    ),
  };
}

// -------------------------------------------------------------- outreach ----

export function generateSequence(
  business: BusinessInput,
  lead?: Lead
): SequenceStep[] {
  const firstName = lead?.contact_name.split(" ")[0] ?? "{{first_name}}";
  const company = lead?.company ?? "{{company}}";
  const yourName = "{{your_name}}";

  return [
    {
      step: 1,
      channel: "email",
      delay_days: 0,
      subject: `${company}'s conversion rate — quick observation`,
      body: `Hi ${firstName},\n\nI was looking at ${company}'s site and noticed your primary CTA sits below the fold on mobile — for a brand your size that's usually worth 15–25% of conversions.\n\nWe help ${business.target_customer.toLowerCase()} find and fix exactly these leaks. ${DEMO_BUSINESS.name === business.name ? "Lumen Goods" : "One client"} recovered $38K/mo from three changes in their first month.\n\nWorth a 15-minute look at what we found on your site? I'll send the full audit either way.\n\n${yourName}`,
    },
    {
      step: 2,
      channel: "email",
      delay_days: 3,
      subject: `Re: ${company}'s conversion rate`,
      body: `Hi ${firstName},\n\nFollowing up with something concrete — I ran our audit on ${company} and three things stood out:\n\n1. Mobile CTA placement (est. impact: high)\n2. No social proof above the fold\n3. Two competitors outranking you on your highest-intent keywords\n\nHappy to walk you through the full report. Does Thursday or Friday work?\n\n${yourName}`,
    },
    {
      step: 3,
      channel: "linkedin",
      delay_days: 5,
      body: `Hi ${firstName} — sent you a note about a few conversion leaks we spotted on ${company}'s site. No pitch; the audit's yours either way. Open to connecting?`,
    },
    {
      step: 4,
      channel: "email",
      delay_days: 9,
      subject: `Last one — the ${company} audit`,
      body: `Hi ${firstName},\n\nI'll close the loop here. The audit found roughly $20–40K/mo in recoverable revenue across CRO and SEO fixes — most of them low-effort.\n\nIf growth is a priority this quarter, the report is a 15-minute read: grab time here → {{calendar_link}}\n\nIf not, no worries at all — I'll leave you be.\n\n${yourName}`,
    },
    {
      step: 5,
      channel: "linkedin",
      delay_days: 14,
      body: `${firstName} — saw ${company}'s latest launch, nice work. If conversion ever moves up the priority list, that audit offer stands. Good luck this quarter either way!`,
    },
  ];
}

export function generateCampaigns(business: BusinessInput): Campaign[] {
  const r = rng(`campaigns:${business.name}`);
  const defs = [
    { name: "Q2 Mid-Market E-commerce Push", status: "active" as const, leads: 120 },
    { name: "New VP-of-Growth Trigger Campaign", status: "active" as const, leads: 64 },
    { name: "Competitor Churn — Funnelwise", status: "paused" as const, leads: 38 },
    { name: "Series A Fundraise Trigger", status: "completed" as const, leads: 85 },
  ];
  return defs.map((d, i) => {
    const sent = Math.round(d.leads * (d.status === "completed" ? 1 : 0.4 + r() * 0.5));
    const opened = Math.round(sent * (0.45 + r() * 0.25));
    const replied = Math.round(opened * (0.12 + r() * 0.15));
    const meetings = Math.round(replied * (0.25 + r() * 0.25));
    return {
      id: `camp_${i + 1}`,
      name: d.name,
      lead_count: d.leads,
      status: d.status,
      sequence: generateSequence(business),
      stats: {
        sent,
        opened,
        replied,
        meetings,
        open_rate: sent ? Math.round((opened / sent) * 1000) / 10 : 0,
        reply_rate: sent ? Math.round((replied / sent) * 1000) / 10 : 0,
        meeting_rate: sent ? Math.round((meetings / sent) * 1000) / 10 : 0,
      },
      created_at: iso(int(r, 5, 60)),
    };
  });
}

// --------------------------------------------------------- opportunities ----

const OPPORTUNITY_SEEDS: Omit<GrowthOpportunity, "id" | "estimated_annual_value" | "status">[] = [
  {
    category: "Pricing",
    title: "Introduce annual billing with 20% incentive",
    description:
      "No annual option exists today. Annual plans typically lift LTV 25–40% and slash involuntary churn. Competitors anchor annual-first.",
    impact_score: 88,
    effort: "low",
  },
  {
    category: "Funnel",
    title: "Fix mobile checkout/signup drop-off",
    description:
      "Mobile converts at less than half of desktop. The signup flow loses users at the form step — cutting fields and adding Google one-tap closes most of the gap.",
    impact_score: 92,
    effort: "medium",
  },
  {
    category: "SEO",
    title: "Own bottom-of-funnel comparison keywords",
    description:
      "4 comparison pages targeting '{competitor} alternative' queries where rivals currently rank. These convert 3–5x better than blog traffic.",
    impact_score: 84,
    effort: "medium",
  },
  {
    category: "Pricing",
    title: "Add a premium tier with white-glove onboarding",
    description:
      "Top 10% of customers show willingness-to-pay far above the current ceiling. A premium tier with onboarding + SLA captures it without touching existing plans.",
    impact_score: 76,
    effort: "medium",
  },
  {
    category: "Market",
    title: "Expand into adjacent vertical with same ICP shape",
    description:
      "Subscription-commerce brands share the exact pain profile of the current ICP and have no incumbent solution. Same product, new landing page + 50-lead test.",
    impact_score: 71,
    effort: "high",
  },
  {
    category: "Outbound",
    title: "Activate hiring-signal trigger campaigns",
    description:
      "Companies hiring growth/demand-gen roles are 4x more likely to buy within 90 days. Automate a always-on sequence for this trigger.",
    impact_score: 81,
    effort: "low",
  },
  {
    category: "Funnel",
    title: "Add exit-intent offer on pricing page",
    description:
      "Pricing-page abandoners are the highest-intent lost traffic. An exit-intent ROI report or extended-trial offer typically recovers 8–12% of them.",
    impact_score: 68,
    effort: "low",
  },
  {
    category: "Product",
    title: "Launch a free interactive audit as top-of-funnel magnet",
    description:
      "A self-serve mini-audit captures emails from buyers 6–12 months before purchase intent and feeds the nurture pipeline with product-qualified leads.",
    impact_score: 79,
    effort: "high",
  },
];

export function generateOpportunityReport(business: BusinessInput): OpportunityReport {
  const r = rng(`opps:${business.name}`);
  const opportunities: GrowthOpportunity[] = OPPORTUNITY_SEEDS.map((seed, i) => ({
    ...seed,
    id: `opp_${i + 1}`,
    estimated_annual_value:
      Math.round((business.revenue_goal * (seed.impact_score / 100) * (0.04 + r() * 0.08)) / 1000) * 1000,
    status: "open",
  })).sort((a, b) => b.impact_score - a.impact_score);

  return {
    generated_at: new Date().toISOString(),
    summary: `Eight prioritized opportunities identified across pricing, funnel, SEO, and outbound. The top three alone cover an estimated ${Math.round(
      (opportunities.slice(0, 3).reduce((s, o) => s + o.estimated_annual_value, 0) /
        business.revenue_goal) *
        100
    )}% of the gap to the ${(business.revenue_goal / 1_000_000).toFixed(1)}M goal. Recommended order: low-effort/high-impact first (annual billing, trigger campaigns, exit-intent), then the mobile funnel fix.`,
    opportunities,
    total_estimated_annual_value: opportunities.reduce(
      (s, o) => s + o.estimated_annual_value,
      0
    ),
  };
}

// ------------------------------------------------------------- overview ----

export function generateOverview(business: BusinessInput): OverviewMetrics {
  const r = rng(`overview:${business.name}`);
  const audit = generateAuditReport(business);
  const opps = generateOpportunityReport(business);
  const leads = generateLeads(business);

  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const start = new Date().getMonth();
  const monthlyBase = business.revenue_goal / 18;
  const forecast = Array.from({ length: 6 }, (_, i) => {
    const growth = 1 + i * 0.022;
    const boosted = 1 + i * 0.085;
    return {
      month: months[(start + i) % 12],
      baseline: Math.round(monthlyBase * growth),
      with_growthos: Math.round(monthlyBase * boosted),
    };
  });

  return {
    revenue_opportunity: audit.total_estimated_monthly_impact * 12 +
      Math.round(opps.total_estimated_annual_value * 0.35),
    new_leads_this_week: leads.filter((l) => Date.now() - new Date(l.created_at).getTime() < 7 * 86_400_000).length,
    pipeline_value: Math.round(
      leads.reduce((s, l) => s + l.estimated_deal_value * l.deal_probability, 0)
    ),
    conversion_score: audit.scorecard.cro,
    competitor_score: int(r, 58, 76),
    growth_score: Math.round((audit.scorecard.overall + int(r, 60, 80)) / 2),
    forecast,
    activity: [
      { id: "a1", agent: "Lead Discovery", message: `Found ${int(r, 6, 14)} new high-fit prospects matching your ICP`, at: iso(0.1) },
      { id: "a2", agent: "Audit", message: "Detected a new conversion leak on your pricing page", at: iso(0.4) },
      { id: "a3", agent: "Outreach", message: "3 replies received in 'Q2 Mid-Market Push' — 2 positive", at: iso(0.8) },
      { id: "a4", agent: "Competitor Intel", message: "Apex Metrics changed pricing — analysis updated", at: iso(1.2) },
      { id: "a5", agent: "Growth", message: "New opportunity: annual billing could add $180K ARR", at: iso(2) },
      { id: "a6", agent: "Lead Discovery", message: "Lead score updated: Brightpeak Goods moved to 94 (hiring signal)", at: iso(2.5) },
    ],
  };
}

// ------------------------------------------------------------ analytics ----

export function generateAnalytics(business: BusinessInput): AnalyticsData {
  const r = rng(`analytics:${business.name}`);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"];
  return {
    funnel: [
      { stage: "Prospects", count: 1240 },
      { stage: "Contacted", count: 860 },
      { stage: "Opened", count: 540 },
      { stage: "Replied", count: 132 },
      { stage: "Meetings", count: 48 },
      { stage: "Won", count: 14 },
    ],
    pipeline_by_month: months.map((month, i) => ({
      month,
      pipeline: int(r, 120, 200) * 1000 + i * 28_000,
      closed: int(r, 30, 60) * 1000 + i * 9_000,
    })),
    campaign_performance: generateCampaigns(business).map((c) => ({
      campaign: c.name,
      sent: c.stats.sent,
      open_rate: c.stats.open_rate,
      reply_rate: c.stats.reply_rate,
      meetings: c.stats.meetings,
    })),
    lead_sources: [
      { source: "AI Discovery", count: 540 },
      { source: "Hiring triggers", count: 280 },
      { source: "Funding triggers", count: 190 },
      { source: "Competitor churn", count: 140 },
      { source: "Inbound", count: 90 },
    ],
    conversion_rate_trend: Array.from({ length: 8 }, (_, i) => ({
      week: `W${i + 1}`,
      rate: Math.round((2.1 + i * 0.18 + r() * 0.3) * 10) / 10,
    })),
  };
}
