'use client';
import React, { useState } from 'react';

export default function CtfProgress() {
  const [flag, setFlag] = useState('');
  const [status, setStatus] = useState<'idle' | 'success' | 'error'>('idle');

  const labs = [
    { id: 1, title: 'Chat SQLi', points: 100, solved: true },
    { id: 2, title: 'Prompt Leakage', points: 150, solved: false },
    { id: 3, title: 'Path Traversal', points: 250, solved: false },
    { id: 4, title: 'Data Poisoning', points: 500, solved: false },
  ];

  const handleFlagSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (flag === "CyberRange{sql_injection_master}") {
      setStatus('success');
    } else {
      setStatus('error');
      setTimeout(() => setStatus('idle'), 2000);
    }
  };

  return (
    <div className="flex flex-col h-full">
      <div className="mb-8">
        <div className="flex justify-between items-end mb-2">
          <h3 className="text-xs font-bold text-[#8b949e] uppercase tracking-widest">Operator Stats</h3>
          <span className="text-xl font-mono font-bold text-white">100<span className="text-[#484f58]">/1000</span></span>
        </div>
        <div className="w-full bg-[#30363d] h-1.5 rounded-full">
          <div className="bg-red-500 h-full w-[10%] rounded-full shadow-[0_0_8px_rgba(239,68,68,0.5)]"></div>
        </div>
      </div>

      <div className="flex-1 space-y-3">
        <h3 className="text-[10px] font-bold text-[#484f58] uppercase mb-4 tracking-tighter">Mission Objectives</h3>
        {labs.map((lab) => (
          <div key={lab.id} className={`p-3 rounded-lg border transition-all ${
            lab.solved
              ? 'bg-green-500/5 border-green-500/30'
              : 'bg-[#0d1117] border-[#30363d]'
          }`}>
            <div className="flex justify-between items-center">
              <span className={`text-xs font-medium ${lab.solved ? 'text-green-400' : 'text-[#c9d1d9]'}`}>
                {lab.solved && '✓ '}Lab {lab.id}: {lab.title}
              </span>
              <span className="text-[10px] font-mono text-[#484f58]">{lab.points}pts</span>
            </div>
          </div>
        ))}
      </div>

      <div className="mt-auto pt-6 border-t border-[#30363d]">
        <form onSubmit={handleFlagSubmit} className="space-y-3">
          <div className="relative">
            <input
              type="text"
              value={flag}
              onChange={(e) => setFlag(e.target.value)}
              placeholder="ENTER_FLAG{...}"
              className={`w-full bg-[#0d1117] border rounded-md px-3 py-2.5 text-xs font-mono transition-all outline-none ${
                status === 'error' ? 'border-red-500 animate-shake' : 'border-[#30363d] focus:border-red-500'
              }`}
            />
            {status === 'success' && <span className="absolute right-3 top-2.5 text-green-500 text-xs">ACCEPTED</span>}
          </div>
          <button className="w-full bg-red-600 hover:bg-red-500 text-white text-[10px] font-bold py-2.5 rounded-md transition-colors uppercase tracking-widest">
            Submit Capture
          </button>
        </form>
      </div>
    </div>
  );
}