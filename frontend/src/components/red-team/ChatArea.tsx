import React, { useState, useEffect, useRef } from 'react';

export default function ChatArea() {
  const [messages, setMessages] = useState([
    { id: '1', role: 'assistant', content: 'Connection established. Vector space initialized. Awaiting queries...' }
  ]);
  const [input, setInput] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages]);

  const handleSend = () => {
    if (!input.trim()) return;

    const userMsg = { id: Date.now().toString(), role: 'user', content: input };
    setMessages(prev => [...prev, userMsg]);

    // Имитация SQLi "глюка" (Lab 1)
    if (input.includes("'") || input.toLowerCase().includes('union')) {
      setTimeout(() => {
        setMessages(prev => [...prev, {
          id: (Date.now() + 1).toString(),
          role: 'assistant',
          content: 'FATAL: database query failed. Query: "SELECT * FROM history WHERE msg LIKE \'' + input + '%\'". Error: Unclosed literal string.',
          isError: true
        }]);
      }, 500);
    }
    setInput('');
  };

  return (
    <div className="flex flex-col h-full bg-[#0d1117]">
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-6 space-y-6 scrollbar-thin scrollbar-thumb-[#30363d]">
        {messages.map((m) => (
          <div key={m.id} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] rounded-lg px-4 py-3 text-sm leading-relaxed shadow-sm ${
              m.isError
                ? 'bg-red-500/10 border border-red-500/50 text-red-400 font-mono animate-pulse'
                : m.role === 'user' ? 'bg-[#21262d] border border-[#30363d] text-white' : 'bg-[#161b22] border border-[#30363d] text-[#c9d1d9]'
            }`}>
              <div className="text-[10px] font-bold uppercase opacity-50 mb-1">{m.role}</div>
              {m.content}
            </div>
          </div>
        ))}
      </div>

      <div className="p-6 bg-[#161b22]/50 border-t border-[#30363d]">
        <div className="relative group">
          <input
            className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg pl-4 pr-12 py-3 text-sm text-white focus:outline-none focus:border-[#8b949e] transition-all"
            placeholder="Type your message or injection payload..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSend()}
          />
          <button
            onClick={handleSend}
            className="absolute right-3 top-3 text-[#8b949e] hover:text-white transition-colors"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 5l7 7-7 7M5 5l7 7-7 7"/></svg>
          </button>
        </div>
      </div>
    </div>
  );
}