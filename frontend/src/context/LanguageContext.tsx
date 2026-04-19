"use client"
import React, { createContext, useContext, useState, ReactNode } from 'react';

type Language = 'en' | 'pl';

const DICTIONARY = {
  en: {
    selectTeam: "Select Your Team",
    redTeamDesc: "Attack and test LLM vulnerabilities",
    blueTeamDesc: "Monitor and defend the system",
    loginTitle: "SOC Authentication",
    loginSub: "Access the CyberRange environment",
    username: "Username",
    password: "Password",
    loginBtn: "Authorize",
    backBtn: "Back to Selection",
    budget: "Budget",
    logout: "Logout",
    chartTitle: "SIEM Activity",
    shopTitle: "Defense Systems",
    shopInst: "Deploy patches to mitigate active threats.",
    tableTitle: "Incident Logs",
    terminalTitle: "Log Inspector",
    terminalEmpty: "Select an event to view raw data...",
    investigateTitle: "Incident Investigation",
    investigateBtn: "Investigate",
    tableTime: "Time",
    tableAction: "Action",
    tableSrc: "Source",
    tableStatus: "Status"
  },
  pl: {
    selectTeam: "Wybierz swój zespół",
    redTeamDesc: "Atakuj i testuj podatności LLM",
    blueTeamDesc: "Monitoruj i broń systemu",
    loginTitle: "Autoryzacja SOC",
    loginSub: "Uzyskaj dostęp do środowiska CyberRange",
    username: "Użytkownik",
    password: "Hasło",
    loginBtn: "Zaloguj się",
    backBtn: "Powrót do wyboru",
    budget: "Budżet",
    logout: "Wyloguj",
    chartTitle: "Aktywność SIEM",
    shopTitle: "Systemy Obronne",
    shopInst: "Wdróż poprawki, aby powstrzymać ataki.",
    tableTitle: "Logi Incydentów",
    terminalTitle: "Inspektor Logów",
    terminalEmpty: "Wybierz zdarzenie, aby zobaczyć dane...",
    investigateTitle: "Dochodzenie incydentu",
    investigateBtn: "Zbadaj",
    tableTime: "Czas",
    tableAction: "Akcja",
    tableSrc: "Źródło",
    tableStatus: "Status"
  }
};

interface LanguageContextType {
  lang: Language;
  setLang: (lang: Language) => void;
  t: typeof DICTIONARY['en'];
}

const LanguageContext = createContext<LanguageContextType | undefined>(undefined);

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLang] = useState<Language>('pl');
  const value = { lang, setLang, t: DICTIONARY[lang] };

  return (
    <LanguageContext.Provider value={value}>
      {children}
    </LanguageContext.Provider>
  );
}

export function useLanguage() {
  const context = useContext(LanguageContext);
  if (!context) throw new Error('useLanguage must be used within LanguageProvider');
  return context;
}