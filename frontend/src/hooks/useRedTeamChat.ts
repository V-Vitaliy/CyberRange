import { useState, useEffect, useCallback, useRef } from 'react';
import { ApiError } from '@/api/client';
import {
  CHAT_FAILED_MESSAGE, ChatMessage, ChatThread, ChatTurn,
  applyChatEvent, createThreadLoader, emptyTurn, newThreadId, streamChat
} from '@/api/chat';

export function useRedTeamChat(token: string, sessionId: string, onUnauthorized: () => void) {
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [threadId, setThreadId] = useState(newThreadId);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [turn, setTurn] = useState<ChatTurn | null>(null);
  const [error, setError] = useState('');
  const pending = useRef<AbortController | null>(null);
  const loader = useRef<ReturnType<typeof createThreadLoader> | null>(null);

  const handleError = useCallback((err: unknown) => {
    if (err instanceof ApiError && err.status === 401) onUnauthorized();
    setError(err instanceof Error ? err.message : CHAT_FAILED_MESSAGE);
  }, [onUnauthorized]);

  useEffect(() => {
    const threadLoader = createThreadLoader(token, sessionId, setThreads, handleError);
    loader.current = threadLoader;
    void threadLoader.load();
    return () => {
      threadLoader.cancel();
      pending.current?.abort();
    };
  }, [token, sessionId, handleError]);

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
        void loader.current?.load();
      }
      pending.current = null;
    }
  };

  const shown = turn?.answer ? [...messages, { role: 'ai' as const, content: turn.answer }] : messages;

  return { threads, threadId, messages: shown, step: turn?.step ?? null, sending: Boolean(turn), error,
    send, selectThread, startThread };
}
