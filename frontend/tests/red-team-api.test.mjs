import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { ApiError } from '../src/api/client.ts';
import { RED_TOKEN_KEY, clearRedToken, fetchRedProfile, getRedToken, loginRedTeam } from '../src/api/red-team.ts';

const originalFetch = globalThis.fetch;
let stored;

beforeEach(() => {
  stored = new Map([['auth_token', 'blue-token']]);
  globalThis.localStorage = {
    getItem: key => stored.get(key) ?? null,
    setItem: (key, value) => stored.set(key, String(value)),
    removeItem: key => stored.delete(key)
  };
});
afterEach(() => {
  globalThis.fetch = originalFetch;
  delete globalThis.localStorage;
});

test('login posts username, password and lab code and keeps the token', async () => {
  let sent;
  globalThis.fetch = async (url, options) => {
    sent = { url, options };
    return Response.json({ access_token: 'red-token', token_type: 'bearer', role: 'red_team' });
  };
  assert.equal(await loginRedTeam('red_attacker', 'secret', 'LAB-CODE'), 'red-token');
  assert.match(sent.url, /\/api\/red\/login$/);
  assert.equal(sent.options.method, 'POST');
  assert.equal(sent.options.headers['Content-Type'], 'application/json');
  assert.deepEqual(JSON.parse(sent.options.body), { username: 'red_attacker', password: 'secret', lab_code: 'LAB-CODE' });
  assert.equal(getRedToken(), 'red-token');
  assert.equal(stored.get(RED_TOKEN_KEY), 'red-token');
});

test('rejected login shows the server message and stores nothing', async () => {
  globalThis.fetch = async () => Response.json({ detail: 'Invalid credentials' }, { status: 401 });
  await assert.rejects(loginRedTeam('red_attacker', 'wrong', 'LAB-CODE'), err => err instanceof ApiError && err.message === 'Invalid credentials');
  assert.equal(getRedToken(), null);
});

test('profile is requested with the bearer token and returns the lab session', async () => {
  const profile = { username: 'red_attacker', role: 'red_team', session_id: 'session-1' };
  let sent;
  globalThis.fetch = async (url, options) => {
    sent = { url, options };
    return Response.json(profile);
  };
  assert.deepEqual(await fetchRedProfile('red-token'), profile);
  assert.match(sent.url, /\/api\/red\/me$/);
  assert.equal(sent.options.headers.Authorization, 'Bearer red-token');
});

test('a 401 from the profile clears the stored token', async () => {
  stored.set(RED_TOKEN_KEY, 'expired-token');
  globalThis.fetch = async () => Response.json({ detail: 'Invalid token; please re-login' }, { status: 401 });
  await assert.rejects(fetchRedProfile('expired-token'), err => err instanceof ApiError && err.status === 401 && err.message === 'Invalid token; please re-login');
  assert.equal(getRedToken(), null);
});

test('other profile errors keep the token', async () => {
  stored.set(RED_TOKEN_KEY, 'red-token');
  globalThis.fetch = async () => new Response('proxy failed', { status: 502 });
  await assert.rejects(fetchRedProfile('red-token'), /HTTP 502/);
  assert.equal(getRedToken(), 'red-token');
});

test('logout clears only the Red Team token', () => {
  stored.set(RED_TOKEN_KEY, 'red-token');
  clearRedToken();
  assert.equal(getRedToken(), null);
  assert.equal(stored.get('auth_token'), 'blue-token');
});
