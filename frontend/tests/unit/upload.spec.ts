import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory } from 'vue-router';

import { createI18nInstance } from '@/i18n';
import en from '@/i18n/en.json';
import { createAppRouter } from '@/router';
import type { ToolModule } from '@/tools';
import * as tool from '@/tools/youtube-prep/index';
import YouTubePrepPage from '@/tools/youtube-prep/YouTubePrepPage.vue';

interface FakeUploadOptions {
  uploadUrl: string;
  chunkSize: number;
  retryDelays: number[];
  onProgress: (sent: number, total: number) => void;
  onSuccess: () => void;
  onError: (error: Error) => void;
}

const tus = vi.hoisted(() => ({
  instances: [] as { file: Blob; options: FakeUploadOptions; start: () => void }[],
}));

vi.mock('tus-js-client', () => ({
  Upload: class {
    file: Blob;
    options: FakeUploadOptions;
    start = vi.fn();
    constructor(file: Blob, options: FakeUploadOptions) {
      this.file = file;
      this.options = options;
      tus.instances.push(this);
    }
  },
}));

const CONFIG = { max_upload_bytes: 1000, upload_chunk_bytes: 256, job_ttl_hours: 24 };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function stubFetch(handlers: Record<string, () => Response>) {
  const fn = vi.fn((url: string, _init?: RequestInit) => {
    const handler = handlers[url];
    return Promise.resolve(handler ? handler() : json(404, {}));
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

async function setup() {
  const router = createAppRouter(createMemoryHistory(), [tool as ToolModule]);
  await router.push('/tools/youtube-prep');
  await router.isReady();
  const wrapper = mount(YouTubePrepPage, { global: { plugins: [router, createI18nInstance()] } });
  await flushPromises();
  return { router, wrapper };
}

async function choose(wrapper: Awaited<ReturnType<typeof setup>>['wrapper'], size: number) {
  const input = wrapper.get('input[type="file"]');
  const file = new File([new Uint8Array(1)], 'clip.mp4', { type: 'video/mp4' });
  Object.defineProperty(file, 'size', { value: size });
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true });
  await input.trigger('change');
  await flushPromises();
  return file;
}

beforeEach(() => {
  tus.instances.length = 0;
});
afterEach(() => vi.unstubAllGlobals());

describe('youtube-prep tool module', () => {
  it('exports the backend tool id and the kebab-case upload route', () => {
    expect(tool.id).toBe('youtube_prep');
    expect(tool.routes.map((r) => r.path)).toContain('/tools/youtube-prep');
  });
});

describe('upload page', () => {
  it('renders a video file input', async () => {
    stubFetch({ '/api/config': () => json(200, CONFIG) });
    const { wrapper } = await setup();
    expect(wrapper.get('input[type="file"]').attributes('accept')).toBe('video/*');
  });

  it('rejects files over the limit with a message stating the limit and does not upload', async () => {
    const fetchMock = stubFetch({ '/api/config': () => json(200, CONFIG) });
    const { wrapper } = await setup();
    await choose(wrapper, 5000);
    expect(wrapper.get('[role="alert"]').text()).toContain('1000 B');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(tus.instances).toHaveLength(0);
  });

  it('creates the job, uploads with tus using uploadUrl and chunk size, shows progress, navigates', async () => {
    const fetchMock = stubFetch({
      '/api/config': () => json(200, CONFIG),
      '/api/jobs': () => json(201, { job_id: 'j1', upload_url: '/api/uploads/j1' }),
    });
    const { wrapper, router } = await setup();
    const file = await choose(wrapper, 500);

    const post = fetchMock.mock.calls.find(([url]) => url === '/api/jobs');
    expect(JSON.parse((post![1] as RequestInit).body as string)).toEqual({
      tool_id: 'youtube_prep',
      size: 500,
      file_name: 'clip.mp4',
    });

    expect(tus.instances).toHaveLength(1);
    const upload = tus.instances[0];
    expect(upload.file).toBe(file);
    expect(upload.options.uploadUrl).toBe('/api/uploads/j1');
    expect(upload.options.chunkSize).toBe(256);
    expect(upload.options.retryDelays.length).toBeGreaterThan(0);
    expect(upload.start).toHaveBeenCalled();

    upload.options.onProgress(250, 500);
    await flushPromises();
    expect(wrapper.get('progress').attributes('value')).toBe('50');
    expect(wrapper.text()).toContain('50%');

    upload.options.onSuccess();
    await vi.waitFor(() =>
      expect(router.currentRoute.value.fullPath).toBe('/tools/youtube-prep/jobs/j1'),
    );
  });

  it('shows the limit message when the server answers 413', async () => {
    stubFetch({
      '/api/config': () => json(200, CONFIG),
      '/api/jobs': () => json(413, { error: { code: 'file_too_large', message: 'Too big.' } }),
    });
    const { wrapper } = await setup();
    await choose(wrapper, 500);
    expect(wrapper.get('[role="alert"]').text()).toContain('1000 B');
    expect(tus.instances).toHaveLength(0);
  });

  it('shows the generic message for http_error failures', async () => {
    stubFetch({
      '/api/config': () => json(200, CONFIG),
      '/api/jobs': () => new Response('oops', { status: 502, statusText: 'Bad Gateway' }),
    });
    const { wrapper } = await setup();
    await choose(wrapper, 500);
    expect(wrapper.get('[role="alert"]').text()).toBe(en.errors.generic);
  });

  it.each(['unknown_tool', 'invalid_request', 'internal_error'] as const)(
    'shows the catalog message for the %s error code, not the API message',
    async (code) => {
      stubFetch({
        '/api/config': () => json(200, CONFIG),
        '/api/jobs': () => json(400, { error: { code, message: 'Raw server text.' } }),
      });
      const { wrapper } = await setup();
      await choose(wrapper, 500);
      expect(wrapper.get('[role="alert"]').text()).toBe(en.errors.codes[code]);
    },
  );

  it('shows the generic message for an unknown error code', async () => {
    stubFetch({
      '/api/config': () => json(200, CONFIG),
      '/api/jobs': () => json(400, { error: { code: 'brand_new_code', message: 'Raw text.' } }),
    });
    const { wrapper } = await setup();
    await choose(wrapper, 500);
    expect(wrapper.get('[role="alert"]').text()).toBe(en.errors.generic);
  });

  it('has a catalog message for every API error code the UI can receive', () => {
    const codes = [
      'file_too_large',
      'unknown_tool',
      'invalid_request',
      'not_cancellable',
      'job_not_found',
      'internal_error',
      'unsupported_tus_version',
      'upload_not_found',
      'chunk_too_large',
      'upload_in_progress',
      'offset_mismatch',
      'unsupported_media_type',
      'job_not_done',
      'regeneration_in_progress',
      'invalid_section',
      'generation_failed',
    ];
    const catalog: Record<string, string> = en.errors.codes;
    for (const code of codes) expect(catalog[code], code).toBeTruthy();
  });

  it('clears the file input so the same file can be re-selected after a failure', async () => {
    stubFetch({
      '/api/config': () => json(200, CONFIG),
      '/api/jobs': () => json(500, {}),
    });
    const { wrapper } = await setup();
    const writes: string[] = [];
    const input = wrapper.get('input[type="file"]').element;
    Object.defineProperty(input, 'value', {
      configurable: true,
      get: () => '',
      set: (v: string) => writes.push(v),
    });
    await choose(wrapper, 500);
    expect(writes).toContain('');
    await choose(wrapper, 500);
    expect(wrapper.get('[role="alert"]').text()).toBe(en.errors.generic);
  });

  it('shows an error when the tus upload fails and allows choosing again', async () => {
    stubFetch({
      '/api/config': () => json(200, CONFIG),
      '/api/jobs': () => json(201, { job_id: 'j1', upload_url: '/api/uploads/j1' }),
    });
    const { wrapper } = await setup();
    await choose(wrapper, 500);
    tus.instances[0].options.onError(new Error('network'));
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe(en.upload.failed);
    expect(wrapper.get('input[type="file"]').attributes('disabled')).toBeUndefined();
  });

  it('shows the catalog message for a coded tus upload error', async () => {
    stubFetch({
      '/api/config': () => json(200, CONFIG),
      '/api/jobs': () => json(201, { job_id: 'j1', upload_url: '/api/uploads/j1' }),
    });
    const { wrapper } = await setup();
    await choose(wrapper, 500);
    const error = Object.assign(new Error('tus: unexpected response'), {
      originalResponse: {
        getStatus: () => 409,
        getBody: () =>
          JSON.stringify({ error: { code: 'upload_in_progress', message: 'Raw text.' } }),
      },
    });
    tus.instances[0].options.onError(error);
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe(en.errors.codes.upload_in_progress);
  });

  it('shows the upload-failed message for a tus error without a known code', async () => {
    stubFetch({
      '/api/config': () => json(200, CONFIG),
      '/api/jobs': () => json(201, { job_id: 'j1', upload_url: '/api/uploads/j1' }),
    });
    const { wrapper } = await setup();
    await choose(wrapper, 500);
    const error = Object.assign(new Error('tus'), {
      originalResponse: { getStatus: () => 500, getBody: () => 'not json' },
    });
    tus.instances[0].options.onError(error);
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe(en.upload.failed);
  });

  it('shows the generic error when config cannot be loaded', async () => {
    stubFetch({ '/api/config': () => json(500, {}) });
    const { wrapper } = await setup();
    expect(wrapper.get('[role="alert"]').text()).toBe(en.errors.generic);
  });
});
