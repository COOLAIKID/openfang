import "server-only";

/**
 * Website Audit agent.
 *
 * Pipeline (when OpenRouter is configured):
 *   1. Scrape the homepage for real page signals.
 *   2. Scrape up to 2 promising internal pages (pricing/product/about).
 *   3. Strategy-class LLM call: senior CRO+SEO consultant produces an
 *      AuditReport from the business profile + collected signals.
 *   4. Post-process: ids, clamped scores, computed totals, real page list.
 *
 * Falls back to the deterministic demo generator on any failure.
 */

import { completeJson, isAiConfigured } from "@/lib/ai/router";
import { fetchPageSignals, type PageSignals } from "@/lib/ai/scrape";
import { generateAuditReport } from "@/lib/demo";
import type {
  AuditCategory,
  AuditIssue,
  AuditReport,
  BusinessInput,
  Severity,
} from "@/lib/types";
import {
  asArray,
  asRecord,
  clamp,
  num,
  oneOf,
  str,
  strArray,
} from "@/lib/workflows/shared";

export const WORKFLOW_STEPS = [
  { key: "scrape_home", label: "Analyzing homepage" },
  { key: "scrape_pages", label: "Crawling key pages" },
  { key: "analyze", label: "Running CRO + SEO analysis" },
  { key: "report", label: "Compiling audit report" },
];

const SEVERITIES = ["critical", "high", "medium", "low"] as const;
const CATEGORIES = ["UX", "SEO", "CRO", "Messaging", "Performance"] as const;
const EFFORTS = ["low", "medium", "high"] as const;

const PROMISING_LINK = /(pricing|plans|product|features|solutions|about)/i;

const AUDIT_SCHEMA_HINT = `{
  "summary": string (3-4 sentence executive summary referencing the business by name and its revenue goal),
  "scorecard": { "ux": number 0-100, "seo": number 0-100, "cro": number 0-100, "messaging": number 0-100, "performance": number 0-100 },
  "issues": [  // 8 to 12 items, grounded in the provided page signals
    {
      "id": string,
      "category": "UX" | "SEO" | "CRO" | "Messaging" | "Performance",
      "severity": "critical" | "high" | "medium" | "low",
      "title": string (short, specific),
      "description": string (2-3 sentences citing concrete evidence from the page signals),
      "recommendation": string (specific, actionable fix),
      "estimated_monthly_impact": number (USD/month, scaled sensibly to the revenue goal),
      "effort": "low" | "medium" | "high"
    }
  ],
  "quick_wins": [string, string, string, string]  // exactly 4 concrete same-week actions
}`;

interface RawAudit {
  summary?: unknown;
  scorecard?: unknown;
  issues?: unknown;
  quick_wins?: unknown;
}

export async function runAudit(business: BusinessInput): Promise<AuditReport> {
  if (!isAiConfigured()) return generateAuditReport(business);

  try {
    // Step 1 — homepage signals
    const home = await fetchPageSignals(business.website_url);

    // Step 2 — up to 2 promising internal pages
    const candidates = home.internalLinks
      .filter((l) => PROMISING_LINK.test(l) && l !== home.url)
      .slice(0, 2);
    const settled = await Promise.allSettled(
      candidates.map((l) => fetchPageSignals(l))
    );
    const extraPages: PageSignals[] = settled
      .filter(
        (s): s is PromiseFulfilledResult<PageSignals> => s.status === "fulfilled"
      )
      .map((s) => s.value);
    const pages = [home, ...extraPages];

    // Step 3 — strategy analysis
    const raw = await completeJson<RawAudit>({
      task: "strategy",
      system:
        "You are a senior CRO and SEO consultant who has audited 500+ B2B and e-commerce websites. You diagnose revenue leaks from concrete page evidence, never generic advice. Every issue you raise must be tied to a signal in the data you are given, and every dollar estimate must be plausible for the company's stated revenue goal.",
      prompt: [
        "Audit this business's website and produce a prioritized issue report.",
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
        `COLLECTED PAGE SIGNALS (${pages.length} page(s) actually fetched):`,
        JSON.stringify(
          pages.map((p) => ({ ...p, internalLinks: p.internalLinks.slice(0, 10) })),
          null,
          2
        ),
        "",
        "Requirements: 8-12 issues spanning multiple categories; severities must reflect real revenue risk; estimated_monthly_impact values should sum to a meaningful but credible fraction of the monthly revenue goal; quick_wins are 4 actions doable within a week.",
      ].join("\n"),
      schemaHint: AUDIT_SCHEMA_HINT,
      maxTokens: 6000,
    });

    // Step 4 — post-process into a guaranteed-valid AuditReport
    return coerceAudit(raw, business, pages);
  } catch {
    return generateAuditReport(business);
  }
}

function coerceAudit(
  raw: RawAudit,
  business: BusinessInput,
  pages: PageSignals[]
): AuditReport {
  const fallback = generateAuditReport(business);
  const monthlyGoal = business.revenue_goal / 12;

  const issues: AuditIssue[] = asArray(raw.issues)
    .map((item, i): AuditIssue => {
      const o = asRecord(item);
      return {
        id: str(o.id, `iss_${i + 1}`),
        category: oneOf<AuditCategory>(o.category, CATEGORIES, "CRO"),
        severity: oneOf<Severity>(o.severity, SEVERITIES, "medium"),
        title: str(o.title, "Conversion issue detected"),
        description: str(o.description, "See recommendation."),
        recommendation: str(o.recommendation, "Address this issue."),
        estimated_monthly_impact: Math.max(
          0,
          Math.round(num(o.estimated_monthly_impact, monthlyGoal * 0.01))
        ),
        effort: oneOf(o.effort, EFFORTS, "medium"),
      };
    })
    .filter((i) => i.title.length > 0);

  if (issues.length < 4) return fallback;

  issues.sort((a, b) => b.estimated_monthly_impact - a.estimated_monthly_impact);

  const sc = asRecord(raw.scorecard);
  const ux = clamp(sc.ux, 0, 100, fallback.scorecard.ux);
  const seo = clamp(sc.seo, 0, 100, fallback.scorecard.seo);
  const cro = clamp(sc.cro, 0, 100, fallback.scorecard.cro);
  const messaging = clamp(sc.messaging, 0, 100, fallback.scorecard.messaging);
  const performance = clamp(sc.performance, 0, 100, fallback.scorecard.performance);

  return {
    url: pages[0]?.url ?? business.website_url,
    analyzed_at: new Date().toISOString(),
    scorecard: {
      ux,
      seo,
      cro,
      messaging,
      performance,
      overall: Math.round((ux + seo + cro + messaging + performance) / 5),
    },
    summary: str(raw.summary, fallback.summary),
    issues,
    quick_wins: strArray(raw.quick_wins, fallback.quick_wins).slice(0, 4),
    total_estimated_monthly_impact: issues.reduce(
      (s, i) => s + i.estimated_monthly_impact,
      0
    ),
    pages_analyzed: pages.map((p) => ({
      url: p.url,
      title: p.title || p.url,
    })),
  };
}
