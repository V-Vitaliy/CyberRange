"use client";
import React, { useState, useRef, useEffect } from 'react';
import {
  Send, FileText, Terminal, Search, Database, AlertCircle, Globe,
  Bug, Flag, CheckCircle
} from 'lucide-react';

/**
 * Интерфейс чата Red Team
 * Шаг 3: Основной интерфейс. Защищен проверкой токена.
 */
export default function RedTeamChat() {
  const [language, setLanguage] = useState('en');
  const [messages, setMessages] = useState([
    { role: 'ai', content: '' }
  ]);
  const [input, setInput] = useState('');
  const [isTyping, setIsTyping] = useState(false);

  const [activeToken, setActiveToken] = useState("");
  const [errorHistory, setErrorHistory] = useState<string[]>([]);
  const [flagInput, setFlagInput] = useState("");

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const translations = {
    en: {
      initialMessage: "System initialized. Ready for security analysis queries",
      historySearch: "Search history...",
      knowledgeBase: "Knowledge Base",
      uploadDocument: "Upload Document",
      indicesNote: "Indices are rebuilt every 30s. Check response similarity for feedback.",
      inputPlaceholder: "Message AI_Assistant...",
      status: "SECURE_CHANNEL_ACTIVE",
      errorStateTitle: "Error State",
      flagSubmitTitle: "Flag Submit",
      flagPlaceholder: "Enter flag...",
      submitFlag: "Submit",
      aiAssistant: "AI_Assistant",
      operator: "Operator"
    },
    pl: {
      initialMessage: "System zainicjowany. Gotowy do zapytań analizy bezpieczeństwa",
      historySearch: "Szukaj w historii...",
      knowledgeBase: "Baza wiedzy",
      uploadDocument: "Prześlij dokument",
      indicesNote: "Indeksy są przebudowywane co 30s. Sprawdź podobieństwo odpowiedzi.",
      inputPlaceholder: "Napisz do AI_Assistant...",
      status: "KANAŁ_BEZPIECZNY_AKTYWNY",
      errorStateTitle: "Stan błędów",
      flagSubmitTitle: "Przesyłanie flagi",
      flagPlaceholder: "Wprowadź flagę...",
      submitFlag: "Prześlij",
      aiAssistant: "AI_Assistant",
      operator: "Operator"
    }
  };

  const t = translations[language as 'en' | 'pl'] || translations.en;

  // Проверка авторизации и загрузка настроек
  useEffect(() => {
    const savedToken = localStorage.getItem('auth_token');
    if (!savedToken) {
      window.location.href = '/red-team/login';
      return;
    }
    setActiveToken(savedToken);

    const savedLang = localStorage.getItem('app_lang');
    if (savedLang === 'en' || savedLang === 'pl') {
      setLanguage(savedLang);
    }
  }, []);

  useEffect(() => {
    setMessages([{ role: 'ai', content: t.initialMessage }]);
  }, [language, t.initialMessage]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(scrollToBottom, [messages]);

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isTyping) return;

    const userMessage = input.trim();
    setInput('');
    setMessages(prev => [...prev, { role: 'user', content: userMessage }]);
    setIsTyping(true);
    setMessages(prev => [...prev, { role: 'ai', content: '' }]);

    try {
      let currentSessionId = "default-session";
      try {
        if (activeToken) {
          const payloadStr = atob(activeToken.split('.')[1]);
          currentSessionId = JSON.parse(payloadStr).lab_instance_id || currentSessionId;
        }
      } catch(e) {}

      const response = await fetch('http://127.0.0.1:8000/api/red/chat/ask', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${activeToken}`
        },
        body: JSON.stringify({
          prompt: userMessage,
          session_id: currentSessionId
        })
      });

      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

      const reader = response.body?.getReader();
      if (!reader) return;

      const decoder = new TextDecoder("utf-8");

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value);
        const lines = chunk.split('\n');
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const dataStr = line.substring(6).trim();
            if (dataStr === '[DONE]') break;
            try {
              const data = JSON.parse(dataStr);
              if (data.text) {
                setMessages(prev => {
                  const updated = [...prev];
                  const lastIdx = updated.length - 1;
                  updated[lastIdx] = { ...updated[lastIdx], content: updated[lastIdx].content + data.text };
                  return updated;
                });
              }
            } catch (e) {}
          }
        }
      }
    } catch (err: any) {
      const errorMsg = err.message || "Unknown error";
      setMessages(prev => {
        const updated = [...prev];
        updated[updated.length - 1].content = `[System Error: ${errorMsg}]`;
        return updated;
      });
      setErrorHistory(prev => [...prev, `[${new Date().toLocaleTimeString()}] ${errorMsg}`]);
    } finally {
      setIsTyping(false);
    }
  };

  const toggleLanguage = () => {
    const newLang = language === 'en' ? 'pl' : 'en';
    setLanguage(newLang);
    localStorage.setItem('app_lang', newLang);
  };

  return (
    <div className="flex h-screen bg-neutral-950 text-neutral-200 font-sans overflow-hidden">

      {/* Левое меню */}
      <aside className="w-80 border-r border-neutral-900 bg-neutral-950 flex flex-col shrink-0 overflow-y-auto custom-scrollbar shadow-2xl z-20">
        <div className="p-6 border-b border-neutral-900 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Terminal size={18} className="text-red-500" />
            <h1 className="font-bold text-xs uppercase tracking-widest text-neutral-100">Red_Operator</h1>
          </div>
          <button
            onClick={toggleLanguage}
            className="text-[10px] font-bold text-neutral-500 hover:text-white transition-colors flex items-center gap-1 border border-neutral-800 rounded px-2 py-1"
          >
            <Globe size={12} />
            {language === 'en' ? 'PL' : 'EN'}
          </button>
        </div>

        <div className="p-6 space-y-10 flex-1">

          {/* 1. Search */}
          <section>
             <div className="relative group">
               <Search size={14} className="absolute left-3 top-2.5 text-neutral-600 group-focus-within:text-red-500 transition-colors" />
               <input
                type="text"
                placeholder={t.historySearch}
                className="w-full bg-neutral-900 border border-neutral-800 rounded-lg pl-9 pr-3 py-2 text-[10px] text-neutral-300 outline-none focus:border-red-500/30 transition-all"
               />
             </div>
          </section>

          {/* 2. Error state (История логов ошибок, без переключателя) */}
          <section>
            <div className="mb-3">
              <h3 className="text-[10px] font-bold text-neutral-500 uppercase tracking-widest flex items-center gap-2">
                <Bug size={14} /> {t.errorStateTitle}
              </h3>
            </div>

            <div className="bg-neutral-900/40 border border-neutral-800 rounded-lg p-3 max-h-32 overflow-y-auto custom-scrollbar">
              {errorHistory.length === 0 ? (
                <div className="text-[9px] text-neutral-600 font-mono italic">No system errors recorded.</div>
              ) : (
                <ul className="space-y-2">
                  {errorHistory.map((err, i) => (
                    <li key={i} className="text-[9px] text-red-500/80 font-mono break-words border-b border-neutral-800/50 pb-2 last:border-0 last:pb-0">
                      {err}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>

          {/* 3. Knowledge base upload */}
          <section>
            <h3 className="text-[10px] font-bold text-neutral-500 uppercase tracking-widest mb-4 flex items-center gap-2">
              <Database size={14} /> {t.knowledgeBase}
            </h3>
            <div className="p-4 border border-dashed border-neutral-800 hover:border-red-500/40 rounded-xl bg-neutral-900/30 transition-all cursor-pointer group flex flex-col items-center gap-2 text-center"
                 onClick={() => fileInputRef.current?.click()}>
              <FileText size={20} className="text-neutral-500 group-hover:text-red-500 transition-colors" />
              <span className="text-[9px] font-bold uppercase tracking-wider text-neutral-400">{t.uploadDocument}</span>
              <input type="file" ref={fileInputRef} onChange={() => {}} className="hidden" accept=".pdf,.doc,.docx,.txt" />
            </div>
            <div className="mt-3 p-2 rounded-lg border border-neutral-800 bg-neutral-900/50 flex items-start gap-2">
              <AlertCircle size={12} className="text-neutral-500 shrink-0 mt-0.5" />
              <p className="text-[8px] text-neutral-500 leading-tight">{t.indicesNote}</p>
            </div>
          </section>

          {/* 4. Flag submit */}
          <section>
            <h3 className="text-[10px] font-bold text-neutral-500 uppercase tracking-widest mb-4 flex items-center gap-2">
              <Flag size={14} /> {t.flagSubmitTitle}
            </h3>
            <div className="space-y-3">
              <input
                type="text"
                value={flagInput}
                onChange={(e) => setFlagInput(e.target.value)}
                placeholder={t.flagPlaceholder}
                className="w-full bg-neutral-900 border border-neutral-800 rounded-lg px-3 py-2.5 text-[10px] font-mono text-neutral-300 outline-none focus:border-red-500/50 transition-all"
              />
              <button
                disabled={!flagInput.trim()}
                className="w-full py-2 bg-red-600/10 hover:bg-red-600/20 disabled:bg-neutral-900/50 text-red-500 border border-red-500/20 rounded-lg text-[10px] font-bold uppercase transition-all flex items-center justify-center gap-2"
              >
                <CheckCircle size={12} />
                {t.submitFlag}
              </button>
            </div>
          </section>

        </div>
      </aside>

      {/* Основная зона чата */}
      <main className="flex-1 flex flex-col h-screen bg-neutral-950 relative overflow-hidden">
        <header className="h-14 border-b border-neutral-900 flex items-center px-8 justify-between bg-neutral-950/80 backdrop-blur-md z-10">
          <div className="flex items-center gap-4">
             <div className="text-[10px] font-mono text-neutral-500 flex items-center gap-2 uppercase tracking-tight">
                <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                {t.status}
             </div>
          </div>
        </header>

        {/* Лента сообщений */}
        <div className="flex-1 overflow-y-auto p-8 space-y-8 custom-scrollbar relative">
          <div className="max-w-3xl mx-auto w-full space-y-8 pb-12">
            {messages.map((msg, i) => (
              <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-[85%] px-6 py-4 rounded-2xl text-sm leading-relaxed shadow-sm transition-all ${
                  msg.role === 'user'
                    ? 'bg-neutral-900 border border-neutral-800 text-neutral-100'
                    : 'bg-transparent text-neutral-400 font-mono italic border-l-2 border-neutral-800 pl-8 ml-4'
                }`}>
                  <div className="text-[9px] font-bold text-neutral-600 mb-2 uppercase tracking-tighter">
                    {msg.role === 'user' ? t.operator : t.aiAssistant}
                  </div>
                  <div className="whitespace-pre-wrap">
                    {msg.content || (isTyping && i === messages.length - 1 && <div className="flex gap-1 py-1"><div className="w-1 h-1 bg-neutral-600 rounded-full animate-bounce" /><div className="w-1 h-1 bg-neutral-600 rounded-full animate-bounce delay-75" /><div className="w-1 h-1 bg-neutral-600 rounded-full animate-bounce delay-150" /></div>)}
                  </div>
                </div>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Плавающая панель ввода */}
        <div className="p-8 bg-gradient-to-t from-neutral-950 via-neutral-950 to-transparent">
          <form onSubmit={handleSendMessage} className="max-w-3xl mx-auto relative group">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={t.inputPlaceholder}
              className="w-full bg-neutral-900 border border-neutral-800 rounded-2xl px-6 py-4 text-sm outline-none focus:border-red-500/40 transition-all text-neutral-100 pr-16 shadow-2xl"
            />
            <button
              type="submit"
              disabled={!input.trim() || isTyping}
              className="absolute right-3 top-2.5 bg-red-600 hover:bg-red-500 disabled:bg-neutral-800 disabled:text-neutral-600 text-white p-2.5 rounded-xl transition-all shadow-lg active:scale-95 flex items-center justify-center"
            >
              <Send size={18} />
            </button>
          </form>
        </div>
      </main>
    </div>
  );
}