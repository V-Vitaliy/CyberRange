"use client"
import React from 'react';
import { useRouter } from 'next/navigation';
import { Terminal, RefreshCw, Globe, LogOut, ArrowLeft } from 'lucide-react';

import { useLanguage } from '@/context/LanguageContext';
import { useRedTeamSession } from '@/hooks/useRedTeamSession';

export default function RedTeamPage() {
  const router = useRouter();
  const { t, lang, setLang } = useLanguage();
  const { token, profile, login, logout, loading, error } = useRedTeamSession();

  if (!token) {
    return (
      <div className="min-h-screen bg-neutral-950 flex flex-col items-center justify-center p-4">
        <button
          onClick={() => router.push('/')}
          className="mb-8 flex items-center gap-2 text-neutral-500 hover:text-white transition-colors text-xs uppercase font-bold tracking-widest"
        >
          <ArrowLeft size={14} /> {t.backBtn}
        </button>

        <div className="max-w-md w-full bg-neutral-900 border border-neutral-800 rounded-2xl p-10 shadow-2xl">
          <div className="flex justify-center mb-6">
            <div className="w-16 h-16 bg-red-500/10 text-red-500 rounded-full flex items-center justify-center">
              <Terminal size={32} />
            </div>
          </div>
          <h2 className="text-2xl font-bold text-center mb-2 text-white">{t.redLoginTitle}</h2>
          <p className="text-neutral-500 text-center text-xs mb-8 uppercase tracking-widest">{t.loginSub}</p>

          <form onSubmit={(e: React.FormEvent<HTMLFormElement>) => {
            e.preventDefault();
            const data = new FormData(e.currentTarget);
            login(String(data.get('username') || ''), String(data.get('password') || ''), String(data.get('lab_code') || '').trim());
          }} className="space-y-4">
            <input name="lab_code" required placeholder={t.labCode} autoComplete="off" className="w-full bg-neutral-950 border border-neutral-800 rounded-xl px-4 py-3 text-sm outline-none focus:border-red-500 transition-colors" />
            <input name="username" required placeholder={t.username} className="w-full bg-neutral-950 border border-neutral-800 rounded-xl px-4 py-3 text-sm outline-none focus:border-red-500 transition-colors" />
            <input name="password" type="password" required placeholder={t.password} className="w-full bg-neutral-950 border border-neutral-800 rounded-xl px-4 py-3 text-sm outline-none focus:border-red-500 transition-colors" />
            {error && <p role="alert" className="text-red-500 text-[10px] text-center font-bold uppercase">{error}</p>}
            <button type="submit" disabled={loading} className="w-full bg-red-600 py-3.5 rounded-xl text-xs font-bold uppercase tracking-widest hover:bg-red-500 transition-all shadow-lg shadow-red-500/10">
              {loading ? <RefreshCw className="animate-spin mx-auto" size={18} /> : t.loginBtn}
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen bg-neutral-950 text-neutral-300 font-sans">
      <header className="h-14 border-b border-neutral-900 flex justify-between items-center px-6">
        <button onClick={() => setLang(lang === 'pl' ? 'en' : 'pl')} className="flex items-center gap-2 text-neutral-500 hover:text-white text-[10px] font-bold uppercase tracking-widest">
          <Globe size={14} /> {lang}
        </button>
        <button onClick={logout} title={t.logout} className="text-neutral-500 hover:text-white p-2">
          <LogOut size={18} />
        </button>
      </header>

      <main className="flex-1 flex items-center justify-center p-6">
        <section className="max-w-md w-full bg-neutral-900 border border-neutral-800 rounded-2xl p-8 space-y-4">
          <h2 className="text-[10px] font-bold text-neutral-500 uppercase tracking-widest flex items-center gap-2">
            <Terminal size={14} className="text-red-500" /> {t.redSessionTitle}
          </h2>
          {error && <p role="alert" className="text-red-400 text-xs">{error}</p>}
          {profile ? (
            <dl className="space-y-3 text-sm">
              <div>
                <dt className="text-[10px] text-neutral-500 uppercase font-bold tracking-widest">{t.username}</dt>
                <dd className="text-white font-mono">{profile.username}</dd>
              </div>
              <div>
                <dt className="text-[10px] text-neutral-500 uppercase font-bold tracking-widest">{t.sessionId}</dt>
                <dd className="text-red-400 font-mono break-all">{profile.session_id ?? t.noSession}</dd>
              </div>
            </dl>
          ) : (
            !error && <RefreshCw className="animate-spin text-neutral-600" size={18} />
          )}
        </section>
      </main>
    </div>
  );
}
