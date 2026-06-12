import "server-only";

/**
 * GrowthOS model routing layer.
 *
 * Routes each task class to the model best suited (and most cost-efficient)
 * for it, via OpenRouter. Every call has an automatic fallback chain so a
 * single provider outage never breaks a workflow.
 *
 *  - strategy   → Claude (deep reasoning: audits, competitive strategy, ICPs)
 *  - extraction → Gemini Flash (fast structured extraction & classification)
 *  - generation → DeepSeek (high-volume copy: emails, LinkedIn, follow-ups)
 */

export type TaskClass = "strategy" | "extraction" | "generation";

const MODEL_ROUTES: Record<TaskClass, string[]> = {
  strategy: [
    process.env.OPENROUTER_MODEL_STRATEGY ?? "anthropic/claude-sonnet-4",
    "google/gemini-2.5-pro",
    "deepseek/deepseek-chat-v3-0324",
  ],
  extraction: [
    process.env.OPENROUTER_MODEL_EXTRACTION ?? "google/gemini-2.5-flash",
    "deepseek/deepseek-chat-v3-0324",
    "anthropic/claude-sonnet-4",
  ],
  generation: [
    process.env.OPENROUTER_MODEL_GENERATION ?? "deepseek/deepseek-chat-v3-0324",
    "google/gemini-2.5-flash",
    "anthropic/claude-sonnet-4",
  ],
};

export function isAiConfigured(): boolean {
  return Boolean(process.env.OPENROUTER_API_KEY);
}

interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

interface CompleteOptions {
  task: TaskClass;
  system: string;
  prompt: string;
  maxTokens?: number;
  temperature?: number;
}

/** Raw text completion with model routing + fallback chain. */
export async function complete(opts: CompleteOptions): Promise<string> {
  const apiKey = process.env.OPENROUTER_API_KEY;
  if (!apiKey) throw new Error("OPENROUTER_API_KEY is not configured");

  const messages: ChatMessage[] = [
    { role: "system", content: opts.system },
    { role: "user", content: opts.prompt },
  ];

  let lastError: unknown;
  for (const model of MODEL_ROUTES[opts.task]) {
    try {
      const res = await fetch("https://openrouter.ai/api/v1/chat/completions", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${apiKey}`,
          "Content-Type": "application/json",
          "HTTP-Referer":
            process.env.NEXT_PUBLIC_APP_URL ?? "https://growthos.app",
          "X-Title": "GrowthOS",
        },
        body: JSON.stringify({
          model,
          messages,
          max_tokens: opts.maxTokens ?? 4096,
          temperature: opts.temperature ?? 0.4,
        }),
        signal: AbortSignal.timeout(90_000),
      });

      if (!res.ok) {
        lastError = new Error(`OpenRouter ${model} → HTTP ${res.status}`);
        continue;
      }

      const data = (await res.json()) as {
        choices?: { message?: { content?: string } }[];
      };
      const content = data.choices?.[0]?.message?.content;
      if (!content) {
        lastError = new Error(`OpenRouter ${model} → empty response`);
        continue;
      }
      return content;
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError instanceof Error
    ? lastError
    : new Error("All models in fallback chain failed");
}

/**
 * JSON completion: instructs the model to emit strict JSON, parses it, and
 * retries down the fallback chain on malformed output.
 */
export async function completeJson<T>(
  opts: CompleteOptions & { schemaHint: string }
): Promise<T> {
  const raw = await complete({
    ...opts,
    system: `${opts.system}\n\nRespond with ONLY valid JSON matching this shape (no markdown fences, no commentary):\n${opts.schemaHint}`,
    temperature: opts.temperature ?? 0.3,
  });
  return parseJsonLoose<T>(raw);
}

/** Tolerant JSON parser — strips code fences and leading/trailing prose. */
export function parseJsonLoose<T>(raw: string): T {
  let text = raw.trim();
  const fence = text.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (fence) text = fence[1].trim();
  const start = text.search(/[[{]/);
  if (start > 0) text = text.slice(start);
  const lastBrace = Math.max(text.lastIndexOf("}"), text.lastIndexOf("]"));
  if (lastBrace !== -1) text = text.slice(0, lastBrace + 1);
  return JSON.parse(text) as T;
}
