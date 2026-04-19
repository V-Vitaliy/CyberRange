"use client"
import React from 'react';
import { useRouter } from 'next/navigation';
import { Shield, Terminal, Globe } from 'lucide-react';
import { useLanguage } from '@/context/LanguageContext';

export default function Home() {
  const router = useRouter();
  const { t, lang, setLang } = useLanguage();

  return (
    <div className="flex h-screen bg-neutral-950 items-center justify-center text-white relative">
      <button
        onClick={() => setLang(lang === 'pl' ? 'en' : 'pl')}
        className="absolute top-8 right-8 flex items-center gap-2 text-neutral-500 hover:text-white transition-colors uppercase text-xs font-bold"
      >
        <Globe size={16} /> {lang}
      </button>

      <div className="text-center max-w-4xl px-4">
        <h1 className="text-5xl font-black mb-4 tracking-tighter italic uppercase">CyberRange</h1>
        <p className="text-neutral-500 mb-12 uppercase tracking-widest text-[10px]">{t.selectTeam}</p>

        <div className="flex flex-col md:flex-row gap-8 justify-center items-center">
          <button
            onClick={() => router.push('/red-team/chat')}
            className="group bg-neutral-900 border border-neutral-800 p-10 rounded-2xl hover:border-red-500/40 transition-all w-72 text-left"
          >
            <div className="w-12 h-12 bg-red-500/10 text-red-500 rounded-lg flex items-center justify-center mb-6">
              <Terminal size={24} />
            </div>
            <h2 className="text-lg font-bold italic">RED_TEAM</h2>
            <p className="text-[10px] text-neutral-500 mt-2 leading-relaxed">{t.redTeamDesc}</p>
          </button>

          <button
            onClick={() => router.push('/blue-team/dashboard')}
            className="group bg-neutral-900 border border-neutral-800 p-10 rounded-2xl hover:border-indigo-500/40 transition-all w-72 text-left"
          >
            <div className="w-12 h-12 bg-indigo-500/10 text-indigo-500 rounded-lg flex items-center justify-center mb-6">
              <Shield size={24} />
            </div>
            <h2 className="text-lg font-bold italic">BLUE_TEAM</h2>
            <p className="text-[10px] text-neutral-500 mt-2 leading-relaxed">{t.blueTeamDesc}</p>
          </button>
        </div>
      </div>
    </div>
  );
}