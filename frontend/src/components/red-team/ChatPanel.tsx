"use client"
import React from 'react';
import { MessageSquarePlus, RefreshCw, Send } from 'lucide-react';

import { useLanguage } from '@/context/LanguageContext';
import { useRedTeamChat } from '@/hooks/useRedTeamChat';
import ChatMessages from '@/components/red-team/ChatMessages';

interface ChatPanelProps {
  token: string;
  sessionId: string;
  onUnauthorized: () => void;
}

export default function ChatPanel({ token, sessionId, onUnauthorized }: ChatPanelProps) {
  const { t } = useLanguage();
  const { threads, threadId, messages, step, sending, error, send, selectThread, startThread } =
    useRedTeamChat(token, sessionId, onUnauthorized);

  return (
    <div className="flex flex-1 overflow-hidden">
      <aside className="w-64 border-r border-neutral-900 flex flex-col">
        <button
          onClick={startThread}
          disabled={sending}
          className="m-4 flex items-center justify-center gap-2 rounded-xl border border-neutral-800 py-2.5 text-[10px] font-bold uppercase tracking-widest text-neutral-400 hover:text-white hover:border-red-500/40 disabled:opacity-50"
        >
          <MessageSquarePlus size={14} /> {t.newThread}
        </button>
        <h3 className="px-4 text-[10px] font-bold text-neutral-500 uppercase tracking-widest mb-2">{t.threadsTitle}</h3>
        <nav className="flex-1 overflow-y-auto px-2 space-y-1">
          {threads.length === 0 && <p className="px-2 text-xs text-neutral-600">{t.noThreads}</p>}
          {threads.map(thread => (
            <button
              key={thread.id}
              onClick={() => selectThread(thread)}
              disabled={sending}
              className={`w-full truncate rounded-lg px-3 py-2 text-left text-xs ${
                thread.id === threadId ? 'bg-neutral-900 text-white' : 'text-neutral-400 hover:bg-neutral-900/60'
              }`}
            >
              {thread.title}
            </button>
          ))}
        </nav>
      </aside>

      <section className="flex-1 flex flex-col overflow-hidden">
        <div className="flex-1 overflow-y-auto p-6">
          {messages.length === 0 && !sending ? (
            <p className="text-center text-xs text-neutral-600 mt-20">{t.chatEmpty}</p>
          ) : (
            <ChatMessages messages={messages} step={step} stepLabel={t.agentStep} />
          )}
          {error && <p role="alert" className="mt-4 text-xs text-red-400">{error}</p>}
        </div>

        <form
          onSubmit={(e: React.FormEvent<HTMLFormElement>) => {
            e.preventDefault();
            const form = e.currentTarget;
            const prompt = String(new FormData(form).get('prompt') || '');
            if (!prompt.trim()) return;
            form.reset();
            void send(prompt);
          }}
          className="border-t border-neutral-900 p-4 flex gap-3"
        >
          <input
            name="prompt"
            autoComplete="off"
            placeholder={t.chatPlaceholder}
            disabled={sending}
            className="flex-1 bg-neutral-900 border border-neutral-800 rounded-xl px-4 py-3 text-sm outline-none focus:border-red-500 transition-colors"
          />
          <button
            type="submit"
            disabled={sending}
            title={t.sendBtn}
            className="bg-red-600 hover:bg-red-500 rounded-xl px-5 flex items-center justify-center disabled:opacity-50"
          >
            {sending ? <RefreshCw className="animate-spin" size={16} /> : <Send size={16} />}
          </button>
        </form>
      </section>
    </div>
  );
}
