import { ApiError, requestJson } from './client.ts';

export const RED_API_BASE = process.env.NEXT_PUBLIC_RED_API_URL || 'http://127.0.0.1:8001/api/red';
// Separate from the Blue Team key: both dashboards share one origin.
export const RED_TOKEN_KEY = 'red_auth_token';

export interface RedProfile {
  username: string;
  role: string;
  session_id: string | null;
}

export function getRedToken(): string | null {
  return localStorage.getItem(RED_TOKEN_KEY);
}

export function clearRedToken(): void {
  localStorage.removeItem(RED_TOKEN_KEY);
}

export async function loginRedTeam(username: string, password: string, labCode: string): Promise<string> {
  const data = await requestJson<{ access_token: string }>(`${RED_API_BASE}/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, lab_code: labCode })
  });
  localStorage.setItem(RED_TOKEN_KEY, data.access_token);
  return data.access_token;
}

export function forgetRejectedToken(err: unknown, token: string): void {
  // A late 401 for an old token must not erase a newer login.
  if (err instanceof ApiError && err.status === 401 && getRedToken() === token) clearRedToken();
}

export async function fetchRedProfile(token: string): Promise<RedProfile> {
  try {
    return await requestJson<RedProfile>(`${RED_API_BASE}/me`, { headers: { Authorization: `Bearer ${token}` } });
  } catch (err) {
    forgetRejectedToken(err, token);
    throw err;
  }
}
