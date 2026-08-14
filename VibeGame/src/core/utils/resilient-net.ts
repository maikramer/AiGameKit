/**
 * Resilient HTTP access for every engine asset download.
 *
 * `fetch` alone fails silently under stress: hanging connections stall
 * loaders forever, one 502 poisons sticky caches for the whole session, and
 * retry loops hammer dead origins. This module layers four mechanisms on top
 * of the platform `fetch`:
 *
 * - per-attempt timeout (raced, so fetches that ignore the abort signal
 *   still time out)
 * - retry with exponential backoff + full jitter, restricted to transient
 *   failures (network errors, timeouts, 408/425/429/5xx) — permanent 4xx
 *   fail fast
 * - `Retry-After` honored for 429/503 when larger than the backoff
 * - a per-origin circuit breaker: after consecutive transient failures the
 *   origin is short-circuited for a cool-down window, then probed with a
 *   single half-open request before traffic resumes
 *
 * Body reads (`res.json()` / `res.arrayBuffer()`) can also fail mid-transfer
 * on connection resets, so prefer the `fetchJsonResilient` /
 * `fetchBytesResilient` wrappers — they retry the whole fetch+body cycle.
 */

export class HttpError extends Error {
  readonly status: number;
  readonly url: string;
  constructor(status: number, url: string) {
    super(`HTTP ${status}: ${url}`);
    this.name = 'HttpError';
    this.status = status;
    this.url = url;
  }
}

export class CircuitOpenError extends Error {
  readonly url: string;
  constructor(url: string, retryAtMs: number) {
    const wait = Math.max(0, retryAtMs - Date.now());
    super(
      `circuit open for origin of ${url} — retry in ${Math.ceil(wait / 1000)}s`
    );
    this.name = 'CircuitOpenError';
    this.url = url;
  }
}

/**
 * A 200 response whose body is a page, not data — the dev-server SPA fallback
 * for a missing JSON file. Retrying can never help: the URL is wrong, not the
 * origin, so callers treat it like a permanent 4xx.
 */
export class HtmlResponseError extends Error {
  readonly url: string;
  constructor(url: string) {
    super(`HTML response instead of data (SPA fallback?): ${url}`);
    this.name = 'HtmlResponseError';
    this.url = url;
  }
}

/** Mutable so tests can shrink delays; restored by resetResilientNetForTests. */
export const resilientNetConfig = {
  timeoutMs: 10_000,
  retries: 2,
  baseDelayMs: 200,
  maxDelayMs: 2_500,
  breakerThreshold: 5,
  breakerCooldownMs: 8_000,
  maxRetryAfterMs: 30_000,
};

export interface ResilientFetchOptions {
  timeoutMs?: number;
  retries?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
}

const PERMANENT_STATUS = new Set([400, 401, 403, 404, 405, 406, 409, 410, 422]);

export function isPermanentStatus(status: number): boolean {
  return PERMANENT_STATUS.has(status);
}

export function isTransientStatus(status: number): boolean {
  return status === 408 || status === 425 || status === 429 || status >= 500;
}

function isNetworkError(err: unknown): boolean {
  // fetch rejects with TypeError on network failure; AbortError is our timeout.
  return (
    err instanceof TypeError ||
    (err instanceof Error && err.name === 'AbortError')
  );
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Full-jitter exponential backoff between baseDelay and the capped ceiling. */
export function backoffDelayMs(
  attempt: number,
  baseDelayMs = resilientNetConfig.baseDelayMs,
  maxDelayMs = resilientNetConfig.maxDelayMs
): number {
  const ceiling = Math.min(maxDelayMs, baseDelayMs * 2 ** Math.max(0, attempt));
  return baseDelayMs + Math.random() * Math.max(0, ceiling - baseDelayMs);
}

function retryAfterMs(response: Response): number | null {
  const header =
    response.headers.get('retry-after') ?? response.headers.get('Retry-After');
  if (!header) return null;
  const seconds = Number(header);
  if (Number.isFinite(seconds) && seconds >= 0) {
    return Math.min(seconds * 1000, resilientNetConfig.maxRetryAfterMs);
  }
  const date = Date.parse(header);
  if (!Number.isNaN(date)) {
    return Math.min(
      Math.max(0, date - Date.now()),
      resilientNetConfig.maxRetryAfterMs
    );
  }
  return null;
}

// ── per-origin circuit breaker ──────────────────────────────────────────────

interface Breaker {
  failures: number;
  openUntilMs: number;
  probeInFlight: boolean;
}

const breakers = new Map<string, Breaker>();

function originOf(url: string): string {
  try {
    const base = typeof location !== 'undefined' ? location.href : undefined;
    return new URL(url, base).origin;
  } catch {
    return url;
  }
}

function acquireProbe(breaker: Breaker): boolean {
  if (breaker.openUntilMs === 0) return true;
  if (Date.now() < breaker.openUntilMs) return false;
  if (breaker.probeInFlight) return false;
  breaker.probeInFlight = true;
  return true;
}

function recordSuccess(breaker: Breaker): void {
  breaker.failures = 0;
  breaker.openUntilMs = 0;
  breaker.probeInFlight = false;
}

function recordFailure(breaker: Breaker): void {
  breaker.probeInFlight = false;
  breaker.failures += 1;
  if (breaker.failures >= resilientNetConfig.breakerThreshold) {
    breaker.openUntilMs = Date.now() + resilientNetConfig.breakerCooldownMs;
  }
}

export function breakerStateForUrl(url: string): {
  open: boolean;
  failures: number;
} {
  const breaker = breakers.get(originOf(url));
  if (!breaker) return { open: false, failures: 0 };
  return {
    open: Date.now() < breaker.openUntilMs,
    failures: breaker.failures,
  };
}

// ── fetch core ──────────────────────────────────────────────────────────────

function attemptFetch(
  url: string,
  init: RequestInit | undefined,
  timeoutMs: number
): Promise<Response> {
  const fetchImpl = globalThis.fetch;
  if (typeof fetchImpl !== 'function') {
    return Promise.reject(
      new Error('resilient fetch requires a global fetch implementation')
    );
  }
  const controller = new AbortController();
  const callerSignal = init?.signal;
  if (callerSignal) {
    if (callerSignal.aborted) controller.abort(callerSignal.reason);
    else
      callerSignal.addEventListener(
        'abort',
        () => controller.abort(callerSignal.reason),
        { once: true }
      );
  }
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeoutPromise = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new Error(`timeout after ${timeoutMs}ms: ${url}`));
    }, timeoutMs);
  });
  return Promise.race([
    fetchImpl(url, { ...init, signal: controller.signal }),
    timeoutPromise,
  ]).finally(() => clearTimeout(timer)) as Promise<Response>;
}

/**
 * Fetch with timeout, retry and circuit breaking. Rejects with `HttpError`
 * (permanent statuses carry `status`), `CircuitOpenError`, or the last
 * underlying error. `data:`/`blob:` URLs skip the network path entirely.
 */
export async function fetchResilient(
  url: string,
  init?: RequestInit,
  options?: ResilientFetchOptions
): Promise<Response> {
  if (url.startsWith('data:') || url.startsWith('blob:')) {
    return attemptFetch(
      url,
      init,
      options?.timeoutMs ?? resilientNetConfig.timeoutMs
    );
  }

  const retries = options?.retries ?? resilientNetConfig.retries;
  const timeoutMs = options?.timeoutMs ?? resilientNetConfig.timeoutMs;
  const baseDelayMs = options?.baseDelayMs ?? resilientNetConfig.baseDelayMs;
  const maxDelayMs = options?.maxDelayMs ?? resilientNetConfig.maxDelayMs;
  const breaker = breakers.get(originOf(url)) ?? {
    failures: 0,
    openUntilMs: 0,
    probeInFlight: false,
  };
  breakers.set(originOf(url), breaker);

  if (!acquireProbe(breaker)) {
    throw new CircuitOpenError(url, breaker.openUntilMs);
  }

  let lastError: unknown;
  for (let attempt = 0; attempt <= retries; attempt++) {
    let response: Response;
    try {
      response = await attemptFetch(url, init, timeoutMs);
    } catch (err) {
      lastError = err;
      if (!isNetworkError(err)) {
        recordFailure(breaker);
        throw err;
      }
      continue;
    }
    if (response.ok) {
      recordSuccess(breaker);
      return response;
    }
    if (isPermanentStatus(response.status)) {
      // A 404 says this URL is wrong, not that the origin is unhealthy. It
      // still answers a half-open probe, so release the slot — otherwise the
      // breaker stays wedged in probeInFlight forever.
      breaker.probeInFlight = false;
      throw new HttpError(response.status, url);
    }
    lastError = new HttpError(response.status, url);
    if (attempt < retries) {
      const wait =
        retryAfterMs(response) ??
        backoffDelayMs(attempt, baseDelayMs, maxDelayMs);
      await sleep(wait);
    }
  }
  recordFailure(breaker);
  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}

/** `fetchResilient` + `.json()`, retrying the whole cycle on body failures. */
export async function fetchJsonResilient(
  url: string,
  options?: ResilientFetchOptions
): Promise<unknown> {
  const { retries = resilientNetConfig.retries } = options ?? {};
  let lastError: unknown;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const response = await fetchResilient(url, undefined, options);
      const type = response.headers.get('content-type') ?? '';
      if (/text\/html/i.test(type)) throw new HtmlResponseError(url);
      return await response.json();
    } catch (err) {
      lastError = err;
      if (err instanceof HtmlResponseError) throw err;
      if (err instanceof HttpError && isPermanentStatus(err.status)) throw err;
      if (err instanceof CircuitOpenError) throw err;
      if (attempt < retries) {
        await sleep(
          backoffDelayMs(attempt, options?.baseDelayMs, options?.maxDelayMs)
        );
      }
    }
  }
  throw lastError;
}

/** `fetchResilient` + `.arrayBuffer()`, retrying the whole cycle on body failures. */
export async function fetchBytesResilient(
  url: string,
  options?: ResilientFetchOptions
): Promise<Uint8Array> {
  const { retries = resilientNetConfig.retries } = options ?? {};
  let lastError: unknown;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const response = await fetchResilient(url, undefined, options);
      return new Uint8Array(await response.arrayBuffer());
    } catch (err) {
      lastError = err;
      if (err instanceof HttpError && isPermanentStatus(err.status)) throw err;
      if (err instanceof CircuitOpenError) throw err;
      if (attempt < retries) {
        await sleep(
          backoffDelayMs(attempt, options?.baseDelayMs, options?.maxDelayMs)
        );
      }
    }
  }
  throw lastError;
}

/**
 * `fetchResilient` + `.blob()`, preserving the response content type (image
 * decoders such as Firefox's `createImageBitmap` rely on it for sniffing).
 */
export async function fetchBlobResilient(
  url: string,
  options?: ResilientFetchOptions
): Promise<Blob> {
  const { retries = resilientNetConfig.retries } = options ?? {};
  let lastError: unknown;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const response = await fetchResilient(url, undefined, options);
      return await response.blob();
    } catch (err) {
      lastError = err;
      if (err instanceof HttpError && isPermanentStatus(err.status)) throw err;
      if (err instanceof CircuitOpenError) throw err;
      if (attempt < retries) {
        await sleep(
          backoffDelayMs(attempt, options?.baseDelayMs, options?.maxDelayMs)
        );
      }
    }
  }
  throw lastError;
}

/** Distinguish "retry later helps" from "this URL will never work". */
export function isPermanentFetchError(err: unknown): boolean {
  return (
    (err instanceof HttpError && isPermanentStatus(err.status)) ||
    err instanceof HtmlResponseError
  );
}

export function resetResilientNetForTests(): void {
  breakers.clear();
  Object.assign(resilientNetConfig, {
    timeoutMs: 10_000,
    retries: 2,
    baseDelayMs: 200,
    maxDelayMs: 2_500,
    breakerThreshold: 5,
    breakerCooldownMs: 8_000,
    maxRetryAfterMs: 30_000,
  });
}
