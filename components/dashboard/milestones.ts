"use client";

/**
 * Activation milestones persisted in localStorage ("growthos.milestones").
 * Pages call markMilestone(key) after a successful agent run; the overview
 * checklist reads them via useMilestones().
 */

import { useEffect, useState } from "react";

export type MilestoneKey =
  | "business"
  | "audit"
  | "competitors"
  | "leads"
  | "outreach";

export const MILESTONE_KEYS: MilestoneKey[] = [
  "business",
  "audit",
  "competitors",
  "leads",
  "outreach",
];

const STORAGE_KEY = "growthos.milestones";
const EVENT = "growthos:milestones";

export function readMilestones(): MilestoneKey[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return MILESTONE_KEYS.filter((k) => parsed.includes(k));
  } catch {
    return [];
  }
}

export function markMilestone(key: MilestoneKey): void {
  if (typeof window === "undefined") return;
  const current = readMilestones();
  if (current.includes(key)) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...current, key]));
    window.dispatchEvent(new Event(EVENT));
  } catch {
    // localStorage unavailable — non-fatal
  }
}

export function useMilestones(): MilestoneKey[] {
  const [milestones, setMilestones] = useState<MilestoneKey[]>([]);

  useEffect(() => {
    const sync = () => setMilestones(readMilestones());
    sync();
    window.addEventListener(EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  return milestones;
}
