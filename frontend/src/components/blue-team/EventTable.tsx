"use client"
import React from 'react';
import { useLanguage } from '@/context/LanguageContext';
import { SiemEvent } from '@/types/blue-team';

interface Props {
  events: SiemEvent[];
  selectedEventId?: string;
  onSelectEvent: (ev: SiemEvent) => void;
}

export default function EventTable({ events, selectedEventId, onSelectEvent }: Props) {
  const { t } = useLanguage();

  return (
    <div className="flex-[3] bg-neutral-900/30 border border-neutral-900 rounded-2xl overflow-hidden flex flex-col">
      <div className="p-4 border-b border-neutral-900 bg-neutral-900/50">
        <span className="text-[10px] font-bold text-neutral-400 uppercase tracking-widest">{t.tableTitle}</span>
      </div>
      <div className="flex-1 overflow-y-auto">
        <table className="w-full text-left text-[11px] font-mono">
          <thead className="bg-neutral-950/50 text-neutral-600 sticky top-0 border-b border-neutral-900">
            <tr>
              <th className="px-6 py-3 font-bold uppercase">{t.tableTime}</th>
              <th className="px-6 py-3 font-bold uppercase">{t.tableAction}</th>
              <th className="px-6 py-3 font-bold uppercase">{t.tableSrc}</th>
              <th className="px-6 py-3 font-bold uppercase">{t.tableStatus}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-900">
            {events.map((log) => (
              <tr
                key={log.id}
                onClick={() => onSelectEvent(log)}
                className={`cursor-pointer transition-all ${selectedEventId === log.id ? 'bg-indigo-500/5' : 'hover:bg-neutral-900/50'}`}
              >
                <td className="px-6 py-4 text-neutral-500">{new Date(log.timestamp).toLocaleTimeString()}</td>
                <td className="px-6 py-4 text-neutral-300 font-bold">{log.event_type}</td>
                <td className="px-6 py-4 text-neutral-500">{log.payload?.source_ip || '127.0.0.1'}</td>
                <td className="px-6 py-4">
                  <span className={`px-2 py-0.5 rounded text-[9px] font-bold border ${log.investigated_at ? 'border-emerald-500/20 text-emerald-500 bg-emerald-500/5' : 'border-neutral-800 text-neutral-500 bg-neutral-900'}`}>
                    {log.investigated_at ? 'RESOLVED' : 'PENDING'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}