import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import {
  backoffDelayMs,
  CircuitOpenError,
  fetchJsonResilient,
  fetchResilient,
  HtmlResponseError,
  HttpError,
  isPermanentFetchError,
  isPermanentStatus,
  isTransientStatus,
  resetResilientNetForTests,
  resilientNetConfig,
} from '../../../src/core/utils/resilient-net';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('resilient-net', () => {
  let realFetch: typeof fetch;

  beforeEach(() => {
    realFetch = globalThis.fetch;
    resetResilientNetForTests();
    resilientNetConfig.retries = 2;
    resilientNetConfig.baseDelayMs = 1;
    resilientNetConfig.maxDelayMs = 4;
    resilientNetConfig.breakerThreshold = 3;
    resilientNetConfig.breakerCooldownMs = 80;
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
    resetResilientNetForTests();
  });

  it('classifies permanent vs transient statuses', () => {
    expect(isPermanentStatus(404)).toBe(true);
    expect(isPermanentStatus(410)).toBe(true);
    expect(isPermanentStatus(401)).toBe(true);
    expect(isTransientStatus(500)).toBe(true);
    expect(isTransientStatus(503)).toBe(true);
    expect(isTransientStatus(429)).toBe(true);
    expect(isTransientStatus(408)).toBe(true);
    expect(isPermanentStatus(500)).toBe(false);
    expect(isTransientStatus(404)).toBe(false);
  });

  it('backoff grows with the attempt index and stays within bounds', () => {
    const first = backoffDelayMs(0, 100, 10_000);
    const late = backoffDelayMs(5, 100, 10_000);
    const capped = backoffDelayMs(20, 100, 300);
    expect(first).toBeGreaterThanOrEqual(100);
    expect(first).toBeLessThanOrEqual(200);
    expect(late).toBeLessThanOrEqual(10_000);
    expect(capped).toBeLessThanOrEqual(300);
  });

  it('returns the response on the happy path without retrying', async () => {
    let fetches = 0;
    globalThis.fetch = (async () => {
      fetches += 1;
      return jsonResponse({ ok: true });
    }) as unknown as typeof fetch;

    const res = await fetchResilient('https://cdn.example.com/manifest.json');
    expect(res.status).toBe(200);
    expect(fetches).toBe(1);
  });

  it('retries transient 5xx and succeeds when the origin recovers', async () => {
    let fetches = 0;
    globalThis.fetch = (async () => {
      fetches += 1;
      return fetches < 3
        ? new Response('boom', { status: 503 })
        : jsonResponse({ ok: true });
    }) as unknown as typeof fetch;

    const res = await fetchResilient('https://cdn.example.com/manifest.json');
    expect(res.status).toBe(200);
    expect(fetches).toBe(3);
  });

  it('fails fast on permanent 404 without retrying', async () => {
    let fetches = 0;
    globalThis.fetch = (async () => {
      fetches += 1;
      return new Response('missing', { status: 404 });
    }) as unknown as typeof fetch;

    const err = await fetchResilient(
      'https://cdn.example.com/absent.json'
    ).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).status).toBe(404);
    expect(fetches).toBe(1);
  });

  it('retries network-level rejections (connection reset)', async () => {
    let fetches = 0;
    globalThis.fetch = (async () => {
      fetches += 1;
      if (fetches < 2) throw new TypeError('connection reset');
      return jsonResponse({ ok: true });
    }) as unknown as typeof fetch;

    const res = await fetchResilient('https://cdn.example.com/asset.glb');
    expect(res.status).toBe(200);
    expect(fetches).toBe(2);
  });

  it('times out a hanging fetch instead of stalling forever', async () => {
    let fetches = 0;
    globalThis.fetch = (async () => {
      fetches += 1;
      // Ignores the abort signal on purpose — must still be raced away.
      return await new Promise<Response>(() => {});
    }) as unknown as typeof fetch;

    await expect(
      fetchResilient('https://cdn.example.com/slow.json', undefined, {
        timeoutMs: 15,
        retries: 0,
      })
    ).rejects.toThrow(/timeout after 15ms/);
    expect(fetches).toBe(1);
  });

  it('opens the circuit after consecutive transient failures and fast-fails', async () => {
    resilientNetConfig.retries = 0;
    let fetches = 0;
    globalThis.fetch = (async () => {
      fetches += 1;
      return new Response('boom', { status: 500 });
    }) as unknown as typeof fetch;

    for (let i = 0; i < 3; i++) {
      await expect(
        fetchResilient('https://dead.example.com/asset.glb')
      ).rejects.toBeInstanceOf(HttpError);
    }
    const before = fetches;
    await expect(
      fetchResilient('https://dead.example.com/asset.glb')
    ).rejects.toBeInstanceOf(CircuitOpenError);
    expect(fetches).toBe(before);
  });

  it('half-open probe closes the breaker after the cooldown', async () => {
    resilientNetConfig.retries = 0;
    let status = 500;
    let _fetches = 0;
    globalThis.fetch = (async () => {
      _fetches += 1;
      return new Response('x', { status });
    }) as unknown as typeof fetch;

    for (let i = 0; i < 3; i++) {
      await fetchResilient('https://flap.example.com/a.glb').catch(() => {});
    }
    await expect(
      fetchResilient('https://flap.example.com/a.glb')
    ).rejects.toBeInstanceOf(CircuitOpenError);

    await new Promise((resolve) => setTimeout(resolve, 90));
    status = 200;
    const res = await fetchResilient('https://flap.example.com/a.glb');
    expect(res.status).toBe(200);
    // Breaker closed: traffic flows without short-circuit again.
    const res2 = await fetchResilient('https://flap.example.com/a.glb');
    expect(res2.status).toBe(200);
  });

  it('does not count 404s against the origin breaker', async () => {
    resilientNetConfig.retries = 0;
    let fetches = 0;
    globalThis.fetch = (async () => {
      fetches += 1;
      return new Response('missing', { status: 404 });
    }) as unknown as typeof fetch;
    for (let i = 0; i < 5; i++) {
      await fetchResilient('https://ok.example.com/miss.glb').catch(() => {});
    }
    // 5 permanent failures would have opened the breaker — the 6th call
    // still reaches fetch instead of short-circuiting.
    await fetchResilient('https://ok.example.com/miss.glb').catch(() => {});
    expect(fetches).toBe(6);
  });

  it('a permanent status during a half-open probe frees the probe slot', async () => {
    resilientNetConfig.retries = 0;
    let status = 500;
    globalThis.fetch = (async () =>
      new Response('x', { status })) as unknown as typeof fetch;

    for (let i = 0; i < 3; i++) {
      await fetchResilient('https://probe.example.com/a.glb').catch(() => {});
    }
    await expect(
      fetchResilient('https://probe.example.com/a.glb')
    ).rejects.toBeInstanceOf(CircuitOpenError);

    await new Promise((resolve) => setTimeout(resolve, 90));
    // Probe answers 404: URL is wrong, but the probe slot must be released —
    // the next request probes again instead of wedging in CircuitOpenError.
    status = 404;
    await expect(
      fetchResilient('https://probe.example.com/a.glb')
    ).rejects.toBeInstanceOf(HttpError);

    status = 200;
    const res = await fetchResilient('https://probe.example.com/a.glb');
    expect(res.status).toBe(200);
  });

  it('fetchJsonResilient retries a body read that fails mid-transfer', async () => {
    let fetches = 0;
    globalThis.fetch = (async () => {
      fetches += 1;
      if (fetches === 1) {
        const res = jsonResponse({ ok: true });
        return {
          ok: true,
          status: 200,
          headers: res.headers,
          json: () =>
            Promise.reject(new TypeError('net::ERR_CONNECTION_RESET')),
        } as unknown as Response;
      }
      return jsonResponse({ ok: true });
    }) as unknown as typeof fetch;

    const payload = await fetchJsonResilient('https://cdn.example.com/m.json');
    expect(payload).toEqual({ ok: true });
    expect(fetches).toBe(2);
  });

  it('treats an HTML 200 (SPA fallback) as a permanent failure, not a parse retry', async () => {
    let fetches = 0;
    globalThis.fetch = (async () => {
      fetches += 1;
      return new Response('<!doctype html><html></html>', {
        status: 200,
        headers: { 'Content-Type': 'text/html' },
      });
    }) as unknown as typeof fetch;

    const err = await fetchJsonResilient(
      'https://cdn.example.com/gameassets_handoff.json'
    ).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(HtmlResponseError);
    expect(isPermanentFetchError(err)).toBe(true);
    expect(fetches).toBe(1);
  });
});
