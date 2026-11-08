export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function requestJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    const message = typeof detail === 'string' ? detail : `Request failed (HTTP ${response.status})`;
    throw new ApiError(message, response.status);
  }
  if (data === null) throw new Error('The server returned an invalid response');
  return data as T;
}
