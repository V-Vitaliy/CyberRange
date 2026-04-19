"use client"
import React from 'react';
import { Shield, Lock, AlertTriangle } from 'lucide-react';
import { DefenseOption } from "@/types/blue-team";

interface Props {
  budget: number;
  defenses: DefenseOption[];
  onBuyDefense: (id: string, cost: number) => void;
}

export default function DefensePanel({ budget, defenses, onBuyDefense }: Props) {
  return (
    <aside className="w-80 bg-neutral-900 border-r border-neutral-800 flex flex-col z-10">
      <div className="h-14 flex items-center px-6 border-b border-neutral-800 bg-neutral-950">
        <Shield className="text-indigo-500 mr-3" size={18} />
        <h1 className="font-bold text-neutral-100 tracking-wider text-xs uppercase">Blue Team / SOC</h1>
      </div>

      <div className="p-5 flex-1 overflow-y-auto">
        <h2 className="text-xs font-semibold text-neutral-400 mb-4 uppercase tracking-wider flex items-center gap-2">
          <Lock size={14} /> Sklep z poprawkami (Patches)
        </h2>

        <div className="space-y-4">
          {defenses.map(defense => (
            <div key={defense.id} className="bg-neutral-950 border border-neutral-800 rounded-lg p-4 flex flex-col gap-3">
              <div className="flex justify-between items-start">
                <div>
                  <h3 className="text-sm font-semibold text-neutral-200">{defense.name}</h3>
                  <p className="text-[10px] text-neutral-500 mt-1 leading-relaxed">{defense.description}</p>
                </div>
              </div>

              <button
                onClick={() => onBuyDefense(defense.id, defense.cost)}
                disabled={defense.enabled || budget < defense.cost}
                className={`w-full py-2 rounded text-xs font-medium transition-all flex justify-center items-center gap-2 
                  ${defense.enabled 
                    ? 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/20 cursor-not-allowed' 
                    : budget >= defense.cost 
                      ? 'bg-neutral-800 hover:bg-neutral-700 text-neutral-200 border border-neutral-700'
                      : 'bg-neutral-900 text-neutral-600 border border-neutral-800 cursor-not-allowed'}`}
              >
                {defense.enabled ? 'Zainstalowano pomyślnie' : `Wdróż poprawkę (-${defense.cost} pts)`}
              </button>
            </div>
          ))}
        </div>
      </div>

      <div className="p-5 border-t border-neutral-800 bg-neutral-950/50">
         <h4 className="text-xs font-semibold text-indigo-400 flex items-center gap-1 mb-2">
             <AlertTriangle size={14} /> Instrukcja SOC
         </h4>
         <p className="text-xs text-neutral-500 leading-relaxed">
            Analizuj logi w terminalu po prawej. Zgłoś incydent, aby zyskać punkty na zakup poprawek bezpieczeństwa.
         </p>
      </div>
    </aside>
  );
}
