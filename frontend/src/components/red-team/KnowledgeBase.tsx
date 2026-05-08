'use client';
import React, { useState } from 'react';

export default function KnowledgeBase() {
  const [files] = useState([
    { name: 'company_policy.pdf', size: '1.2 MB', status: 'Indexed' },
    { name: 'product_specs.docx', size: '850 KB', status: 'Indexed' },
    { name: 'faq_base.json', size: '120 KB', status: 'Indexed' },
  ]);

  return (
    <div className="p-8 bg-[#0d1117] h-full overflow-y-auto">
      <div className="max-w-4xl mx-auto">
        <header className="mb-8">
          <h2 className="text-xl font-bold text-white mb-2">RAG Knowledge Base</h2>
          <p className="text-sm text-[#8b949e]">Управляйте документами, которые LLM использует для контекста. Система автоматически индексирует загруженные файлы.</p>
        </header>

        {/* Сетка файлов */}
        <div className="grid grid-cols-1 gap-4 mb-8">
          {files.map((file, idx) => (
            <div key={idx} className="flex items-center justify-between p-4 bg-[#161b22] border border-[#30363d] rounded-lg hover:border-[#8b949e] transition-colors group">
              <div className="flex items-center space-x-4">
                <div className="p-2 bg-[#21262d] rounded text-red-400">
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
                </div>
                <div>
                  <div className="text-sm font-medium text-[#c9d1d9]">{file.name}</div>
                  <div className="text-[10px] text-[#8b949e] uppercase">{file.size} • {file.status}</div>
                </div>
              </div>
              <button className="text-xs text-[#8b949e] hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity uppercase font-bold">
                Download
              </button>
            </div>
          ))}
        </div>

        {/* Зона загрузки (Vulnerable to Path Traversal) */}
        <div className="border-2 border-dashed border-[#30363d] rounded-xl p-12 text-center hover:border-red-500/50 transition-all cursor-pointer bg-[#161b22]/30">
          <input type="file" className="hidden" id="file-upload" />
          <label htmlFor="file-upload" className="cursor-pointer">
            <div className="mx-auto w-12 h-12 mb-4 text-[#484f58]">
              <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/></svg>
            </div>
            <p className="text-sm font-medium text-[#c9d1d9]">Click to upload document for indexing</p>
            <p className="text-xs text-[#8b949e] mt-1">PDF, DOCX, JSON (Max 10MB)</p>
          </label>
        </div>

        <div className="mt-4 p-4 bg-yellow-500/5 border border-yellow-500/20 rounded-lg">
          <div className="flex space-x-3">
            <svg className="w-5 h-5 text-yellow-600 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            <p className="text-[11px] text-yellow-200/60 leading-relaxed">
              <span className="font-bold text-yellow-500 uppercase">System Note:</span> При загрузке файлы сохраняются во временное хранилище `/data/uploads/tmp/`. Убедитесь, что имена файлов не содержат спецсимволов.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}