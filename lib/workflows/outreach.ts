import "server-only";

/**
 * Outreach Sequence agent.
 *
 * Generation-class call with a top-1% SDR coach persona producing a 5-step
 * sequence (3 emails + 2 LinkedIn touches at days 0/3/5/9/14). Personalizes
 * with lead fields when a lead is provided, otherwise uses {{placeholders}}.
 *
 * Falls back to the deterministic demo generator on any failure.
 */

import { completeJson, isAiConfigured } from "@/lib/ai/router";
import { generateSequence } from "@/lib/demo";
import type { BusinessInput, Lead, SequenceStep } from "@/lib/types";
import { asArray, asRecord, num, oneOf, str } from "@/lib/workflows/shared";

export const WORKFLOW_STEPS = [
  { key: "research", label: "Researching the prospect angle" },
  { key: "write", label: "Writing the 5-step sequence" },
  { key: "polish", label: "Polishing subjects and CTAs" },
];

const DELAYS = [0, 3, 5, 9, 14];
const CHANNELS: SequenceStep["channel"][] = [
  "email",
  "email",
  "linkedin",
  "email",
  "linkedin",
];

const SEQUENCE_SCHEMA_HINT = `{
  "sequence": [  // exactly 5 steps, in order
    { "step": 1, "channel": "email",    "delay_days": 0,  "subject": string (<55 chars), "body": string },
    { "step": 2, "channel": "email",    "delay_days": 3,  "subject": string (<55 chars), "body": string },
    { "step": 3, "channel": "linkedin", "delay_days": 5,  "body": string (short connection note, no subject) },
    { "step": 4, "channel": "email",    "delay_days": 9,  "subject": string (<55 chars), "body": string },
    { "step": 5, "channel": "linkedin", "delay_days": 14, "body": string (short breakup note, no subject) }
  ]
}`;

interface RawSequence {
  sequence?: unknown;
}

export async function runOutreach(
  business: BusinessInput,
  lead?: Partial<Lead>
): Promise<{ sequence: SequenceStep[] }> {
  const fallbackLead =
    lead && lead.contact_name && lead.company ? (lead as Lead) : undefined;
  if (!isAiConfigured())
    return { sequence: generateSequence(business, fallbackLead) };

  try {
    const personalization = lead
      ? [
          "PROSPECT (personalize every step with these real details):",
          JSON.stringify(
            {
              company: lead.company,
              website: lead.website,
              industry: lead.industry,
              company_size: lead.company_size,
              location: lead.location,
              contact_name: lead.contact_name,
              contact_title: lead.contact_title,
              score_reasons: lead.score_reasons,
            },
            null,
            2
          ),
        ].join("\n")
      : "PROSPECT: none provided — use template placeholders like {{first_name}}, {{company}}, {{your_name}}, {{calendar_link}} wherever a personal detail belongs.";

    const raw = await completeJson<RawSequence>({
      task: "generation",
      system:
        "You are a top 1% SDR coach who writes cold outbound that gets 15%+ reply rates. Rules you never break: value-first (lead with an insight about THEIR business, never about the sender); each email under 120 words; subjects under 55 characters, lowercase-friendly, curiosity-driven, zero spam words (no 'free', 'guarantee', 'act now', 'limited time', '!'); one idea per touch; explicit CTAs only in steps 2 and 4 (a specific meeting ask); steps 1, 3 and 5 end softly with no hard ask; the final LinkedIn touch is a graceful breakup. Plain text only, line breaks as \\n.",
      prompt: [
        "Write a 5-step outbound sequence (3 emails + 2 LinkedIn) selling for this business.",
        "",
        "SELLER:",
        JSON.stringify(
          {
            name: business.name,
            website_url: business.website_url,
            industry: business.industry,
            target_customer: business.target_customer,
            annual_revenue_goal_usd: business.revenue_goal,
          },
          null,
          2
        ),
        "",
        personalization,
        "",
        "Cadence is fixed: day 0 email, day 3 email (CTA), day 5 LinkedIn, day 9 email (CTA), day 14 LinkedIn breakup.",
      ].join("\n"),
      schemaHint: SEQUENCE_SCHEMA_HINT,
      maxTokens: 3000,
      temperature: 0.6,
    });

    const sequence = coerceSequence(raw);
    if (sequence.length !== 5)
      return { sequence: generateSequence(business, fallbackLead) };
    return { sequence };
  } catch {
    return { sequence: generateSequence(business, fallbackLead) };
  }
}

function coerceSequence(raw: RawSequence): SequenceStep[] {
  const items = asArray(raw.sequence).slice(0, 5);
  if (items.length !== 5) return [];
  if (items.some((item) => !str(asRecord(item).body))) return [];

  return items.map((item, i): SequenceStep => {
    const o = asRecord(item);
    const channel = oneOf(o.channel, ["email", "linkedin"] as const, CHANNELS[i]);
    const body = str(o.body);
    const step: SequenceStep = {
      step: Math.round(num(o.step, i + 1)) || i + 1,
      channel,
      delay_days: Math.max(0, Math.round(num(o.delay_days, DELAYS[i]))),
      body,
    };
    if (channel === "email") {
      step.subject = str(o.subject, "quick question").slice(0, 55);
    }
    return step;
  });
}
