import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import UploadPanel from '@/components/UploadPanel.vue';
import { createI18nInstance } from '@/i18n';

interface FakeUploadOptions {
  uploadUrl: string;
  storeFingerprintForResuming?: boolean;
  onSuccess: () => void;
  onError: (error: Error) => void;
}

interface FakeUpload {
  file: Blob;
  options: FakeUploadOptions;
  start: ReturnType<typeof vi.fn>;
  abort: ReturnType<typeof vi.fn>;
}

const tus = vi.hoisted(() => ({ instances: [] as FakeUpload[] }));

vi.mock('tus-js-client', () => ({
  Upload: class {
    file: Blob;
    options: FakeUploadOptions;
    start = vi.fn();
    abort = vi.fn();
    constructor(file: Blob, options: FakeUploadOptions) {
      this.file = file;
      this.options = options;
      tus.instances.push(this);
    }
  },
}));

const STORAGE_KEY = 'videokit.uploads';
const CONFIG = { max_upload_bytes: 1000, upload_chunk_bytes: 256, job_ttl_hours: 24 };
const FINGERPRINT = 'clip.mp4|500|1700000000000|video/mp4';

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

let jobReply: () => Response;
let fetchMock: ReturnType<typeof vi.fn>;

function stubFetch() {
  fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/api/config') return Promise.resolve(json(200, CONFIG));
    if (url === '/api/jobs' && init?.method === 'POST') {
      return Promise.resolve(json(201, { job_id: 'new', upload_url: '/api/uploads/new' }));
    }
    if (url === '/api/jobs/new/cancel') return Promise.resolve(json(202, {}));
    if (url === '/api/jobs/old') return Promise.resolve(jobReply());
    return Promise.resolve(json(404, {}));
  });
  vi.stubGlobal('fetch', fetchMock);
}

const posted = () =>
  fetchMock.mock.calls.filter(([url, init]) => url === '/api/jobs' && init?.method === 'POST');
const stored = () => JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}');

async function setup() {
  const wrapper = mount(UploadPanel, {
    props: { toolId: 'youtube_prep' },
    global: { plugins: [createI18nInstance()] },
  });
  await flushPromises();
  return wrapper;
}

async function choose(
  wrapper: Awaited<ReturnType<typeof setup>>,
  lastModified = 1700000000000,
): Promise<void> {
  const input = wrapper.get('input[type="file"]');
  const file = new File([new Uint8Array(1)], 'clip.mp4', { type: 'video/mp4', lastModified });
  Object.defineProperty(file, 'size', { value: 500 });
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true });
  await input.trigger('change');
  await flushPromises();
}

function seed(entry: Record<string, unknown> = { job_id: 'old', upload_url: '/api/uploads/old' }) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ [FINGERPRINT]: entry }));
}

beforeEach(() => {
  tus.instances.length = 0;
  localStorage.clear();
  jobReply = () => json(200, { job_id: 'old', state: 'uploading' });
  stubFetch();
});
afterEach(() => vi.unstubAllGlobals());

describe('resumable uploads', () => {
  it('stores job id and upload url under a file fingerprint and disables tus own storage', async () => {
    const wrapper = await setup();
    await choose(wrapper);
    expect(stored()).toEqual({ [FINGERPRINT]: { job_id: 'new', upload_url: '/api/uploads/new' } });
    expect(tus.instances[0].options.storeFingerprintForResuming).toBe(false);
  });

  it('resumes the stored upload when the job is still uploading', async () => {
    seed();
    const wrapper = await setup();
    await choose(wrapper);
    expect(posted()).toHaveLength(0);
    expect(tus.instances).toHaveLength(1);
    expect(tus.instances[0].options.uploadUrl).toBe('/api/uploads/old');
    expect(tus.instances[0].start).toHaveBeenCalled();
    tus.instances[0].options.onSuccess();
    expect(wrapper.emitted('uploaded')).toEqual([['old']]);
  });

  it('starts fresh and drops the entry when the job is no longer uploading', async () => {
    seed();
    jobReply = () => json(200, { job_id: 'old', state: 'queued' });
    const wrapper = await setup();
    await choose(wrapper);
    expect(posted()).toHaveLength(1);
    expect(tus.instances[0].options.uploadUrl).toBe('/api/uploads/new');
    expect(stored()[FINGERPRINT].job_id).toBe('new');
  });

  it('starts fresh when the stored job is gone', async () => {
    seed();
    jobReply = () => json(404, { error: { code: 'job_not_found', message: 'gone' } });
    const wrapper = await setup();
    await choose(wrapper);
    expect(posted()).toHaveLength(1);
    expect(tus.instances[0].options.uploadUrl).toBe('/api/uploads/new');
  });

  it('treats a different lastModified as a different file', async () => {
    seed();
    const wrapper = await setup();
    await choose(wrapper, 1800000000000);
    expect(posted()).toHaveLength(1);
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/jobs/old')).toBe(false);
  });

  it('ignores corrupt stored data', async () => {
    localStorage.setItem(STORAGE_KEY, '{not json');
    const wrapper = await setup();
    await choose(wrapper);
    expect(posted()).toHaveLength(1);
    expect(stored()[FINGERPRINT].job_id).toBe('new');
  });

  it('clears the entry on completion', async () => {
    const wrapper = await setup();
    await choose(wrapper);
    tus.instances[0].options.onSuccess();
    expect(stored()).toEqual({});
  });

  it('keeps the entry after an upload error so a reload can resume', async () => {
    const wrapper = await setup();
    await choose(wrapper);
    tus.instances[0].options.onError(new Error('network'));
    expect(stored()[FINGERPRINT]).toBeTruthy();
  });

  it('pauses when offline and resumes when online', async () => {
    const wrapper = await setup();
    await choose(wrapper);
    const upload = tus.instances[0];
    window.dispatchEvent(new Event('offline'));
    expect(upload.abort).toHaveBeenCalledTimes(1);
    window.dispatchEvent(new Event('online'));
    expect(upload.start).toHaveBeenCalledTimes(2);
    wrapper.unmount();
    window.dispatchEvent(new Event('offline'));
    expect(upload.abort).toHaveBeenCalledTimes(1);
  });

  it('ignores online events when the upload was not paused', async () => {
    const wrapper = await setup();
    await choose(wrapper);
    window.dispatchEvent(new Event('online'));
    expect(tus.instances[0].start).toHaveBeenCalledTimes(1);
    window.dispatchEvent(new Event('offline'));
    window.dispatchEvent(new Event('online'));
    window.dispatchEvent(new Event('online'));
    expect(tus.instances[0].start).toHaveBeenCalledTimes(2);
    wrapper.unmount();
  });

  it('cancel before the job exists stops the upload from starting and cancels the new job', async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const base = fetchMock.getMockImplementation() as (
      u: string,
      i?: RequestInit,
    ) => Promise<Response>;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === '/api/jobs' && init?.method === 'POST') await gate;
      return base(url, init);
    });
    const wrapper = await setup();
    await choose(wrapper);
    await wrapper.get('[data-test="cancel-upload"]').trigger('click');
    release();
    await flushPromises();
    expect(tus.instances).toHaveLength(0);
    expect(stored()).toEqual({});
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/jobs/new/cancel')).toBe(true);
    expect(wrapper.find('[data-test="cancel-upload"]').exists()).toBe(false);
    expect(wrapper.get('input[type="file"]').attributes('disabled')).toBeUndefined();
  });

  it('cancel while checking a stored upload does not resume it', async () => {
    seed();
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const base = fetchMock.getMockImplementation() as (
      u: string,
      i?: RequestInit,
    ) => Promise<Response>;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === '/api/jobs/old') await gate;
      return base(url, init);
    });
    const wrapper = await setup();
    await choose(wrapper);
    await wrapper.get('[data-test="cancel-upload"]').trigger('click');
    release();
    await flushPromises();
    expect(tus.instances).toHaveLength(0);
    expect(stored()).toEqual({});
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/jobs/old/cancel')).toBe(true);
  });

  it('cancel stops the upload, clears the entry and cancels the job', async () => {
    const wrapper = await setup();
    await choose(wrapper);
    await wrapper.get('[data-test="cancel-upload"]').trigger('click');
    await flushPromises();
    expect(tus.instances[0].abort).toHaveBeenCalled();
    expect(stored()).toEqual({});
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/jobs/new/cancel')).toBe(true);
    expect(wrapper.find('[data-test="cancel-upload"]').exists()).toBe(false);
    expect(wrapper.get('input[type="file"]').attributes('disabled')).toBeUndefined();
  });
});
