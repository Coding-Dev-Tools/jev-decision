/**
 * Zero-dependency TypeScript client, primitives, and harness guardrails for Jev (TypeSafe AI).
 */

export type QuestionType = "noul" | "choice" | "score";

export interface NoulQuestion {
  id: string;
  prompt: string;
  type: "noul";
}

export interface ChoiceQuestion {
  id: string;
  prompt: string;
  type: "choice";
  options: string[];
}

export interface ScoreQuestion {
  id: string;
  prompt: string;
  type: "score";
  scale: (number | string)[];
}

export type Question = NoulQuestion | ChoiceQuestion | ScoreQuestion;

export interface NoulDecision {
  id: string;
  probability: number;
  confidence: number;
}

export interface ChoiceDecision {
  id: string;
  selected: string;
  probabilities: Record<string, number>;
  confidence: number;
}

export interface ScoreDecision {
  id: string;
  score: number | string;
  probabilities: Record<string, number>;
  confidence: number;
}

export type Decision = NoulDecision | ChoiceDecision | ScoreDecision;

export interface DecisionBatch {
  state: string;
  decisions: Record<string, Decision>;
  latencyMs: number;
  isFallback: boolean;
  rawResponse?: any;
}

export interface CalibrationTier {
  tierDestructive: number; // 0.95
  tierLoopHalt: number;    // 0.85
  tierRelevancePrune: number; // 0.40
}

export const DEFAULT_CALIBRATION: CalibrationTier = {
  tierDestructive: 0.95,
  tierLoopHalt: 0.85,
  tierRelevancePrune: 0.40,
};

// Known safe / destructive patterns for deterministic offline fallback
const SAFE_PATTERNS = [
  /(?:^|\n|COMMAND:\s*)git\s+(status|diff|log|show|branch|rev-parse|stash\s+list)/i,
  /(?:^|\n|COMMAND:\s*)(ls|dir|cat|type|head|tail|grep|findstr|echo|pwd|where|which)\b/i,
  /(?:^|\n|COMMAND:\s*)(pytest|python\s+-m\s+pytest|npm\s+test|cargo\s+check|ruff\s+check)\b/i,
];

const DESTRUCTIVE_PATTERNS = [
  /\brm\s+-rf\s+[/~]/i,
  /\b(format|mkfs|fdisk|dd\s+if=)\b/i,
  /\b(drop\s+database|truncate\s+table)\b/i,
  /\bgit\s+push\s+.*(--force|-f)\b/i,
];

function tokenize(text: string): Set<string> {
  const words = text.toLowerCase().match(/\w+/g) || [];
  return new Set(words.filter(w => w.length > 1));
}

export function evaluateHeuristics(state: string, questions: Question[]): DecisionBatch {
  const decisions: Record<string, Decision> = {};

  for (const q of questions) {
    if (q.type === "noul") {
      const promptLower = q.prompt.toLowerCase();
      let prob = 0.50;
      let conf = 0.50;

      if (promptLower.includes("safe") || promptLower.includes("destructive")) {
        if (DESTRUCTIVE_PATTERNS.some(p => p.test(state))) {
          prob = 0.01;
          conf = 0.99;
        } else if (SAFE_PATTERNS.some(p => p.test(state))) {
          prob = 0.98;
          conf = 0.95;
        }
      } else if (promptLower.includes("complete") || promptLower.includes("finished")) {
        const stateLower = state.toLowerCase();
        if (stateLower.includes("error:") || stateLower.includes("failed") || stateLower.includes("assertionerror")) {
          prob = 0.05;
          conf = 0.95;
        } else if (stateLower.includes("passed") || stateLower.includes("100% green") || stateLower.includes("success")) {
          prob = 0.95;
          conf = 0.90;
        }
      }
      decisions[q.id] = { id: q.id, probability: prob, confidence: conf };

    } else if (q.type === "choice") {
      let selected = q.options[0] || "";
      let conf = 0.50;

      if (DESTRUCTIVE_PATTERNS.some(p => p.test(state))) {
        selected = q.options.find(o => o.includes("destruct") || o.includes("danger")) || selected;
        conf = 0.95;
      } else if (SAFE_PATTERNS.some(p => p.test(state))) {
        selected = q.options.find(o => o.includes("read") || o.includes("safe") || o.includes("inspect")) || selected;
        conf = 0.92;
      }

      const probs: Record<string, number> = {};
      for (const opt of q.options) {
        probs[opt] = opt === selected ? conf : (1.0 - conf) / Math.max(1, q.options.length - 1);
      }
      decisions[q.id] = { id: q.id, selected, probabilities: probs, confidence: conf };

    } else if (q.type === "score") {
      const score = q.scale[q.scale.length - 1] ?? 2;
      decisions[q.id] = { id: q.id, score, probabilities: {}, confidence: 0.70 };
    }
  }

  return {
    state,
    decisions,
    latencyMs: 0.5,
    isFallback: true,
  };
}

export interface JevClientOptions {
  apiKey?: string;
  baseUrl?: string;
  timeoutMs?: number;
  offlineMode?: boolean;
}

export class JevClient {
  public apiKey?: string;
  public baseUrl: string;
  public timeoutMs: number;
  public offlineMode: boolean;

  constructor(options: JevClientOptions = {}) {
    this.apiKey = options.apiKey || (typeof process !== "undefined" ? process.env?.TYPESAFE_API_KEY || process.env?.JEV_API_KEY : undefined);
    this.baseUrl = options.baseUrl || (typeof process !== "undefined" ? process.env?.JEV_ENDPOINT_URL : undefined) || "https://api.typesafe.ai/v1/decide";
    this.timeoutMs = options.timeoutMs ?? 2000;
    this.offlineMode = options.offlineMode ?? (typeof process !== "undefined" ? process.env?.JEV_OFFLINE_MODE === "1" : false);
  }

  public get isConfigured(): boolean {
    return !this.offlineMode && Boolean(this.apiKey && this.apiKey.trim());
  }

  public async evaluate(state: string, questions: Question[], model = "jev-1"): Promise<DecisionBatch> {
    if (!questions.length) {
      return { state, decisions: {}, latencyMs: 0, isFallback: false };
    }

    if (!this.isConfigured) {
      return evaluateHeuristics(state, questions);
    }

    const payload = {
      model,
      state,
      questions,
    };

    const start = Date.now();
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.timeoutMs);

      const resp = await fetch(this.baseUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${this.apiKey}`,
          "User-Agent": "jev-decision-ts/0.2.0",
        },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      clearTimeout(timer);

      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
      }

      const data = await resp.json();
      const elapsed = Date.now() - start;

      const decisions: Record<string, Decision> = {};
      for (const [id, val] of Object.entries((data as any).decisions || {})) {
        const v = val as any;
        if (v.type === "noul") {
          decisions[id] = { id, probability: Number(v.probability || 0), confidence: Number(v.confidence || 1) };
        } else if (v.type === "choice") {
          decisions[id] = { id, selected: String(v.selected || ""), probabilities: v.probabilities || {}, confidence: Number(v.confidence || 1) };
        } else if (v.type === "score") {
          decisions[id] = { id, score: v.score, probabilities: v.probabilities || {}, confidence: Number(v.confidence || 1) };
        }
      }

      return {
        state,
        decisions,
        latencyMs: elapsed,
        isFallback: false,
        rawResponse: data,
      };
    } catch (err) {
      const fallback = evaluateHeuristics(state, questions);
      fallback.latencyMs = Date.now() - start;
      return fallback;
    }
  }
}

// Harness Guard Helpers
export function guardBashCommand(
  command: string,
  cwd = "",
  client = new JevClient(),
  calibration = DEFAULT_CALIBRATION
): { allowAuto: boolean; escalateToUser: boolean; category: string; safetyProbability: number; isFallback: boolean } {
  const state = `CWD: ${cwd}\nCOMMAND: ${command.trim()}`;
  const questions: Question[] = [
    { id: "is_safe", type: "noul", prompt: "Is this command safe to run autonomously?" },
    { id: "category", type: "choice", prompt: "Categorize the command", options: ["read_only", "compile_test", "destructive_or_leak"] },
  ];

  const batch = evaluateHeuristics(state, questions);
  const safeDec = batch.decisions["is_safe"] as NoulDecision;
  const catDec = batch.decisions["category"] as ChoiceDecision;

  const prob = safeDec?.probability ?? 0.5;
  const cat = catDec?.selected ?? "unknown";
  const allowAuto = prob >= calibration.tierDestructive && cat !== "destructive_or_leak";

  return {
    allowAuto,
    escalateToUser: !allowAuto,
    category: cat,
    safetyProbability: prob,
    isFallback: batch.isFallback,
  };
}
