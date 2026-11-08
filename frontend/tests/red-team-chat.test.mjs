import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import ts from 'typescript';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ApiError } from '../src/api/client.ts';
import { RED_TOKEN_KEY } from '../src/api/red-team.ts';
import { CHAT_FAILED_MESSAGE, applyChatEvent, createThreadLoader, emptyTurn, listThreads, newThreadId, readChatEvents, streamChat } from '../src/api/chat.ts';

const originalFetch = globalThis.fetch;
let stored;

beforeEach(() => {
  stored = new Map();
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

function streamOf(...chunks) {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    }
  });
}

async function collect(iterable) {
  const events = [];
  for await (const event of iterable) events.push(event);
  return events;
}

const step = { step: 1, tool: 'send_email', arguments: '{"to": "a@lab.local"}', outcome: 'sent' };

test('parser joins lines split across chunks and keeps their order', async () => {
  const events = await collect(readChatEvents(streamOf('data: {"te', 'xt": "Hel"}\n\ndata: {"text": "lo"}\n', '\ndata: [DONE]\n\n')));
  assert.deepEqual(events, [{ type: 'text', text: 'Hel' }, { type: 'text', text: 'lo' }]);
});

test('parser keeps agent steps apart from the answer text', async () => {
  const body = `data: {"text": "Sending"}\n\ndata: ${JSON.stringify({ step })}\n\ndata: {"text": " done"}\n\ndata: [DONE]\n\n`;
  const events = await collect(readChatEvents(streamOf(body)));
  assert.deepEqual(events, [{ type: 'text', text: 'Sending' }, { type: 'step', step }, { type: 'text', text: ' done' }]);
  const text = events.filter(e => e.type === 'text').map(e => e.text).join('');
  assert.equal(text, 'Sending done');
});

test('parser reports a stream error event and stops at DONE', async () => {
  const body = 'data: {"error": "The assistant could not answer. Please try again."}\n\ndata: [DONE]\n\ndata: {"text": "late"}\n\n';
  const events = await collect(readChatEvents(streamOf(body)));
  assert.deepEqual(events, [{ type: 'error', message: CHAT_FAILED_MESSAGE }]);
});

test('parser treats a stream cut before DONE as a failure', async () => {
  const events = await collect(readChatEvents(streamOf('data: {"text": "partial"}\n\n')));
  assert.deepEqual(events, [{ type: 'text', text: 'partial' }, { type: 'error', message: CHAT_FAILED_MESSAGE }]);
});

test('parser accepts CRLF lines and skips comments and malformed data', async () => {
  const body = ': keep-alive\r\n\r\ndata: not json\r\n\r\ndata: {"unknown": 1}\r\n\r\ndata: {"text": "ok"}\r\n\r\ndata: [DONE]\r\n\r\n';
  const events = await collect(readChatEvents(streamOf(body)));
  assert.deepEqual(events, [{ type: 'text', text: 'ok' }]);
});

test('a turn shows the current agent step and keeps it out of the answer', () => {
  const second = { ...step, step: 2, tool: 'search_documents' };
  const events = [
    { type: 'text', text: 'Let me check. ' },
    { type: 'step', step },
    { type: 'step', step: second },
    { type: 'text', text: 'Done.' }
  ];
  const turns = [];
  let turn = emptyTurn();
  for (const event of events) {
    turn = applyChatEvent(turn, event);
    turns.push(turn);
  }
  assert.deepEqual(turns[1].step, step);
  assert.deepEqual(turns[2].step, second);
  assert.equal(turn.answer, 'Let me check. Done.');
  assert.equal(turn.error, '');
});

test('a stream error ends the turn with the generic message', () => {
  const turn = applyChatEvent(applyChatEvent(emptyTurn(), { type: 'text', text: 'partial' }), { type: 'error', message: CHAT_FAILED_MESSAGE });
  assert.equal(turn.answer, 'partial');
  assert.equal(turn.error, CHAT_FAILED_MESSAGE);
});

test('ask posts the prompt, session and thread with the bearer token', async () => {
  let sent;
  globalThis.fetch = async (url, options) => {
    sent = { url, options };
    return new Response(streamOf('data: {"text": "hi"}\n\ndata: [DONE]\n\n'), { headers: { 'Content-Type': 'text/event-stream' } });
  };
  const events = await collect(streamChat('red-token', { sessionId: 'session-1', threadId: 'thread-1', prompt: 'hello' }));
  assert.deepEqual(events, [{ type: 'text', text: 'hi' }]);
  assert.match(sent.url, /\/api\/red\/chat\/ask$/);
  assert.equal(sent.options.method, 'POST');
  assert.equal(sent.options.headers.Authorization, 'Bearer red-token');
  assert.deepEqual(JSON.parse(sent.options.body), { prompt: 'hello', session_id: 'session-1', thread_id: 'thread-1' });
});

test('ask shows the server message of a rejected request', async () => {
  globalThis.fetch = async () => Response.json({ detail: 'Too many requests' }, { status: 429 });
  await assert.rejects(collect(streamChat('red-token', { sessionId: 's', threadId: 't', prompt: 'p' })),
    err => err instanceof ApiError && err.status === 429 && err.message === 'Too many requests');
});

test('ask 401 clears the token it was sent with', async () => {
  stored.set(RED_TOKEN_KEY, 'red-token');
  globalThis.fetch = async () => Response.json({ detail: 'Invalid token; please re-login' }, { status: 401 });
  await assert.rejects(collect(streamChat('red-token', { sessionId: 's', threadId: 't', prompt: 'p' })), err => err.status === 401);
  assert.equal(stored.get(RED_TOKEN_KEY), undefined);
});

test('ask network failure shows a generic message', async () => {
  globalThis.fetch = async () => { throw new TypeError('fetch failed: ECONNREFUSED 127.0.0.1:8001'); };
  await assert.rejects(collect(streamChat('red-token', { sessionId: 's', threadId: 't', prompt: 'p' })),
    err => err.message === CHAT_FAILED_MESSAGE);
});

test('thread list comes from the API for the session', async () => {
  const threads = [{ id: 'thread-1', title: 'hello', updated_at: '2026-11-08T12:00:00Z', messages: [] }];
  let sent;
  globalThis.fetch = async (url, options) => {
    sent = { url, options };
    return Response.json(threads);
  };
  assert.deepEqual(await listThreads('red-token', 'session 1'), threads);
  assert.match(sent.url, /\/api\/red\/chat\/threads\?session_id=session%201$/);
  assert.equal(sent.options.headers.Authorization, 'Bearer red-token');
});

test('thread list request is cancelled by its signal', async () => {
  stored.set(RED_TOKEN_KEY, 'red-token');
  globalThis.fetch = (url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(options.signal.reason));
  });
  const controller = new AbortController();
  const request = listThreads('red-token', 'session-1', controller.signal);
  controller.abort();
  await assert.rejects(request, err => err.name === 'AbortError');
  assert.equal(stored.get(RED_TOKEN_KEY), 'red-token');
});

// Replies are released by the test in any order; the signal is ignored like a reply already on the wire.
function heldFetch() {
  const replies = [];
  globalThis.fetch = () => new Promise(resolve => replies.push(resolve));
  return replies;
}

test('only the latest thread list is applied', async () => {
  const replies = heldFetch();
  const applied = [];
  const loader = createThreadLoader('red-token', 'session-1', list => applied.push(list), assert.fail);
  const first = loader.load();
  const second = loader.load();
  replies[1](Response.json([{ id: 'new', title: 'new', updated_at: null, messages: [] }]));
  await second;
  replies[0](Response.json([]));
  await first;
  assert.deepEqual(applied.map(list => list.map(t => t.id)), [['new']]);
});

test('a cancelled loader ignores a late 401', async () => {
  stored.set(RED_TOKEN_KEY, 'newer-token');
  const replies = heldFetch();
  const errors = [];
  const loader = createThreadLoader('red-token', 'session-1', assert.fail, err => errors.push(err));
  const load = loader.load();
  loader.cancel();
  replies[0](Response.json({ detail: 'Invalid token; please re-login' }, { status: 401 }));
  await load;
  assert.deepEqual(errors, []);
  assert.equal(stored.get(RED_TOKEN_KEY), 'newer-token');
});

test('a loader reports the error of its current load', async () => {
  const replies = heldFetch();
  const errors = [];
  const loader = createThreadLoader('red-token', 'session-1', assert.fail, err => errors.push(err));
  const load = loader.load();
  replies[0](Response.json({ detail: 'Game session not found' }, { status: 404 }));
  await load;
  assert.equal(errors.length, 1);
  assert.equal(errors[0].status, 404);
});

test('new thread ids are distinct UUIDs generated on the client', () => {
  const first = newThreadId();
  assert.match(first, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.notEqual(newThreadId(), first);
});

// Node cannot load TSX, so the real component is transpiled with the project's TypeScript.
async function loadModule(relativePath) {
  const frontend = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
  const source = fs.readFileSync(path.join(frontend, relativePath), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 }
  });
  const dir = fs.mkdtempSync(path.join(frontend, '.render-'));
  const file = path.join(dir, 'component.mjs');
  fs.writeFileSync(file, outputText);
  try {
    return await import(pathToFileURL(file).href);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

test('model output is rendered as text, never as HTML', async () => {
  const { default: ChatMessages } = await loadModule('src/components/red-team/ChatMessages.tsx');
  const payload = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
  const html = renderToStaticMarkup(createElement(ChatMessages, {
    messages: [{ role: 'user', content: 'show me' }, { role: 'ai', content: payload }]
  }));
  assert.ok(html.includes('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&lt;script&gt;'), html);
  assert.ok(!/<img|<script/i.test(html), html);
});

test('the current agent step is shown as text while the answer streams', async () => {
  const { default: ChatMessages } = await loadModule('src/components/red-team/ChatMessages.tsx');
  const props = { messages: [{ role: 'user', content: 'mail it' }], stepLabel: 'Step' };
  const live = renderToStaticMarkup(createElement(ChatMessages, { ...props, step: { ...step, step: 2, tool: '<img src=x onerror=alert(1)>' } }));
  assert.ok(live.includes('Step 2: &lt;img src=x onerror=alert(1)&gt;'), live);
  assert.ok(!/<img/i.test(live), live);
  const unknown = renderToStaticMarkup(createElement(ChatMessages, { ...props, step: { ...step, step: 3, tool: 'delete_all_labs' } }));
  assert.ok(unknown.includes('Step 3: delete_all_labs'), unknown);
  const idle = renderToStaticMarkup(createElement(ChatMessages, { ...props, step: null }));
  assert.ok(!idle.includes('Step '), idle);
});

test('the step label and every chat text exist in English and Polish', async () => {
  const { DICTIONARY } = await loadModule('src/context/LanguageContext.tsx');
  assert.deepEqual(Object.keys(DICTIONARY.pl).sort(), Object.keys(DICTIONARY.en).sort());
  assert.equal(DICTIONARY.en.agentStep, 'Step');
  assert.equal(DICTIONARY.pl.agentStep, 'Krok');
});
