"use client"
import React, { useState, Suspense, useEffect } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { Shield, RefreshCw, Activity, Globe, LogOut, ArrowLeft } from 'lucide-react';

import { useLanguage } from '@/context/LanguageContext';
import { useBlueTeamDashboard } from '@/hooks/useBlueTeamDashboard';
import ActivityChart from '@/components/blue-team/ActivityChart';
import EventTable from '@/components/blue-team/EventTable';
import LogTerminal from '@/components/blue-team/LogTerminal';
import DefensePanel from '@/components/blue-team/DefensePanel';

function DashboardContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { t, lang, setLang } = useLanguage();

  const sessionId = searchParams.get('session_id') || "33333333-3333-3333-3333-333333333333";

  const {
    token, login, logout, buyDefense, investigate,
    budget, logs, defenses, loading, error
  } = useBlueTeamDashboard(sessionId);

  const [selectedEvent, setSelectedEvent] = useState<any>(null);
  const [investigateId, setInvestigateId] = useState('');

  // Sync input with selected event ID
  useEffect(() => {
    if (selectedEvent) setInvestigateId(selectedEvent.id);
  }, [selectedEvent]);

  // МАППИНГ: Исправляем несоответствие названий между фронтендом и бэкендом
  const handleBuyDefense = (id: string) => {
    // В базе и defense.py защита называется "system_prompt", а в UI "prompt_hardening"
    const backendId = id === 'prompt_hardening' ? 'system_prompt' : id;
    buyDefense(backendId);
  };

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
            <div className="w-16 h-16 bg-indigo-500/10 text-indigo-500 rounded-full flex items-center justify-center">
              <Shield size={32} />
            </div>
          </div>
          <h2 className="text-2xl font-bold text-center mb-2 text-white">{t.loginTitle}</h2>
          <p className="text-neutral-500 text-center text-xs mb-8 uppercase tracking-widest">{t.loginSub}</p>

          <form onSubmit={(e: any) => {
            e.preventDefault();
            login(e.target.username.value, e.target.password.value);
          }} className="space-y-4">
            <input name="username" placeholder={t.username} defaultValue="blue_defender" className="w-full bg-neutral-950 border border-neutral-800 rounded-xl px-4 py-3 text-sm outline-none focus:border-indigo-500 transition-colors" />
            <input name="password" type="password" placeholder={t.password} defaultValue="secret" className="w-full bg-neutral-950 border border-neutral-800 rounded-xl px-4 py-3 text-sm outline-none focus:border-indigo-500 transition-colors" />
            {error && <p className="text-red-500 text-[10px] text-center font-bold uppercase">{error}</p>}
            <button type="submit" disabled={loading} className="w-full bg-indigo-600 py-3.5 rounded-xl text-xs font-bold uppercase tracking-widest hover:bg-indigo-500 transition-all shadow-lg shadow-indigo-500/10">
              {loading ? <RefreshCw className="animate-spin mx-auto" size={18} /> : t.loginBtn}
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-neutral-950 text-neutral-300 font-sans overflow-hidden">
      <DefensePanel
        budget={budget}
        defenses={defenses}
        onBuyDefense={handleBuyDefense}
      />

      <main className="flex-1 flex flex-col h-screen overflow-hidden">
        <header className="h-14 border-b border-neutral-900 flex justify-between items-center px-6">
          <button onClick={() => setLang(lang === 'pl' ? 'en' : 'pl')} className="flex items-center gap-2 text-neutral-500 hover:text-white text-[10px] font-bold uppercase tracking-widest">
            <Globe size={14} /> {lang}
          </button>

          <div className="flex items-center gap-4">
            <div className="bg-neutral-900 border border-neutral-800 rounded-full px-5 py-1.5 flex items-center gap-3">
              <span className="text-[10px] text-neutral-500 uppercase font-bold tracking-widest">{t.budget}</span>
              <span className="text-indigo-400 font-mono font-bold text-sm">{budget}pts</span>
            </div>
            <button onClick={logout} className="text-neutral-500 hover:text-white p-2">
              <LogOut size={18} />
            </button>
          </div>
        </header>

        <div className="p-6 flex-1 overflow-hidden flex flex-col gap-6">
          <section className="bg-neutral-900/20 border border-neutral-800 rounded-2xl p-6 shrink-0">
            <h3 className="text-[10px] font-bold text-neutral-500 uppercase tracking-widest flex items-center gap-2 mb-2">
              <Activity size={14} className="text-indigo-500" /> {t.chartTitle}
            </h3>
            {/* УБРАЛИ ФИЛЬТР: Теперь передаем сырые логи (logs) */}
            <ActivityChart data={logs} />
          </section>

          <div className="flex-1 flex gap-6 overflow-hidden pb-4">
            {/* УБРАЛИ ФИЛЬТР: Теперь передаем сырые логи (logs) */}
            <EventTable
              events={logs}
              selectedEventId={selectedEvent?.id}
              onSelectEvent={setSelectedEvent}
            />
            <LogTerminal
              selectedEvent={selectedEvent}
              investigateId={investigateId}
              setInvestigateId={setInvestigateId}
              onInvestigate={() => investigate(investigateId)}
            />
          </div>
        </div>
      </main>
    </div>
  );
}

export default function DefenseDashboard() {
  return (
    <Suspense fallback={<div className="h-screen bg-neutral-950 flex items-center justify-center font-mono text-xs text-neutral-600">INTIALIZING_CORE_SYSTEM...</div>}>
      <DashboardContent />
    </Suspense>
  );
}