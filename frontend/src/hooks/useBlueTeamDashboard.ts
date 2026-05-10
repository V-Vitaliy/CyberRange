import { useState, useEffect, useCallback } from 'react';
import { DefenseOption, SiemEvent } from '@/types/blue-team';

const API_BASE = 'http://127.0.0.1:8000/api/blue';

export function useBlueTeamDashboard(sessionId: string) {
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [budget, setBudget] = useState(0);
  const [logs, setLogs] = useState<SiemEvent[]>([]);
  const [defenses, setDefenses] = useState<DefenseOption[]>([
    { id: 'rate_limit', name: 'Rate Limiter', cost: 3, enabled: false, description: "Blocks DoS attacks." },
    { id: 'reranker', name: 'Reranker', cost: 4, enabled: false, description: "Filters malicious context." },
    { id: 'prompt_hardening', name: 'Prompt Patch', cost: 2, enabled: false, description: "System prompt security." },
    { id: 'jwt_filter', name: 'JWT Strict Mode', cost: 2, enabled: false, description: "Enforces signature validation." }
  ]);

  const login = async (username: string, password: string) => {
    setLoading(true); setError('');
    try {
      const res = await fetch(`${API_BASE}/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      });
      if (!res.ok) throw new Error('Invalid credentials');
      const data = await res.json();
      setToken(data.access_token);
      localStorage.setItem('auth_token', data.access_token);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const logout = () => {
    setToken(null);
    localStorage.removeItem('auth_token');
  };

  const buyDefense = async (defenseId: string) => {
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE}/defenses/buy`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({ session_id: sessionId, defense_type: defenseId })
      });
      if (res.ok) await fetchData();
    } catch (e) { console.error(e); }
  };

  const investigate = async (logId: string) => {
    if (!token) return;
    try {
      const res = await fetch(`${API_BASE}/investigate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({ log_id: logId, is_malicious: true, session_id: sessionId })
      });
      if (res.ok) await fetchData();
    } catch (e) { console.error(e); }
  };

  const fetchData = useCallback(async () => {
    if (!token) return;
    try {
      const headers = { 'Authorization': `Bearer ${token}` };
      const stats = await fetch(`${API_BASE}/dashboard?session_id=${sessionId}`, { headers });
      if (stats.ok) {
        const data = await stats.json();
        setBudget(data.budget);
        setDefenses(prev => prev.map(d => ({ ...d, enabled: data.active_defenses[`${d.id}_enabled`] || false })));
      }
      const eventLogs = await fetch(`${API_BASE}/logs?session_id=${sessionId}`, { headers });
      if (eventLogs.ok) setLogs(await eventLogs.json());
    } catch (e) { console.error(e); }
  }, [token, sessionId]);

  useEffect(() => {
    const saved = localStorage.getItem('auth_token');
    if (saved) setToken(saved);
  }, []);

  useEffect(() => {
    if (token) {
      fetchData();
      const interval = setInterval(fetchData, 5000);
      return () => clearInterval(interval);
    }
  }, [token, fetchData]);

  return { token, login, logout, buyDefense, investigate, budget, logs, defenses, loading, error };
}