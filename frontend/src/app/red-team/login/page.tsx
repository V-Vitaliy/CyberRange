"use client";
import React, { useState, useEffect } from 'react';
import { Terminal, ShieldAlert, Lock, Globe } from 'lucide-react';

/**
 * Страница входа Red Team
 * Шаг 2: Ввод данных. При успешном логине сохраняет токен и перенаправляет в чат.
 */
export default function RedTeamLogin() {
  const [language, setLanguage] = useState('en');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  useEffect(() => {
    const savedLang = localStorage.getItem('app_lang');
    if (savedLang === 'en' || savedLang === 'pl') {
      setLanguage(savedLang);
    }
  }, []);

  const translations = {
    en: {
      title: "Red Team Access",
      userPlaceholder: "Operator Username",
      passPlaceholder: "Access Key",
      button: "Authenticate",
    },
    pl: {
      title: "Dostęp Red Team",
      userPlaceholder: "Nazwa użytkownika",
      passPlaceholder: "Klucz dostępu",
      button: "Autoryzuj",
    }
  };

  const t = translations[language as 'en' | 'pl'] || translations.en;

  const handleLogin = (e: React.FormEvent) => {
    e.preventDefault();
    const mockToken = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6Ik9wZXJhdG9yIiwicm9sZSI6InJlZF90ZWFtIiwibGFiX2luc3RhbmNlX2lkIjoiZGVmYXVsdC1sYWIifQ.signature";
    localStorage.setItem('auth_token', mockToken);
    window.location.href = '/red-team/chat';
  };

  const toggleLanguage = () => {
    const newLang = language === 'en' ? 'pl' : 'en';
    setLanguage(newLang);
    localStorage.setItem('app_lang', newLang);
  };

  return (
    <div className="flex items-center justify-center min-h-screen bg-neutral-950 font-sans p-4 relative">
      <button
        onClick={toggleLanguage}
        className="absolute top-8 right-8 flex items-center gap-2 text-neutral-500 hover:text-white transition-colors text-xs font-bold uppercase tracking-widest"
      >
        <Globe size={14} />
        {language === 'en' ? 'PL' : 'EN'}
      </button>

      <div className="w-full max-w-md p-8 space-y-8 bg-neutral-900 border border-neutral-800 rounded-2xl shadow-2xl">
        <div className="flex flex-col items-center space-y-4">
          <div className="p-3 bg-red-600/10 rounded-full border border-red-500/20 shadow-[0_0_20px_rgba(239,68,68,0.1)]">
            <ShieldAlert className="w-8 h-8 text-red-500" />
          </div>
          <h2 className="text-2xl font-bold text-white tracking-tight text-center uppercase">{t.title}</h2>
        </div>

        <form onSubmit={handleLogin} className="space-y-6">
          <div className="space-y-4">
            <div className="relative group">
              <div className="absolute inset-y-0 left-0 flex items-center pl-4 pointer-events-none text-neutral-500 group-focus-within:text-red-500 transition-colors">
                <Terminal size={18} />
              </div>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="block w-full pl-11 pr-4 py-3 bg-neutral-950 border border-neutral-800 rounded-xl text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-red-500/50 transition-all font-mono"
                placeholder={t.userPlaceholder}
                required
              />
            </div>
            <div className="relative group">
              <div className="absolute inset-y-0 left-0 flex items-center pl-4 pointer-events-none text-neutral-500 group-focus-within:text-red-500 transition-colors">
                <Lock size={18} />
              </div>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="block w-full pl-11 pr-4 py-3 bg-neutral-950 border border-neutral-800 rounded-xl text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-red-500/50 transition-all font-mono"
                placeholder={t.passPlaceholder}
                required
              />
            </div>
          </div>

          <button
            type="submit"
            className="w-full py-3 bg-red-600 hover:bg-red-500 text-white rounded-xl font-bold text-sm tracking-widest uppercase transition-all shadow-lg shadow-red-600/10 active:scale-[0.98]"
          >
            {t.button}
          </button>
        </form>
      </div>
    </div>
  );
}