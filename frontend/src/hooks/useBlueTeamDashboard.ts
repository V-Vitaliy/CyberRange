import { useState, useEffect, useCallback, useRef } from 'react';
import { DefenseOption, SiemEvent } from '@/types/blue-team';
import { ApiError, requestJson } from '@/api/blue-team';

const API_BASE = process.env.NEXT_PUBLIC_BLUE_API_URL || 'http://127.0.0.1:8001/api/blue';
const initialDefenses: DefenseOption[] = [
  { id: 'rate_limit', name: 'Rate Limiter', cost: 3, enabled: false, description: 'Blocks DoS attacks.' },
  { id: 'reranker', name: 'Reranker', cost: 4, enabled: false, description: 'Filters malicious context.' },
  { id: 'prompt_hardening', name: 'Prompt Patch', cost: 2, enabled: false, description: 'System prompt security.' },
  { id: 'jwt_filter', name: 'JWT Strict Mode', cost: 2, enabled: false, description: 'Enforces signature validation.' }
];

export function useBlueTeamDashboard(requestedSessionId: string | null) {
  const [token, setToken] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const actionPending = useRef(false);
  const identity = useRef(0);
  const [error, setError] = useState('');
  const [pollError, setPollError] = useState('');
  const [budget, setBudget] = useState(0);
  const [logs, setLogs] = useState<SiemEvent[]>([]);
  const [defenses, setDefenses] = useState(initialDefenses);
  const [refresh, setRefresh] = useState(0);
  const [snapshotOwner, setSnapshotOwner] = useState<{ token: string; requestedSessionId: string | null } | null>(null);
  const isCurrent = Boolean(token && snapshotOwner?.token === token && snapshotOwner?.requestedSessionId === requestedSessionId);

  const logout = useCallback(() => {
    identity.current += 1;
    setToken(null);
    setSessionId(null);
    setBudget(0);
    setLogs([]);
    setDefenses(initialDefenses);
    setError('');
    setPollError('');
    localStorage.removeItem('auth_token');
  }, []);

  const handleError = useCallback((err: unknown) => {
    if (err instanceof ApiError && err.status === 401) logout();
    setError(err instanceof Error ? err.message : 'Request failed');
  }, [logout]);

  const login = async (username: string, password: string) => {
    setLoading(true);
    setError('');
    try {
      const data = await requestJson<{ access_token: string }>(`${API_BASE}/login`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      });
      identity.current += 1;
      setToken(data.access_token);
      localStorage.setItem('auth_token', data.access_token);
    } catch (err) {
      handleError(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // Browser storage is unavailable during SSR; restore only after hydration.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setToken(localStorage.getItem('auth_token'));
  }, []);

  useEffect(() => {
    identity.current += 1;
  }, [token, requestedSessionId]);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let resolvedSession: string | null = null;
    const headers = { Authorization: `Bearer ${token}` };
    const options = { headers, signal: controller.signal };
    const poll = async () => {
      try {
        if (!resolvedSession) {
          const profile = await requestJson<{ session_id: string | null }>(`${API_BASE}/me`, options);
          resolvedSession = requestedSessionId || profile.session_id;
          if (!resolvedSession) throw new Error('No game session is available for this laboratory. Run database setup first.');
        }
        const query = `session_id=${encodeURIComponent(resolvedSession)}`;
        const [stats, eventLogs] = await Promise.all([
          requestJson<{ budget: number; active_defenses: Record<string, boolean> }>(`${API_BASE}/dashboard?${query}`, options),
          requestJson<SiemEvent[]>(`${API_BASE}/logs?${query}`, options)
        ]);
        if (controller.signal.aborted) return;
        setSnapshotOwner({ token, requestedSessionId });
        setSessionId(resolvedSession);
        setBudget(stats.budget);
        setLogs(eventLogs);
        setDefenses(initialDefenses.map(d => ({ ...d, enabled: Boolean(stats.active_defenses[`${d.id}_enabled`]) })));
        setPollError('');
      } catch (err) {
        if (controller.signal.aborted) return;
        if (err instanceof ApiError && err.status === 401) handleError(err);
        else setPollError(err instanceof Error ? err.message : 'Could not refresh dashboard');
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 5000);
      }
    };
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [token, requestedSessionId, refresh, handleError]);

  const performAction = async (path: string, data: Record<string, unknown>) => {
    if (!token || !isCurrent || !sessionId || actionPending.current) return;
    const currentIdentity = identity.current;
    actionPending.current = true;
    setBusy(true);
    setError('');
    try {
      await requestJson(`${API_BASE}/${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ ...data, session_id: sessionId })
      });
      if (identity.current === currentIdentity) setRefresh(value => value + 1);
    } catch (err) {
      if (identity.current === currentIdentity) handleError(err);
    } finally {
      actionPending.current = false;
      setBusy(false);
    }
  };

  const buyDefense = (defenseId: string) => performAction('defenses/buy', { defense_type: defenseId });
  const investigate = (logId: string, isMalicious: boolean) => performAction('investigate', { log_id: logId, is_malicious: isMalicious });

  return { token, sessionId: isCurrent ? sessionId : null, login, logout, buyDefense, investigate,
    budget: isCurrent ? budget : 0, logs: isCurrent ? logs : [], defenses: isCurrent ? defenses : initialDefenses,
    loading, busy, error: error || pollError };
}
