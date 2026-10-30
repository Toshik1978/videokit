import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, postForm } from '@/api/client';

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe('postForm', () => {
  it('posts the form as multipart without setting Content-Type and parses the JSON reply', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(json(201, { job_id: 'j1' })));
    vi.stubGlobal('fetch', fetchMock);
    const form = new FormData();
    form.append('prompt', 'hello');
    form.append('pictures', new File(['x'], 'a.png', { type: 'image/png' }));

    const result = await postForm<{ job_id: string }>('/api/tools/video_generation/jobs', form);

    expect(result).toEqual({ job_id: 'j1' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe('/api/tools/video_generation/jobs');
    expect(init.method).toBe('POST');
    expect(init.body).toBe(form);
    const headers = new Headers(init.headers);
    expect(headers.get('Accept')).toBe('application/json');
    expect(headers.has('Content-Type')).toBe(false);
  });

  it('throws an ApiError carrying the error envelope code and status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(json(413, { error: { code: 'picture_too_large', message: 'Too big.' } })),
      ),
    );

    const err = await postForm('/api/tools/video_generation/jobs', new FormData()).catch(
      (e: unknown) => e,
    );

    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 413, code: 'picture_too_large', message: 'Too big.' });
  });

  it('falls back to a generic http_error when the reply is not an error envelope', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(new Response('<html>', { status: 502, statusText: 'Bad Gateway' })),
      ),
    );

    const err = await postForm('/api/tools/video_generation/jobs', new FormData()).catch(
      (e: unknown) => e,
    );

    expect(err).toMatchObject({ status: 502, code: 'http_error' });
  });
});
