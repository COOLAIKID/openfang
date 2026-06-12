import "server-only";

/**
 * Lead Discovery agent.
 *
 * Pipeline (when OpenRouter is configured):
 *   1. Extraction call → Ideal Customer Profile from the business profile.
 *   2. Generation call → 20-25 plausible prospect companies + contacts
 *      matching the ICP, scored in the same call (merged scoring pass for
 *      reliability: score 0-100, reasons, deal probability, deal value).
 *   3. Post-process: ids, clamps, sort by score, compute pipeline value.
 *
 * Falls back to the deterministic demo generator on any failure.
 */

import { completeJson, isAiConfigured } from "@/lib/ai/router";
import { generateICP, generateLeadReport } from "@/lib/demo";
import type { BusinessInput, ICP, Lead, LeadReport } from "@/lib/types";
import {
  asArray,
  asRecord,
  clamp,
  num,
  str,
  strArray,
} from "@/lib/workflows/shared";

export const WORKFLOW_STEPS = [
  { key: "icp", label: "Building your ideal customer profile" },
  { key: "discover", label: "Discovering matching prospects" },
  { key: "score", label: "Scoring and qualifying leads" },
  { key: "report", label: "Building the pipeline report" },
];

const ICP_SCHEMA_HINT = `{
  "name": string (short label, e.g. "Primary ICP — Mid-market e-commerce"),
  "industry": string,
  "company_size": string (e.g. "50-500 employees"),
  "region": string,
  "pain_points": [string, ...] (4),
  "buying_triggers": [string, ...] (4),
  "decision_makers": [string, ...] (3-4 job titles)
}`;

const LEADS_SCHEMA_HINT = `{
  "leads": [  // 20 to 25 items
    {
      "company": string (realistic company name, no placeholders),
      "website": string (https URL with a plausible domain for the company name),
      "industry": string,
      "company_size": string (e.g. "51-200"),
      "location": string (city, state/country),
      "contact_name": string (realistic full name),
      "contact_title": string (one of the ICP decision-maker titles),
      "contact_email": string (first.last@companydomain — lowercase, matching contact_name and website domain),
      "linkedin_url": string (https://linkedin.com/in/first-last),
      "score": number 0-100 (ICP fit),
      "score_reasons": [string, string, string] (2-3 concrete reasons),
      "deal_probability": number 0-1,
      "estimated_deal_value": number (USD, scaled to a deal size sensible for the seller's revenue goal)
    }
  ]
}`;

interface RawIcp {
  name?: unknown;
  industry?: unknown;
  company_size?: unknown;
  region?: unknown;
  pain_points?: unknown;
  buying_triggers?: unknown;
  decision_makers?: unknown;
}

interface RawLeads {
  leads?: unknown;
}

export async function runLeads(business: BusinessInput): Promise<LeadReport> {
  if (!isAiConfigured()) return generateLeadReport(business);

  try {
    const profile = JSON.stringify(
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
    );

    // Step 1 — ICP
    const rawIcp = await completeJson<RawIcp>({
      task: "extraction",
      system:
        "You are a B2B sales operations analyst. You distill business profiles into precise, actionable Ideal Customer Profiles.",
      prompt: `Derive the Ideal Customer Profile for this business:\n\n${profile}`,
      schemaHint: ICP_SCHEMA_HINT,
      maxTokens: 1500,
    });
    const icp = coerceIcp(rawIcp, business);

    // Steps 2+3 (merged for reliability) — prospect generation + scoring
    const rawLeads = await completeJson<RawLeads>({
      task: "generation",
      system:
        "You are a B2B prospecting researcher. You generate realistic, plausible prospect lists: real-sounding company names for the target industry (never 'Acme' or placeholders), realistic contact names with titles drawn from the ICP decision makers, emails in first.last@domain format matching the company website domain, and honest fit scores with concrete reasons.",
      prompt: [
        "Generate 20-25 prospect companies with one contact each, matching this seller and ICP. Score every lead.",
        "",
        "SELLER PROFILE:",
        profile,
        "",
        "IDEAL CUSTOMER PROFILE:",
        JSON.stringify(icp, null, 2),
        "",
        "Scoring rules: score 0-100 reflects ICP fit; give each lead 2-3 specific score_reasons (size match, hiring signals, funding, tech stack, trigger events); deal_probability between 0 and 1, roughly correlated with score; estimated_deal_value scaled to deals this seller would plausibly close given its revenue goal. Vary scores realistically (roughly 40-98).",
      ].join("\n"),
      schemaHint: LEADS_SCHEMA_HINT,
      maxTokens: 8000,
      temperature: 0.5,
    });

    const leads = coerceLeads(rawLeads, business);
    if (leads.length < 8) return generateLeadReport(business);

    // Step 4 — sort + pipeline value
    leads.sort((a, b) => b.score - a.score);
    return {
      generated_at: new Date().toISOString(),
      icp,
      leads,
      total_pipeline_value: leads.reduce(
        (s, l) => s + l.estimated_deal_value * l.deal_probability,
        0
      ),
    };
  } catch {
    return generateLeadReport(business);
  }
}

function coerceIcp(raw: RawIcp, business: BusinessInput): ICP {
  const fallback = generateICP(business);
  return {
    id: "icp_1",
    name: str(raw.name, fallback.name),
    industry: str(raw.industry, fallback.industry),
    company_size: str(raw.company_size, fallback.company_size),
    region: str(raw.region, fallback.region),
    pain_points: strArray(raw.pain_points, fallback.pain_points),
    buying_triggers: strArray(raw.buying_triggers, fallback.buying_triggers),
    decision_makers: strArray(raw.decision_makers, fallback.decision_makers),
  };
}

function coerceLeads(raw: RawLeads, business: BusinessInput): Lead[] {
  const avgDeal = Math.max(5_000, Math.round(business.revenue_goal / 60));
  const now = new Date().toISOString();

  return asArray(raw.leads)
    .map((item, i): Lead | null => {
      const o = asRecord(item);
      const company = str(o.company);
      if (!company) return null;
      const domain =
        str(o.website)
          .replace(/^https?:\/\//, "")
          .replace(/\/.*$/, "") ||
        `${company.toLowerCase().replace(/[^a-z0-9]/g, "")}.com`;
      const contactName = str(o.contact_name, "Alex Morgan");
      const [first = "alex", last = "morgan"] = contactName
        .toLowerCase()
        .split(/\s+/);
      const score = Math.round(clamp(o.score, 0, 100, 60));

      return {
        id: str(o.id, `lead_${i + 1}`),
        company,
        website: `https://${domain}`,
        industry: str(o.industry, business.target_customer || business.industry),
        company_size: str(o.company_size, "51-200"),
        location: str(o.location, business.location || "United States"),
        contact_name: contactName,
        contact_title: str(o.contact_title, "Head of Marketing"),
        contact_email: str(o.contact_email, `${first}.${last}@${domain}`),
        linkedin_url: str(
          o.linkedin_url,
          `https://linkedin.com/in/${first}-${last}`
        ),
        score,
        score_reasons: strArray(o.score_reasons, [
          "ICP fit on industry and size",
        ]).slice(0, 3),
        deal_probability:
          Math.round(clamp(o.deal_probability, 0, 1, score / 200) * 100) / 100,
        estimated_deal_value: Math.max(
          1000,
          Math.round(num(o.estimated_deal_value, avgDeal))
        ),
        status: "new",
        created_at: now,
      };
    })
    .filter((l): l is Lead => l !== null)
    .slice(0, 25);
}
