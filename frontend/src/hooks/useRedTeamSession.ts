import { useState, useEffect, useCallback } from 'react';
import { ApiError } from '@/api/client';
import { RedProfile, clearRedToken, fetchRedProfile, getRedToken, loginRedTeam } from '@/api/red-team';

export function useRedTeamSession() {
  const [token, setToken] = useState<string | null>(null);
  const [profile, setProfile] = useState<RedProfile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const logout = useCallback(() => {
    clearRedToken();
    setToken(null);
    setProfile(null);
  }, []);

  const login = async (username: string, password: string, labCode: string) => {
    setLoading(true);
    setError('');
    try {
      setToken(await loginRedTeam(username, password, labCode));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Request failed');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // Browser storage is unavailable during SSR; restore only after hydration.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setToken(getRedToken());
  }, []);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    fetchRedProfile(token)
      .then(data => {
        if (cancelled) return;
        setProfile(data);
        setError('');
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) logout();
        setError(err instanceof Error ? err.message : 'Request failed');
      });
    return () => { cancelled = true; };
  }, [token, logout]);

  return { token, profile, login, logout, loading, error };
}
