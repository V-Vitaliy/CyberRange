"use client"
import React from 'react';
import { Database, Search } from 'lucide-react';
import { useLanguage } from '@/context/LanguageContext';
import { SiemEvent } from '@/types/blue-team';

interface Props {
  selectedEvent: SiemEvent | null;
  investigateId: string;
  setInvestigateId: (id: string) => void;
  onInvestigate: () => void;
}

export default function LogTerminal({ selectedEvent, investigateId, setInvestigateId, onInvestigate }: Props) {
  const { t } = useLanguage();

  return (
    <div className="flex-[2] flex flex-col gap-6 h-full overflow-hidden">
      {/* RAW Log Viewer */}
      <div className="flex-1 bg-black border border-neutral-800 rounded-xl overflow-hidden flex flex-col font-mono">
        <div className="p-3 border-b border-neutral-800 bg-neutral-900/50 flex items-center gap-2">
          <Database size={14} className="text-neutral-500" />
          <span className="text-[10px] text-neutral-500 uppercase tracking-widest font-bold">{t.terminalTitle}</span>
        </div>
        <div className="p-5 overflow-y-auto text-[11px] text-emerald-500/80 leading-relaxed custom-scrollbar">
          {selectedEvent ? (
            <pre className="whitespace-pre-wrap break-all">
              {JSON.stringify(selectedEvent.payload, null, 2)}
            </pre>
          ) : (
            <span className="text-neutral-700 italic">{t.terminalEmpty}</span>
          )}
        </div>
      </div>

      {/* Investigation Control */}
      <div className="bg-neutral-900 border border-neutral-800 rounded-xl p-5 space-y-4">
        <h3 className="text-[10px] font-bold text-neutral-400 uppercase tracking-widest">{t.investigateTitle}</h3>

        <div className="relative">
          <Search className="absolute left-3 top-2.5 text-neutral-600" size={14} />
          <input
            type="text"
            value={investigateId}
            onChange={(e) => setInvestigateId(e.target.value)}
            placeholder="Event ID..."
            className="w-full bg-neutral-950 border border-neutral-800 rounded-lg pl-9 pr-4 py-2 text-xs outline-none focus:border-indigo-500 text-neutral-300 font-mono"
          />
        </div>

        <button
          onClick={onInvestigate}
          disabled={!investigateId}
          className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:bg-neutral-800 disabled:text-neutral-600 text-white font-bold py-2.5 rounded-lg text-[10px] uppercase tracking-widest transition-all"
        >
          {t.investigateBtn}
        </button>
      </div>
    </div>
  );
}