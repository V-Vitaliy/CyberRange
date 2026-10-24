import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { ApiError, requestJson } from '../src/api/blue-team.ts';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

test('returns server budget and defense state', async () => {
  const result = { budget: 3, active_defenses: { jwt_filter_enabled: true } };
  globalThis.fetch = async () => Response.json(result);
  assert.deepEqual(await requestJson('http://test/dashboard'), result);
});

test('preserves authentication status and server explanation', async () => {
  globalThis.fetch = async () => Response.json({ detail: 'Token has expired' }, { status: 401 });
  await assert.rejects(requestJson('http://test/me'), err => err instanceof ApiError && err.status === 401 && err.message === 'Token has expired');
});

test('shows rejected purchase instead of treating it as success', async () => {
  globalThis.fetch = async () => Response.json({ detail: 'Insufficient funds' }, { status: 400 });
  await assert.rejects(requestJson('http://test/defenses/buy'), /Insufficient funds/);
});

test('handles non-JSON errors and invalid success responses', async () => {
  globalThis.fetch = async () => new Response('proxy failed', { status: 502 });
  await assert.rejects(requestJson('http://test/logs'), /HTTP 502/);
  globalThis.fetch = async () => new Response('not JSON', { status: 200 });
  await assert.rejects(requestJson('http://test/logs'), /invalid response/);
});
