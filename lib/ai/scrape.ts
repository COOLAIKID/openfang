import "server-only";

/**
 * Lightweight server-side page analysis used by the Website Audit workflow.
 * Fetches real HTML and extracts the signals the audit agent reasons over.
 */

export interface PageSignals {
  url: string;
  status: number;
  title: string;
  metaDescription: string;
  h1s: string[];
  h2s: string[];
  ctaTexts: string[];
  wordCount: number;
  hasViewportMeta: boolean;
  hasOgTags: boolean;
  hasStructuredData: boolean;
  imageCount: number;
  imagesMissingAlt: number;
  internalLinks: string[];
  loadTimeMs: number;
}

const CTA_PATTERN =
  /(get started|start free|book a demo|request demo|sign up|try (it )?free|contact (us|sales)|buy now|subscribe|learn more|get my|start now|free trial)/i;

export async function fetchPageSignals(url: string): Promise<PageSignals> {
  const normalized = url.startsWith("http") ? url : `https://${url}`;
  const started = Date.now();
  const res = await fetch(normalized, {
    headers: { "User-Agent": "GrowthOS-AuditBot/1.0 (+https://growthos.app)" },
    signal: AbortSignal.timeout(15_000),
    redirect: "follow",
  });
  const html = await res.text();
  const loadTimeMs = Date.now() - started;

  const pick = (re: RegExp): string => html.match(re)?.[1]?.trim() ?? "";
  const pickAll = (re: RegExp): string[] => {
    const out: string[] = [];
    for (const m of html.matchAll(re)) {
      const text = m[1].replace(/<[^>]+>/g, "").trim();
      if (text) out.push(text);
    }
    return out;
  };

  const text = html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ");

  const anchors = pickAll(/<a[^>]*>([\s\S]*?)<\/a>/gi);
  const images = html.match(/<img[^>]*>/gi) ?? [];
  const origin = new URL(normalized).origin;
  const internalLinks = Array.from(
    new Set(
      Array.from(html.matchAll(/href="([^"#?]+)"/g))
        .map((m) => m[1])
        .filter((h) => h.startsWith("/") || h.startsWith(origin))
        .map((h) => (h.startsWith("/") ? origin + h : h))
    )
  ).slice(0, 25);

  return {
    url: normalized,
    status: res.status,
    title: pick(/<title[^>]*>([\s\S]*?)<\/title>/i),
    metaDescription: pick(
      /<meta[^>]*name=["']description["'][^>]*content=["']([^"']*)["']/i
    ),
    h1s: pickAll(/<h1[^>]*>([\s\S]*?)<\/h1>/gi).slice(0, 5),
    h2s: pickAll(/<h2[^>]*>([\s\S]*?)<\/h2>/gi).slice(0, 10),
    ctaTexts: anchors.filter((a) => CTA_PATTERN.test(a)).slice(0, 10),
    wordCount: text.split(/\s+/).filter(Boolean).length,
    hasViewportMeta: /<meta[^>]*name=["']viewport["']/i.test(html),
    hasOgTags: /<meta[^>]*property=["']og:/i.test(html),
    hasStructuredData: /application\/ld\+json/i.test(html),
    imageCount: images.length,
    imagesMissingAlt: images.filter((img) => !/alt=/.test(img)).length,
    internalLinks,
    loadTimeMs,
  };
}
