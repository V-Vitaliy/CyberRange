import { useState, useEffect, useCallback, useRef } from 'react';
import { ApiError } from '@/api/client';
import {
  CHAT_FAILED_MESSAGE, ChatMessage, ChatThread, ChatTurn,
  applyChatEvent, emptyTurn, listThreads, newThreadId, streamChat
} from '@/api/chat';

export function useRedTeamChat(token: string, sessionId: string, onUnauthorized: () => void) {
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [threadId, setThreadId] = useState(newThreadId);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [turn, setTurn] = useState<ChatTurn | null>(null);
  const [error, setError] = useState('');
  const pending = useRef<AbortController | null>(null);
  const mounted = useRef<AbortController | null>(null);

  const handleError = useCallback((err: unknown) => {
    if (err instanceof ApiError && err.status === 401) onUnauthorized();
    setError(err instanceof Error ? err.message : CHAT_FAILED_MESSAGE);
  }, [onUnauthorized]);

  // Thread loads belong to one mount: once it ends, a late reply or 401 must not reach a newer login.
  const loadThreads = useCallback((signal: AbortSignal) => {
    listThreads(token, sessionId, signal).then(
      list => { if (!signal.aborted) setThreads(list); },
      err => { if (!signal.aborted) handleError(err); }
    );
  }, [token, sessionId, handleError]);

  useEffect(() => {
    const controller = new AbortController();
    mounted.current = controller;
    loadThreads(controller.signal);
    return () => {
      controller.abort();
      pending.current?.abort();
    };
  }, [loadThreads]);

  const selectThread = (thread: ChatThread) => {
    if (turn) return;
    setThreadId(thread.id);
    setMessages(thread.messages);
    setError('');
  };

  const startThread = () => {
    if (turn) return;
    setThreadId(newThreadId());
    setMessages([]);
    setError('');
  };

  const send = async (prompt: string) => {
    if (turn || !prompt.trim()) return;
    const controller = new AbortController();
    pending.current = controller;
    setMessages(current => [...current, { role: 'user', content: prompt }]);
    setError('');
    let current = emptyTurn();
    setTurn(current);
    try {
      for await (const event of streamChat(token, { sessionId, threadId, prompt }, controller.signal)) {
        current = applyChatEvent(current, event);
        setTurn(current);
      }
      if (current.error) setError(current.error);
    } catch (err) {
      if (!controller.signal.aborted) handleError(err);
    } finally {
      if (!controller.signal.aborted) {
        const answer = current.answer;
        if (answer) setMessages(list => [...list, { role: 'ai', content: answer }]);
        setTurn(null);
        if (mounted.current) loadThreads(mounted.current.signal);
      }
      pending.current = null;
    }
  };

  const shown = turn?.answer ? [...messages, { role: 'ai' as const, content: turn.answer }] : messages;

  return { threads, threadId, messages: shown, step: turn?.step ?? null, sending: Boolean(turn), error,
    send, selectThread, startThread };
}
