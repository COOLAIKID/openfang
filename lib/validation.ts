import { z } from "zod";
import type { BusinessInput } from "@/lib/types";

/**
 * Request validation schemas for GrowthOS API routes.
 * All POST bodies are validated here; invalid input → 400 { error }.
 */

/** Prepend https:// to bare domains so "acme.com" is accepted. */
export function normalizeUrl(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return trimmed;
  return /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
}

export const businessInputSchema = z.object({
  name: z.string().min(1, "Business name is required"),
  website_url: z
    .string()
    .min(4, "Website URL is required")
    .transform(normalizeUrl),
  industry: z.string().catch("").default(""),
  location: z.string().catch("").default(""),
  target_customer: z.string().catch("").default(""),
  revenue_goal: z.coerce
    .number()
    .positive("Revenue goal must be positive")
    .catch(1_000_000)
    .default(1_000_000),
});

export type BusinessInputParsed = z.infer<typeof businessInputSchema>;

/**
 * Parse an unknown request body into a BusinessInput.
 * Throws ZodError on invalid input — callers map that to a 400.
 */
export function parseBusinessInput(body: unknown): BusinessInput {
  const parsed = businessInputSchema.parse(body ?? {});
  return {
    name: parsed.name,
    website_url: parsed.website_url,
    industry: parsed.industry,
    location: parsed.location,
    target_customer: parsed.target_customer,
    revenue_goal: parsed.revenue_goal,
  };
}

/** Loose lead schema — passthrough so extra fields survive round-trips. */
export const leadSchema = z
  .object({
    id: z.string().optional(),
    company: z.string().optional(),
    website: z.string().optional(),
    industry: z.string().optional(),
    company_size: z.string().optional(),
    location: z.string().optional(),
    contact_name: z.string().optional(),
    contact_title: z.string().optional(),
    contact_email: z.string().optional(),
    linkedin_url: z.string().optional(),
    score: z.coerce.number().optional(),
    score_reasons: z.array(z.string()).optional(),
    deal_probability: z.coerce.number().optional(),
    estimated_deal_value: z.coerce.number().optional(),
    status: z.string().optional(),
    created_at: z.string().optional(),
  })
  .passthrough();

export type LeadParsed = z.infer<typeof leadSchema>;
