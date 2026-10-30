export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

const FALLBACK_CODE = 'http_error';

async function toApiError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { error?: { code?: unknown; message?: unknown } };
    const { code, message } = body.error ?? {};
    if (typeof code === 'string' && typeof message === 'string') {
      return new ApiError(response.status, code, message);
    }
  } catch {
    // Not JSON: fall through to the generic error.
  }
  return new ApiError(response.status, FALLBACK_CODE, response.statusText || 'Request failed');
}

async function request<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    throw await toApiError(response);
  }
  return (await response.json()) as T;
}

export function getJson<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'GET', headers: { Accept: 'application/json' } });
}

export function postJson<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

/** POST a multipart form. No Content-Type: the browser adds it, with the multipart boundary. */
export function postForm<T>(path: string, form: FormData): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { Accept: 'application/json' },
    body: form,
  });
}
