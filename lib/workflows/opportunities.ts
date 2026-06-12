import "server-only";

/**
 * Growth Opportunities agent.
 *
 * Strategy-class call with a growth advisor persona producing an
 * OpportunityReport: 8 opportunities across Pricing/Funnel/SEO/Market/
 * Product/Outbound with impact, effort and annual value scaled to the
 * revenue goal. Sorted by impact descending.
 *
 * Falls back to the deterministic demo generator on any failure.
 */

import { completeJson, isAiConfigured } from "@/lib/ai/router";
import { generateOpportunityReport } from "@/lib/demo";
import type {
  BusinessInput,
  GrowthOpportunity,
  OpportunityCategory,
  OpportunityReport,
} from "@/lib/types";
import {
  asArray,
  asRecord,
  clamp,
  num,
  oneOf,
  str,
} from "@/lib/workflows/shared";

export const WORKFLOW_STEPS = [
  { key: "model", label: "Modeling growth levers" },
  { key: "prioritize", label: "Scoring impact vs. effort" },
  { key: "report", label: "Compiling opportunity report" },
];

const CATEGORIES = [
  "Pricing",
  "Funnel",
  "SEO",
  "Market",
  "Product",
  "Outbound",
] as const;
const EFFORTS = ["low", "medium", "high"] as const;

const OPPORTUNITY_SCHEMA_HINT = `{
  "summary": string (3-4 sentences explicitly referencing the revenue goal and the recommended execution order),
  "opportunities": [  // exactly 8, spread across categories (at least 4 distinct categories)
    {
      "id": string,
      "category": "Pricing" | "Funnel" | "SEO" | "Market" | "Product" | "Outbound",
      "title": string (short, specific),
      "description": string (2-3 sentences with concrete reasoning for this business),
      "impact_score": number 0-100,
      "effort": "low" | "medium" | "high",
      "estimated_annual_value": number (USD/year, a credible fraction of the revenue goal)
    }
  ]
}`;

interface RawOpportunities {
  summary?: unknown;
  opportunities?: unknown;
}

export async function runOpportunities(
  business: BusinessInput
): Promise<OpportunityReport> {
  if (!isAiConfigured()) return generateOpportunityReport(business);

  try {
    const raw = await completeJson<RawOpportunities>({
      task: "strategy",
      system:
        "You are a growth advisor who has helped 100+ companies scale revenue. You identify the highest-leverage growth opportunities for a specific business — pricing moves, funnel fixes, SEO plays, market expansion, product wedges, and outbound motions — and you quantify each one honestly against the company's revenue goal. No generic advice: every opportunity must be tailored to this business's industry and target customer.",
      prompt: [
        "Identify the 8 highest-leverage growth opportunities for this business.",
        "",
        "BUSINESS PROFILE:",
        JSON.stringify(
          {
            name: business.name,
            website_url: business.website_url,
            industry: business.industry,
            location: business.location,
            target_customer: business.target_customer,
            annual_revenue_goal_usd: business.revenue_goal,
          },
          null,
          2
        ),
        "",
        `Requirements: exactly 8 opportunities across at least 4 of the 6 categories; impact_score reflects revenue leverage; estimated_annual_value figures must be credible fractions of the $${business.revenue_goal.toLocaleString("en-US")} goal (individually roughly 2-15% of it); the summary must reference the goal and recommend an execution order (low-effort/high-impact first).`,
      ].join("\n"),
      schemaHint: OPPORTUNITY_SCHEMA_HINT,
      maxTokens: 5000,
    });

    return coerceOpportunities(raw, business);
  } catch {
    return generateOpportunityReport(business);
  }
}

function coerceOpportunities(
  raw: RawOpportunities,
  business: BusinessInput
): OpportunityReport {
  const fallback = generateOpportunityReport(business);

  const opportunities: GrowthOpportunity[] = asArray(raw.opportunities)
    .map((item, i): GrowthOpportunity => {
      const o = asRecord(item);
      return {
        id: str(o.id, `opp_${i + 1}`),
        category: oneOf<OpportunityCategory>(o.category, CATEGORIES, "Funnel"),
        title: str(o.title),
        description: str(o.description, "See title."),
        impact_score: Math.round(clamp(o.impact_score, 0, 100, 60)),
        effort: oneOf(o.effort, EFFORTS, "medium"),
        estimated_annual_value: Math.max(
          0,
          Math.round(num(o.estimated_annual_value, business.revenue_goal * 0.05))
        ),
        status: "open",
      };
    })
    .filter((o) => o.title.length > 0);

  if (opportunities.length < 4) return fallback;

  opportunities.sort((a, b) => b.impact_score - a.impact_score);

  return {
    generated_at: new Date().toISOString(),
    summary: str(raw.summary, fallback.summary),
    opportunities,
    total_estimated_annual_value: opportunities.reduce(
      (s, o) => s + o.estimated_annual_value,
      0
    ),
  };
}
