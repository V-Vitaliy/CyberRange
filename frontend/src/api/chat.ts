import { errorFromResponse, requestJson } from './client.ts';
import { RED_API_BASE, forgetRejectedToken } from './red-team.ts';

export const CHAT_FAILED_MESSAGE = 'The assistant could not answer. Please try again.';

export interface AgentStep {
  step: number;
  tool: string;
  arguments: string;
  outcome: string;
}

export interface ChatMessage {
  role: 'user' | 'ai';
  content: string;
}

export interface ChatThread {
  id: string;
  title: string;
  updated_at: string | null;
  messages: ChatMessage[];
}

export type ChatEvent =
  | { type: 'text'; text: string }
  | { type: 'step'; step: AgentStep }
  | { type: 'error'; message: string };

export interface ChatTurn {
  answer: string;
  step: AgentStep | null;
  error: string;
}

export interface ChatRequest {
  sessionId: string;
  threadId: string;
  prompt: string;
}

const failed: ChatEvent = { type: 'error', message: CHAT_FAILED_MESSAGE };

function parseData(payload: string): ChatEvent | null {
  let data: unknown;
  try {
    data = JSON.parse(payload);
  } catch {
    return null;
  }
  if (!data || typeof data !== 'object') return null;
  const event = data as { text?: unknown; step?: unknown; error?: unknown };
  if (typeof event.text === 'string') return { type: 'text', text: event.text };
  if (event.step && typeof event.step === 'object') return { type: 'step', step: event.step as AgentStep };
  // The server already sends a generic message; never show anything more specific.
  if ('error' in event) return failed;
  return null;
}

export async function* readChatEvents(stream: ReadableStream<Uint8Array>): AsyncGenerator<ChatEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split(/\r?\n/);
      buffer = done ? '' : lines.pop() ?? '';
      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        const payload = line.slice(5).trim();
        if (payload === '[DONE]') return;
        const event = parseData(payload);
        if (event) yield event;
        if (event?.type === 'error') return;
      }
      if (done) break;
    }
  } catch {
    // A dropped connection is reported below like a stream cut before [DONE].
  } finally {
    reader.releaseLock();
  }
  yield failed;
}

export async function* streamChat(token: string, request: ChatRequest, signal?: AbortSignal): AsyncGenerator<ChatEvent> {
  let response: Response;
  try {
    response = await fetch(`${RED_API_BASE}/chat/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ prompt: request.prompt, session_id: request.sessionId, thread_id: request.threadId }),
      signal
    });
  } catch {
    throw new Error(CHAT_FAILED_MESSAGE);
  }
  if (!response.ok) {
    const err = errorFromResponse(response, await response.json().catch(() => null));
    forgetRejectedToken(err, token);
    throw err;
  }
  if (!response.body) throw new Error(CHAT_FAILED_MESSAGE);
  yield* readChatEvents(response.body);
}

export async function listThreads(token: string, sessionId: string, signal?: AbortSignal): Promise<ChatThread[]> {
  try {
    return await requestJson<ChatThread[]>(`${RED_API_BASE}/chat/threads?session_id=${encodeURIComponent(sessionId)}`, {
      headers: { Authorization: `Bearer ${token}` },
      signal
    });
  } catch (err) {
    forgetRejectedToken(err, token);
    throw err;
  }
}

// crypto.randomUUID exists only in secure contexts; a lab may be served over plain HTTP.
export function newThreadId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function emptyTurn(): ChatTurn {
  return { answer: '', step: null, error: '' };
}

export function applyChatEvent(turn: ChatTurn, event: ChatEvent): ChatTurn {
  if (event.type === 'text') return { ...turn, answer: turn.answer + event.text };
  if (event.type === 'step') return { ...turn, step: event.step };
  return { ...turn, error: event.message };
}
