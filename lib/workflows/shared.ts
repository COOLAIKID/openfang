import "server-only";

/** Small coercion helpers shared by the workflow agents. */

export function clamp(n: unknown, min: number, max: number, fallback: number): number {
  const v = typeof n === "number" && Number.isFinite(n) ? n : Number(n);
  if (!Number.isFinite(v)) return fallback;
  return Math.min(max, Math.max(min, v));
}

export function num(n: unknown, fallback: number): number {
  const v = typeof n === "number" && Number.isFinite(n) ? n : Number(n);
  return Number.isFinite(v) ? v : fallback;
}

export function str(s: unknown, fallback = ""): string {
  return typeof s === "string" && s.trim() ? s : fallback;
}

export function strArray(a: unknown, fallback: string[] = []): string[] {
  if (!Array.isArray(a)) return fallback;
  const out = a.filter((x): x is string => typeof x === "string" && x.trim().length > 0);
  return out.length > 0 ? out : fallback;
}

export function oneOf<T extends string>(
  value: unknown,
  allowed: readonly T[],
  fallback: T
): T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : fallback;
}

export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
