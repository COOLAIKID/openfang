"use client";

/**
 * Client-side business profile store.
 *
 * In demo mode (no Supabase) the profile lives in localStorage so the entire
 * product is usable without an account. When Supabase is configured, the
 * onboarding flow also persists via POST /api/business and this acts as a
 * fast local cache.
 */

import { DEMO_BUSINESS } from "@/lib/demo";
import type { BusinessInput } from "@/lib/types";

const KEY = "growthos.business";

export function loadBusiness(): BusinessInput | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<BusinessInput>;
    if (!parsed.name || !parsed.website_url) return null;
    return {
      name: parsed.name,
      website_url: parsed.website_url,
      industry: parsed.industry ?? "",
      location: parsed.location ?? "",
      revenue_goal: Number(parsed.revenue_goal) || 1_000_000,
      target_customer: parsed.target_customer ?? "",
    };
  } catch {
    return null;
  }
}

export function saveBusiness(business: BusinessInput): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(KEY, JSON.stringify(business));
}

export function clearBusiness(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(KEY);
}

/** Business profile to render with: the user's, or the demo company. */
export function businessOrDemo(): BusinessInput {
  return loadBusiness() ?? DEMO_BUSINESS;
}

export function hasOnboarded(): boolean {
  return loadBusiness() !== null;
}
