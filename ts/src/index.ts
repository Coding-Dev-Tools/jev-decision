/**
 * Explicit, unmanaged TypeSafe API client. This package does not read credentials
 * from the environment, enforce the managed harness budget, or authorize actions.
 * Installed harnesses must use the Python MCP/CLI runtime for those controls.
 */
import { createHash, randomUUID } from "node:crypto";

export const DEFAULT_MODEL = "jev-1.13.0";
export const DEFAULT_TYPESAFE_ENDPOINT = "https://api.typesafe.ai/v1/systemone";
export const MAX_REQUEST_BYTES = 24_576;
export const MAX_RESPONSE_BYTES = 262_144;
export const MAX_DEADLINE_MS = 5_000;
const MAX_INPUT_TOKENS = 64_000;
const PROBABILITY_TOLERANCE = 1e-3;
const ROUNDING_EPSILON = 1e-12;

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export type State = string | JsonValue[] | { [key: string]: JsonValue };
export type Description = string | JsonValue[] | { [key: string]: JsonValue };
export type QuestionType = "noul" | "choice" | "score";
export interface NoulQuestion {
  id: string;
  prompt: Description;
  type: "noul";
  criteria?: { true?: Description; false?: Description };
}
export interface ChoiceQuestion {
  id: string;
  prompt: Description;
  type: "choice";
  options?: readonly string[];
  criteria?: Record<string, Description | null>;
}
export interface ScoreQuestion {
  id: string;
  prompt: Description;
  type: "score";
  criteria?: readonly Description[];
  /** Compatibility input: descriptive strings only; numeric-only scales are invalid. */
  scale?: readonly string[];
}
export type Question = NoulQuestion | ChoiceQuestion | ScoreQuestion;
export type NativeQuestion = {
  type: "noul";
  instructions: Description;
  criteria?: { true?: Description; false?: Description };
} | {
  type: "choice";
  instructions: Description;
  criteria: Record<string, Description | null>;
} | {
  type: "score";
  instructions: Description;
  criteria: readonly Description[];
};
export type Questions = readonly Question[] | Record<string, NativeQuestion>;

export interface NoulDecision {
  type: "noul";
  probability: number;
  /** Noul's probability is not a separately calibrated confidence estimate. */
  confidence: null;
}
export interface ChoiceDecision {
  type: "choice";
  selected: string;
  probabilities: Record<string, number>;
  confidence: number;
}
export interface ScoreDecision {
  type: "score";
  score: number;
  legend: Record<string, Description>;
  probabilities: Record<string, number>;
  confidence: number;
}
export type Decision = NoulDecision | ChoiceDecision | ScoreDecision;
export type ErrorCode = "missing_key" | "offline" | "invalid_request" | "request_too_large"
  | "response_too_large" | "invalid_response" | "model_mismatch" | "timeout"
  | "authentication_error" | "rate_limited" | "provider_error" | "transport_error"
  | "redirect_rejected" | "budget_exhausted" | "budget_unavailable" | "runtime_disabled"
  | "credential_unavailable" | "configuration_error" | "credential_in_payload";
export interface DecisionBatch {
  status: "ok" | "unavailable" | "offline";
  source: "provider" | "cache" | "heuristic" | "none";
  decisions: Record<string, Decision>;
  requested_model: string;
  resolved_model: string | null;
  usage: { input_tokens: number | null; output_tokens: number | null };
  latency_ms: number;
  attempts: number;
  request_id: string;
  error_code: ErrorCode | null;
  is_fallback: false;
}

class ClientFailure extends Error {
  constructor(readonly code: ErrorCode, readonly transient = false, readonly retryAfterMs: number | null = null) {
    // Only a fixed code is ever exposed; provider bodies and transport errors are discarded.
    super(code);
  }
}
function fail(code: ErrorCode): never { throw new ClientFailure(code); }
function isRecord(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}
function equalKeys(actual: Record<string, unknown>, expected: readonly string[]): boolean {
  const keys = Object.keys(actual);
  return keys.length === expected.length && expected.every(key => Object.hasOwn(actual, key));
}
function descriptive(value: unknown, depth = 0): boolean {
  if (depth > 32) return false;
  if (typeof value === "string") return value.trim().length > 0 && /\p{L}/u.test(value);
  if (Array.isArray(value)) return value.length > 0 && value.some(v => descriptive(v, depth + 1));
  if (isRecord(value)) return Object.values(value).some(v => descriptive(v, depth + 1));
  return false;
}
function assertDescription(value: unknown): asserts value is Description {
  if (!descriptive(value)) fail("invalid_request");
}
function assertId(value: unknown): asserts value is string {
  if (typeof value !== "string" || !value.trim() || value.length > 200) fail("invalid_request");
}

// Python re uses Unicode whitespace/word boundaries and its case-insensitive
// Latin ranges include dotted/dotless I, long S and the Kelvin sign. Define that
// policy explicitly rather than silently changing it with JavaScript's \s/\b/i.
const ID_WHITESPACE = "\\x09-\\x0d\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const ID_WORD = "\\p{L}\\p{N}_";
const ID_WORD_BOUNDARY = `(?:(?<=[${ID_WORD}])(?![${ID_WORD}])|(?<![${ID_WORD}])(?=[${ID_WORD}]))`;
const ID_SECRET_NAMES = "typesafe_api_key|jev_api_key|api[_-]?key|api[_-]?token|secret|password|passwd|authorization|access[_-]?token|refresh[_-]?token|client[_-]?secret|private[_-]?key|aws_secret_access_key|_authToken|_auth"
  .replace(/[iks]/g, letter => ({ i: "[iİı]", k: "[kK]", s: "[sſ]" })[letter]!);
const ID_URL_USERINFO = new RegExp(`(http[sſ]?://)[^${ID_WHITESPACE}/@]+:[^${ID_WHITESPACE}/@]+@`, "giu");
const ID_BEARER = new RegExp(`(?<![${ID_WORD}])Bearer[${ID_WHITESPACE}]+[A-Za-zİı0-9._~+/=-]+`, "giu");
const ID_TOKEN = new RegExp(`(?<![${ID_WORD}])(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{12,}|github_pat_[A-Za-z0-9_]{12,}|npm_[A-Za-z0-9]{12,}|apikey_[A-Za-z0-9_-]{16,})${ID_WORD_BOUNDARY}`, "gu");
const ID_JWT = new RegExp(`(?<![${ID_WORD}])eyJ[A-Za-z0-9_-]{5,}\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+${ID_WORD_BOUNDARY}`, "gu");
const ID_ASSIGNMENT = new RegExp(`(["']?(?:${ID_SECRET_NAMES})["']?[${ID_WHITESPACE}]*[:=][${ID_WHITESPACE}]*)(?:"(?:\\\\[^\\n]|[^"\\\\])*"|'(?:\\\\[^\\n]|[^'\\\\])*'|\\[REDACTED\\]|[^${ID_WHITESPACE},;}\\]]+)`, "giu");

/** Match the Python wire-ID safeguards; original IDs stay local to this call. */
function sanitizeQuestionId(value: string, secret?: string): string {
  if (secret) value = value.split(secret).join("[REDACTED]");
  return value
    .replace(/-----BEGIN (?:[A-Z0-9 ]*PRIVATE KEY)-----[\s\S]*?-----END (?:[A-Z0-9 ]*PRIVATE KEY)-----/g, "[REDACTED PRIVATE KEY]")
    .replace(ID_URL_USERINFO, "$1[REDACTED]@")
    .replace(ID_BEARER, "Bearer [REDACTED]")
    .replace(ID_TOKEN, "[REDACTED]")
    .replace(ID_JWT, "[REDACTED]")
    .replace(ID_ASSIGNMENT, '$1"[REDACTED]"');
}

/** Check JSON without invoking custom toJSON methods or accepting undefined/NaN. */
function validateJson(value: unknown, limit: number, error: ErrorCode): void {
  const ancestors = new Set<object>();
  let size = 0;
  let nodes = 0;
  const visit = (item: unknown, depth: number): void => {
    if (depth > 32 || ++nodes > 100_000) fail(error);
    if (item === null || typeof item === "boolean") size += 5;
    else if (typeof item === "number") {
      if (!Number.isFinite(item)) fail(error);
      size += String(item).length;
    } else if (typeof item === "string") size += Buffer.byteLength(item, "utf8");
    else if (Array.isArray(item) || isRecord(item)) {
      if (ancestors.has(item)) fail(error);
      ancestors.add(item);
      const descriptors = Object.getOwnPropertyDescriptors(item);
      if (Array.isArray(item) && (Object.keys(item).length !== item.length || Object.keys(item).some((key, index) => key !== String(index)))) fail(error);
      for (const [key, descriptor] of Object.entries(descriptors)) {
        if (Array.isArray(item) && key === "length") continue;
        if (!descriptor.enumerable || !("value" in descriptor)) fail(error);
        size += Buffer.byteLength(key, "utf8") + 3;
        visit(descriptor.value, depth + 1);
      }
      ancestors.delete(item);
    } else fail(error);
    if (size > limit) fail(error === "invalid_request" ? "request_too_large" : error);
  };
  visit(value, 0);
}

/** Convert typed convenience questions or a native question map to the official schema. */
export function normalizeQuestions(input: Questions): Record<string, NativeQuestion> {
  validateJson(input, MAX_REQUEST_BYTES, "invalid_request");
  const typed = Array.isArray(input);
  let entries: [string, unknown][];
  if (Array.isArray(input)) {
    entries = input.map(q => {
      if (!isRecord(q)) fail("invalid_request");
      assertId(q.id);
      return [q.id, q];
    });
  } else if (isRecord(input)) entries = Object.entries(input);
  else fail("invalid_request");
  if (entries.length < 1 || entries.length > 128) fail("invalid_request");
  const output: [string, NativeQuestion][] = [];
  const ids = new Set<string>();
  for (const [id, value] of entries) {
    assertId(id);
    if (ids.has(id) || !isRecord(value)) fail("invalid_request");
    ids.add(id);
    const q = value;
    const allowed = typed
      ? ["id", "type", "prompt", "criteria", ...(q.type === "choice" ? ["options"] : q.type === "score" ? ["scale"] : [])]
      : ["type", "instructions", "criteria"];
    if (Object.keys(q).some(key => !allowed.includes(key))) fail("invalid_request");
    const instructions = typed ? q.prompt : q.instructions;
    if (!((typeof instructions === "string" && instructions.trim()) || (Array.isArray(instructions) && instructions.length) || (isRecord(instructions) && Object.keys(instructions).length))) fail("invalid_request");
    const common = { instructions: instructions as Description };
    if (q.type === "noul") {
      const question: NativeQuestion = { type: "noul", ...common };
      if (q.criteria !== undefined) {
        if (!isRecord(q.criteria) || !Object.keys(q.criteria).length || Object.keys(q.criteria).some(k => k !== "true" && k !== "false")) fail("invalid_request");
        Object.values(q.criteria).forEach(assertDescription);
        question.criteria = q.criteria;
      }
      output.push([id, question]);
    } else if (q.type === "choice") {
      let criteria: Record<string, unknown>;
      if (q.criteria !== undefined) {
        if (!isRecord(q.criteria)) fail("invalid_request");
        criteria = q.criteria;
        if (q.options !== undefined && (!Array.isArray(q.options) || q.options.some(o => typeof o !== "string") || new Set(q.options).size !== q.options.length || JSON.stringify(Object.keys(criteria)) !== JSON.stringify(q.options))) fail("invalid_request");
      } else {
        if (!Array.isArray(q.options) || q.options.some(o => typeof o !== "string") || new Set(q.options).size !== q.options.length) fail("invalid_request");
        criteria = Object.fromEntries(q.options.map(option => [option, option]));
      }
      const options = Object.keys(criteria);
      if (options.length < 2 || options.length > 255) fail("invalid_request");
      if (options.some(key => !key.trim())) fail("invalid_request");
      Object.values(criteria).forEach(value => { if (value !== null) assertDescription(value); });
      output.push([id, { type: "choice", ...common, criteria: criteria as Record<string, Description | null> }]);
    } else if (q.type === "score") {
      if (q.criteria !== undefined && q.scale !== undefined && stableJson(q.criteria) !== stableJson(q.scale)) fail("invalid_request");
      const criteria = q.criteria ?? q.scale;
      if (!Array.isArray(criteria) || criteria.length < 2 || criteria.length > 10) fail("invalid_request");
      if (q.scale !== undefined && criteria.some(v => typeof v !== "string")) fail("invalid_request");
      criteria.forEach(assertDescription);
      if (new Set(criteria.map(stableJson)).size !== criteria.length) fail("invalid_request");
      output.push([id, { type: "score", ...common, criteria }]);
    } else fail("invalid_request");
  }
  return Object.fromEntries(output);
}

function probability(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1) fail("invalid_response");
  return value;
}
/** The provider rounds probability fields to two decimal places. */
function roundingIntervals(values: readonly number[]): [number, number][] | null {
  if (!values.every(value => Math.abs(value * 100 - Math.round(value * 100)) <= 1e-8)) return null;
  return values.map(value => [Math.max(0, value - 0.005), Math.min(1, value + 0.005)]);
}
function distribution(value: unknown, keys: readonly string[]): Record<string, number> {
  if (!isRecord(value) || !equalKeys(value, keys)) fail("invalid_response");
  const entries = keys.map(key => [key, probability(value[key])] as const);
  const values = entries.map(([, p]) => p);
  const total = values.reduce((sum, p) => sum + p, 0);
  if (total <= 0) fail("invalid_response");
  const intervals = roundingIntervals(values);
  if (intervals) {
    const lower = intervals.reduce((sum, [low]) => sum + low, 0);
    const upper = intervals.reduce((sum, [, high]) => sum + high, 0);
    if (lower > 1 + ROUNDING_EPSILON || upper < 1 - ROUNDING_EPSILON) fail("invalid_response");
  } else if (Math.abs(total - 1) > PROBABILITY_TOLERANCE) fail("invalid_response");
  return Object.fromEntries(entries);
}
/** Extremize the expected index while keeping the unrounded probabilities summing to one. */
function weightedExtreme(intervals: readonly [number, number][], descending: boolean): number {
  let remaining = Math.max(0, 1 - intervals.reduce((sum, [low]) => sum + low, 0));
  let mean = intervals.reduce((sum, [low], index) => sum + low * index, 0);
  const indices = intervals.map((_, index) => index);
  if (descending) indices.reverse();
  for (const index of indices) {
    const [low, high] = intervals[index];
    const allocated = Math.min(remaining, high - low);
    mean += allocated * index;
    remaining = Math.max(0, remaining - allocated);
  }
  if (remaining > ROUNDING_EPSILON) fail("invalid_response");
  return mean;
}
function scoreConsistent(score: number, values: readonly number[]): boolean {
  const intervals = roundingIntervals(values);
  if (!intervals) {
    const weighted = values.reduce((sum, p, index) => sum + p * index, 0);
    return Math.abs(score - weighted) <= PROBABILITY_TOLERANCE;
  }
  const minimum = weightedExtreme(intervals, false);
  const maximum = weightedExtreme(intervals, true);
  return score + 0.005 >= minimum - ROUNDING_EPSILON && score - 0.005 <= maximum + ROUNDING_EPSILON;
}
function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (isRecord(value)) return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${stableJson(value[k])}`).join(",")}}`;
  return JSON.stringify(value);
}
function parseResponse(data: unknown, questions: Record<string, NativeQuestion>): Pick<DecisionBatch, "decisions" | "resolved_model" | "usage"> {
  if (!isRecord(data)) fail("invalid_response");
  if (data.model !== DEFAULT_MODEL) fail("model_mismatch");
  if (!isRecord(data.answers) || !equalKeys(data.answers, Object.keys(questions))) fail("invalid_response");
  const decisions: [string, Decision][] = [];
  for (const [id, q] of Object.entries(questions)) {
    const answer = data.answers[id];
    if (!isRecord(answer) || answer.type !== q.type) fail("invalid_response");
    if (q.type === "noul") {
      if (!equalKeys(answer, ["type", "noul"])) fail("invalid_response");
      decisions.push([id, { type: "noul", probability: probability(answer.noul), confidence: null }]);
    } else if (q.type === "choice") {
      if (!equalKeys(answer, ["type", "choice", "confidence", "probabilities"])) fail("invalid_response");
      const probabilities = distribution(answer.probabilities, Object.keys(q.criteria));
      if (typeof answer.choice !== "string" || !Object.hasOwn(probabilities, answer.choice)) fail("invalid_response");
      if (Math.max(...Object.values(probabilities)) - probabilities[answer.choice] > PROBABILITY_TOLERANCE) fail("invalid_response");
      decisions.push([id, { type: "choice", selected: answer.choice, probabilities, confidence: probability(answer.confidence) }]);
    } else {
      if (!equalKeys(answer, ["type", "score", "legend", "confidence", "probabilities"])) fail("invalid_response");
      const keys = q.criteria.map((_, index) => String(index));
      if (!isRecord(answer.legend) || !equalKeys(answer.legend, keys)) fail("invalid_response");
      for (const [index, criterion] of q.criteria.entries()) {
        if (stableJson(answer.legend[String(index)]) !== stableJson(criterion)) fail("invalid_response");
      }
      const probabilities = distribution(answer.probabilities, keys);
      const score = answer.score;
      if (typeof score !== "number" || !Number.isFinite(score) || score < 0 || score > q.criteria.length - 1) fail("invalid_response");
      if (!scoreConsistent(score, keys.map(key => probabilities[key]))) fail("invalid_response");
      decisions.push([id, { type: "score", score, legend: answer.legend as Record<string, Description>, probabilities, confidence: probability(answer.confidence) }]);
    }
  }
  const usage = isRecord(data.usage) ? data.usage : {};
  const tokens = (v: unknown): number | null => typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : null;
  return { decisions: Object.fromEntries(decisions), resolved_model: data.model, usage: { input_tokens: tokens(usage.input_tokens), output_tokens: tokens(usage.output_tokens) } };
}

function unavailable(requestId: string, started: number, code: ErrorCode, attempts = 0): DecisionBatch {
  return {
    status: code === "offline" ? "offline" : "unavailable", source: "none", decisions: {},
    requested_model: DEFAULT_MODEL, resolved_model: null, usage: { input_tokens: null, output_tokens: null },
    latency_ms: Math.max(0, performance.now() - started), attempts, request_id: requestId,
    error_code: code, is_fallback: false,
  };
}
function beforeDeadline<T>(pending: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const abort = (): void => reject(new ClientFailure("timeout"));
    if (signal.aborted) { abort(); return; }
    signal.addEventListener("abort", abort, { once: true });
    pending.then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
  });
}
async function discard(response: Response): Promise<void> {
  try { await response.body?.cancel(); } catch { /* Never expose response data. */ }
}
function retryAfterMilliseconds(value: string | null): number | null {
  if (value === null || value.length > 128) return null;
  const hint = value.trim();
  if (/^[0-9]+(?:\.[0-9]+)?$/.test(hint)) {
    const milliseconds = Number(hint) * 1000;
    return Number.isFinite(milliseconds) ? milliseconds : null;
  }
  // Require an HTTP date, rather than Date.parse's permissive numeric/date input.
  if (!/^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?,/i.test(hint)) return null;
  const parsed = Date.parse(hint);
  return Number.isFinite(parsed) ? Math.max(0, parsed - Date.now()) : null;
}
/** JSON.parse validates syntax; this bounded second pass rejects duplicate decoded keys. */
function strictJsonParse(text: string): unknown {
  const data: unknown = JSON.parse(text);
  let cursor = 0;
  const whitespace = (): void => { while (/\s/.test(text[cursor] ?? "") && cursor < text.length) cursor++; };
  const stringToken = (): string => {
    const start = cursor++;
    while (cursor < text.length) {
      if (text[cursor] === "\\") { cursor += 2; continue; }
      if (text[cursor++] === '"') return JSON.parse(text.slice(start, cursor)) as string;
    }
    fail("invalid_response");
  };
  const value = (depth: number): void => {
    if (depth > 32) fail("invalid_response");
    whitespace();
    const first = text[cursor];
    if (first === '"') { stringToken(); return; }
    if (first === "{" || first === "[") {
      const object = first === "{";
      const end = object ? "}" : "]";
      const keys = new Set<string>();
      cursor++; whitespace();
      while (text[cursor] !== end) {
        if (object) {
          const key = stringToken();
          if (keys.has(key)) fail("invalid_response");
          keys.add(key);
          whitespace(); cursor++; // Validated colon.
        }
        value(depth + 1); whitespace();
        if (text[cursor] !== ",") break;
        cursor++; whitespace();
      }
      cursor++; return;
    }
    while (cursor < text.length && !/[\s,}\]]/.test(text[cursor])) cursor++;
  };
  value(0);
  return data;
}
async function readResponse(response: Response, signal: AbortSignal): Promise<unknown> {
  const length = response.headers.get("content-length");
  if (length !== null && (!/^\d+$/.test(length) || !Number.isSafeInteger(Number(length)))) fail("invalid_response");
  if (length !== null && Number(length) > MAX_RESPONSE_BYTES) fail("response_too_large");
  const contentType = response.headers.get("content-type");
  if (contentType && !/^application\/json(?:\s*;|$)/i.test(contentType)) fail("invalid_response");
  if (!response.body) fail("invalid_response");
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let size = 0;
  let text = "";
  try {
    while (true) {
      const chunk = await beforeDeadline(reader.read(), signal);
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > MAX_RESPONSE_BYTES) fail("response_too_large");
      text += decoder.decode(chunk.value, { stream: true });
    }
    text += decoder.decode();
    const data = strictJsonParse(text);
    validateJson(data, MAX_RESPONSE_BYTES, "invalid_response");
    return data;
  } catch (error) {
    if (error instanceof ClientFailure) throw error;
    fail("invalid_response");
  } finally {
    // Do not await an uncooperative stream cancellation beyond the request deadline.
    void reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export interface JevClientOptions {
  /** Required for online calls. Never inferred from global environment variables. */
  apiKey?: string;
  /** Compatibility option: only the exact official endpoint is accepted. */
  baseUrl?: string;
  /** Total budget including response body and retry; may shorten but not exceed 5000 ms. */
  timeoutMs?: number;
  offlineMode?: boolean;
  cacheEnabled?: boolean;
  /** Test transport injection; production normally uses global fetch. */
  fetchImpl?: typeof fetch;
}
export interface JevEvaluator {
  evaluate(state: State, questions: Questions, model?: string): Promise<DecisionBatch>;
}

export class JevClient implements JevEvaluator {
  #timeoutMs: number;
  #offlineMode: boolean;
  #apiKey?: string;
  #fetch: typeof fetch;
  #cacheEnabled: boolean;
  #cache = new Map<string, DecisionBatch>();
  #inFlight = new Map<string, Promise<DecisionBatch>>();

  constructor(options: JevClientOptions = {}) {
    if (options.baseUrl !== undefined && options.baseUrl !== DEFAULT_TYPESAFE_ENDPOINT) throw new TypeError("Only the official TypeSafe System One endpoint is supported.");
    this.#timeoutMs = options.timeoutMs ?? MAX_DEADLINE_MS;
    if (!Number.isInteger(this.#timeoutMs) || this.#timeoutMs < 1 || this.#timeoutMs > MAX_DEADLINE_MS) throw new RangeError("timeoutMs must be an integer between 1 and 5000.");
    // Unexpanded ${NAME}/{env:NAME}/$NAME/%NAME% references (passed through by some
    // harness configurations when the variable is unset) are never credentials.
    if (options.apiKey !== undefined && (typeof options.apiKey !== "string" || !/^[\x21-\x7e]{1,512}$/.test(options.apiKey)
      || /^(?:\$\{[^{}]*\}|\{env:[^{}]*\}|\$[A-Za-z_][A-Za-z0-9_]*|%[A-Za-z_][A-Za-z0-9_]*%)$/.test(options.apiKey))) throw new TypeError("Invalid API credential format.");
    this.#apiKey = options.apiKey;
    this.#offlineMode = options.offlineMode ?? false;
    this.#fetch = options.fetchImpl ?? globalThis.fetch;
    this.#cacheEnabled = options.cacheEnabled ?? true;
  }
  get mode(): "unmanaged_explicit_api" { return "unmanaged_explicit_api"; }
  get baseUrl(): string { return DEFAULT_TYPESAFE_ENDPOINT; }
  get timeoutMs(): number { return this.#timeoutMs; }
  get offlineMode(): boolean { return this.#offlineMode; }
  get isConfigured(): boolean { return !this.#offlineMode && Boolean(this.#apiKey); }
  clearCache(): void { this.#cache.clear(); }

  async evaluate(state: State, questions: Questions, model = DEFAULT_MODEL): Promise<DecisionBatch> {
    const started = performance.now();
    const requestId = randomUUID();
    let body: string;
    let canonical: Record<string, NativeQuestion>;
    let hash: string;
    const originalIds = new Map<string, string>();
    try {
      if (model !== DEFAULT_MODEL || !((typeof state === "string" && state.trim()) || (Array.isArray(state) && state.length) || (isRecord(state) && Object.keys(state).length))) fail("invalid_request");
      const wireQuestions = Object.fromEntries(Object.entries(normalizeQuestions(questions)).map(([id, question]) => {
        const wireId = sanitizeQuestionId(id, this.#apiKey);
        assertId(wireId);
        if (originalIds.has(wireId)) fail("invalid_request");
        originalIds.set(wireId, id);
        return [wireId, question];
      }));
      const payload = { model: DEFAULT_MODEL, state, questions: wireQuestions };
      validateJson(payload, MAX_REQUEST_BYTES, "invalid_request");
      body = JSON.stringify(payload);
      if (Buffer.byteLength(body, "utf8") > MAX_REQUEST_BYTES) fail("request_too_large");
      // Validation later compares against the immutable payload snapshot actually sent.
      const snapshot = JSON.parse(body) as typeof payload;
      canonical = snapshot.questions;
      hash = createHash("sha256").update(stableJson(snapshot)).digest("hex");
    } catch (error) {
      return unavailable(requestId, started, error instanceof ClientFailure ? error.code : "invalid_request");
    }
    if (this.#offlineMode) return unavailable(requestId, started, "offline");
    if (!this.isConfigured) return unavailable(requestId, started, "missing_key");
    if (performance.now() - started >= this.#timeoutMs) return unavailable(requestId, started, "timeout");
    // Cache/in-flight entries retain wire IDs so aliases cannot return a previous
    // caller's identifier. Never mutate a batch shared with another invocation.
    const restoreIds = (batch: DecisionBatch): DecisionBatch => ({
      ...structuredClone(batch), decisions: Object.fromEntries(Object.entries(batch.decisions).map(([id, decision]) => [originalIds.get(id)!, structuredClone(decision)])),
    });
    const fromCache = (batch: DecisionBatch): DecisionBatch => ({
      ...restoreIds(batch), source: "cache", usage: { input_tokens: 0, output_tokens: 0 },
      attempts: 0, request_id: requestId, latency_ms: Math.max(0, performance.now() - started),
    });
    if (this.#cacheEnabled) {
      const existing = this.#cache.get(hash);
      if (existing) {
        this.#cache.delete(hash);
        this.#cache.set(hash, existing);
        return fromCache(existing);
      }
      const pending = this.#inFlight.get(hash);
      if (pending) {
        const batch = await pending;
        return batch.status === "ok" ? fromCache(batch) : { ...structuredClone(batch), attempts: 0, request_id: requestId, latency_ms: performance.now() - started };
      }
    }
    const pending = this.#request(body, canonical, started, requestId);
    if (this.#cacheEnabled) this.#inFlight.set(hash, pending);
    try {
      const batch = await pending;
      if (this.#cacheEnabled && batch.status === "ok") {
        this.#cache.set(hash, structuredClone(batch));
        if (this.#cache.size > 128) this.#cache.delete(this.#cache.keys().next().value!);
      }
      return restoreIds(batch);
    } finally { if (this.#cacheEnabled) this.#inFlight.delete(hash); }
  }

  async #request(body: string, questions: Record<string, NativeQuestion>, started: number, requestId: string): Promise<DecisionBatch> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), Math.max(1, this.#timeoutMs - (performance.now() - started)));
    let attempts = 0;
    try {
      for (;;) {
        try {
          if (controller.signal.aborted || performance.now() - started >= this.#timeoutMs) fail("timeout");
          attempts += 1;
          let response: Response;
          try {
            response = await beforeDeadline(this.#fetch(DEFAULT_TYPESAFE_ENDPOINT, {
              method: "POST", headers: { "Content-Type": "application/json", "Authorization": `Bearer ${this.#apiKey}`, "User-Agent": "jev-decision-ts/0.3.0" },
              body, signal: controller.signal, redirect: "manual", credentials: "omit",
            }), controller.signal);
          } catch (error) {
            if (error instanceof ClientFailure) throw error;
            throw new ClientFailure(controller.signal.aborted ? "timeout" : "transport_error", !controller.signal.aborted);
          }
          if (response.redirected || (response.url && response.url !== DEFAULT_TYPESAFE_ENDPOINT) || (response.status >= 300 && response.status < 400)) {
            void discard(response); fail("redirect_rejected");
          }
          if (response.status !== 200) {
            void discard(response);
            const code = response.status === 401 || response.status === 403 ? "authentication_error" : response.status === 408 ? "timeout" : response.status === 429 ? "rate_limited" : "provider_error";
            throw new ClientFailure(code, [408, 429, 500, 502, 503, 504, 529].includes(response.status), retryAfterMilliseconds(response.headers.get("retry-after")));
          }
          let data: unknown;
          try { data = await readResponse(response, controller.signal); }
          catch (error) { void discard(response); throw error; }
          const parsed = parseResponse(data, questions);
          if (controller.signal.aborted || performance.now() - started >= this.#timeoutMs) fail("timeout");
          // Failed earlier attempts may still have been billed; never report the final
          // response's token counts as a known total across an uncertain retry.
          const usage = attempts === 1 ? parsed.usage : { input_tokens: null, output_tokens: null };
          const reportedInput = isRecord(data) && isRecord(data.usage) ? data.usage.input_tokens : undefined;
          if (typeof reportedInput === "number" && Number.isInteger(reportedInput) && reportedInput > MAX_INPUT_TOKENS) {
            // An unsafe integer becomes unknown telemetry, but its clear overrun
            // must still prevent exposing or caching an anomalous answer.
            return { ...unavailable(requestId, started, "invalid_response", attempts), usage };
          }
          return { status: "ok", source: "provider", ...parsed, usage, requested_model: DEFAULT_MODEL, latency_ms: performance.now() - started, attempts, request_id: requestId, error_code: null, is_fallback: false };
        } catch (error) {
          const failure = error instanceof ClientFailure ? error : new ClientFailure("invalid_response");
          if (!failure.transient || attempts >= 2 || controller.signal.aborted) throw failure;
          const delay = Math.max(50 + Math.random() * 50, failure.retryAfterMs ?? 0);
          if (delay >= this.#timeoutMs - (performance.now() - started)) throw failure;
          await beforeDeadline(new Promise<void>(resolve => setTimeout(resolve, delay)), controller.signal);
        }
      }
    } catch (error) {
      return unavailable(requestId, started, error instanceof ClientFailure ? error.code : "transport_error", attempts);
    } finally { clearTimeout(timer); }
  }
}

/** Compatibility shim. Offline execution never fabricates model probabilities. */
export function evaluateHeuristics(_state: State, _questions: Questions): DecisionBatch {
  return unavailable(randomUUID(), performance.now(), "offline");
}

export interface CommandRiskAssessment {
  status: DecisionBatch["status"];
  source: DecisionBatch["source"];
  advisory: true;
  execution_authority: "none";
  category: string | null;
  risk_probability: number | null;
  error_code: ErrorCode | null;
}
/** Advisory assessment only. The caller's existing authorization controls remain authoritative. */
export async function guardBashCommand(command: string, cwd = "", client: JevEvaluator = new JevClient()): Promise<CommandRiskAssessment> {
  const batch = await client.evaluate({ command, cwd }, [
    { id: "material_risk", type: "noul", prompt: "Does this command present a material risk of destructive changes, data disclosure, or effects outside the stated working directory?", criteria: { true: "The command may destroy or overwrite valuable data, disclose secrets, or perform external side effects.", false: "The available evidence indicates inspection or bounded local work without those material risks." } },
    { id: "category", type: "choice", prompt: "Classify the command's apparent effects. Assess behavior only; this answer grants no permission to execute.", criteria: { read_only: "Reads or inspects existing local data without modifying it.", compile_test: "Builds or tests local code and may write bounded build artifacts.", destructive_or_leak: "May delete or overwrite important data, expose secrets, or mutate external systems.", uncertain: "The command or relevant context is insufficient to determine its effects." } },
  ]);
  const risk = batch.decisions.material_risk;
  const category = batch.decisions.category;
  return { status: batch.status, source: batch.source, advisory: true, execution_authority: "none", category: batch.status === "ok" && category?.type === "choice" ? category.selected : null, risk_probability: batch.status === "ok" && risk?.type === "noul" ? risk.probability : null, error_code: batch.error_code };
}

export interface CompletionAssessment {
  status: DecisionBatch["status"];
  source: DecisionBatch["source"];
  advisory: true;
  task_success_authority: "none";
  evidence_probability: number | null;
  error_code: ErrorCode | null;
}
/** Review reported completion evidence; never marks a task successful or stops a loop. */
export async function verifyTurnCompletion(state: State, client: JevEvaluator = new JevClient()): Promise<CompletionAssessment> {
  const batch = await client.evaluate(state, [{ id: "completion_evidence", type: "noul", prompt: "Does the provided evidence substantiate every explicitly stated task acceptance criterion? This is an advisory evidence assessment, not a task-success decision.", criteria: { true: "Each stated criterion has specific supporting verification evidence and no unresolved contradiction.", false: "At least one criterion is unmet, contradicted, unspecified, or lacks verification evidence." } }]);
  const evidence = batch.decisions.completion_evidence;
  return { status: batch.status, source: batch.source, advisory: true, task_success_authority: "none", evidence_probability: batch.status === "ok" && evidence?.type === "noul" ? evidence.probability : null, error_code: batch.error_code };
}
