import "server-only";

/**
 * Competitor Intelligence agent.
 *
 * Single strategy-class call with a market-analyst persona that produces a
 * full CompetitorReport for the business's industry. Output is coerced into
 * the canonical shape (ids, clamped traffic, sorted by traffic) and falls
 * back to the deterministic demo generator on any failure.
 */

import { completeJson, isAiConfigured } from "@/lib/ai/router";
import { generateCompetitorReport } from "@/lib/demo";
import type { BusinessInput, Competitor, CompetitorReport } from "@/lib/types";
import {
  asArray,
  asRecord,
  num,
  oneOf,
  str,
  strArray,
} from "@/lib/workflows/shared";

export const WORKFLOW_STEPS = [
  { key: "map_market", label: "Mapping the competitive landscape" },
  { key: "analyze", label: "Analyzing positioning and pricing" },
  { key: "report", label: "Compiling competitor report" },
];

const THREATS = ["high", "medium", "low"] as const;
const TRENDS = ["up", "down", "flat"] as const;
const IMPACTS = ["high", "medium", "low"] as const;

const COMPETITOR_SCHEMA_HINT = `{
  "market_summary": string (3-4 sentences on market dynamics and where the gap is for this business),
  "competitors": [  // exactly 5, realistic plausible company names for this industry
    {
      "id": string,
      "name": string,
      "website": string (https URL),
      "positioning": string (one-line positioning statement),
      "estimated_monthly_traffic": number (realistic monthly visits, e.g. 15000-400000),
      "traffic_trend": "up" | "down" | "flat",
      "pricing_strategy": string,
      "key_offers": [string, string],
      "strengths": [string, ...] (2-3),
      "weaknesses": [string, ...] (2-3),
      "threat_level": "high" | "medium" | "low"
    }
  ],
  "positioning_gaps": [string, ...] (4 gaps nobody in the market owns),
  "recommendations": [  // 3-4
    { "title": string, "description": string (2 sentences), "impact": "high" | "medium" | "low" }
  ]
}`;

interface RawCompetitorReport {
  market_summary?: unknown;
  competitors?: unknown;
  positioning_gaps?: unknown;
  recommendations?: unknown;
}

export async function runCompetitors(
  business: BusinessInput
): Promise<CompetitorReport> {
  if (!isAiConfigured()) return generateCompetitorReport(business);

  try {
    const raw = await completeJson<RawCompetitorReport>({
      task: "strategy",
      system:
        "You are a senior market and competitive intelligence analyst. You produce sharp, decision-ready competitor briefs: realistic competitor profiles for the given industry, honest traffic estimates, and positioning gaps the client can actually exploit. Company names must sound like real companies in this space (no placeholders like 'Competitor A').",
      prompt: [
        "Produce a competitive intelligence report for this business.",
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
        "Requirements: exactly 5 competitors with realistic names for the industry, plausible monthly traffic estimates, threat levels consistent with traffic and positioning, 4 positioning gaps, and 3-4 recommendations each tagged with impact.",
      ].join("\n"),
      schemaHint: COMPETITOR_SCHEMA_HINT,
      maxTokens: 5000,
    });

    return coerceCompetitors(raw, business);
  } catch {
    return generateCompetitorReport(business);
  }
}

function coerceCompetitors(
  raw: RawCompetitorReport,
  business: BusinessInput
): CompetitorReport {
  const fallback = generateCompetitorReport(business);

  const competitors: Competitor[] = asArray(raw.competitors)
    .map((item, i): Competitor => {
      const o = asRecord(item);
      const name = str(o.name, `Competitor ${i + 1}`);
      return {
        id: `comp_${i + 1}`,
        name,
        website: str(
          o.website,
          `https://${name.toLowerCase().replace(/[^a-z0-9]/g, "")}.com`
        ),
        positioning: str(o.positioning, "Established player in the category"),
        estimated_monthly_traffic: Math.max(
          1000,
          Math.round(num(o.estimated_monthly_traffic, 25_000))
        ),
        traffic_trend: oneOf(o.traffic_trend, TRENDS, "flat"),
        pricing_strategy: str(o.pricing_strategy, "Undisclosed pricing"),
        key_offers: strArray(o.key_offers, ["Free trial"]).slice(0, 3),
        strengths: strArray(o.strengths, ["Established brand"]).slice(0, 4),
        weaknesses: strArray(o.weaknesses, ["Slow innovation"]).slice(0, 4),
        threat_level: oneOf(o.threat_level, THREATS, "medium"),
      };
    })
    .filter((c) => c.name.length > 0);

  if (competitors.length < 3) return fallback;

  competitors.sort(
    (a, b) => b.estimated_monthly_traffic - a.estimated_monthly_traffic
  );

  const recommendations = asArray(raw.recommendations)
    .map((item) => {
      const o = asRecord(item);
      return {
        title: str(o.title),
        description: str(o.description),
        impact: oneOf(o.impact, IMPACTS, "medium"),
      };
    })
    .filter((r) => r.title.length > 0);

  return {
    analyzed_at: new Date().toISOString(),
    market_summary: str(raw.market_summary, fallback.market_summary),
    competitors,
    positioning_gaps: strArray(raw.positioning_gaps, fallback.positioning_gaps),
    recommendations:
      recommendations.length > 0 ? recommendations : fallback.recommendations,
  };
}
