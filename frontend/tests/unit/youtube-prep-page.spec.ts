import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory } from 'vue-router';

import { createI18nInstance } from '@/i18n';
import { createAppRouter } from '@/router';
import type { ToolModule } from '@/tools';
import * as tool from '@/tools/youtube-prep/index';
import { endonym, LANGUAGE_KEY } from '@/tools/youtube-prep/language';
import YouTubePrepPage from '@/tools/youtube-prep/YouTubePrepPage.vue';

vi.mock('tus-js-client', () => ({ Upload: class {} }));

const CONFIG = { max_upload_bytes: 1000, upload_chunk_bytes: 256, job_ttl_hours: 24 };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function stubFetch(options: () => Response) {
  const fn = vi.fn((url: string, _init?: RequestInit) => {
    if (url === '/api/config') return Promise.resolve(json(200, CONFIG));
    if (url === '/api/tools/youtube_prep/options') return Promise.resolve(options());
    if (url === '/api/tools/youtube_prep/jobs')
      return Promise.resolve(json(201, { id: 'j1', upload_url: '/u/1' }));
    return Promise.resolve(json(404, {}));
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
  return wrapper;
}

const okOptions = () => json(200, { languages: ['ru', 'de', 'en'] });

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe('youtube prep language picker', () => {
  it('has a labelled select with auto first, then endonyms in sorted order', async () => {
    stubFetch(okOptions);
    const wrapper = await setup();
    const select = wrapper.get('select');
    const label = wrapper.get(`label[for="${select.attributes('id')}"]`);
    expect(label.text()).toBe('Output language');
    const options = select.findAll('option');
    expect(options[0]?.text()).toBe('Same as the speech');
    expect(options[0]?.element.value).toBe('auto');
    const names = options.slice(1).map((o) => o.text());
    expect(names).toEqual(['de', 'en', 'ru'].map(endonym).sort((a, b) => a.localeCompare(b)));
    expect(select.element.value).toBe('auto');
  });

  it('restores the saved choice and saves a new one', async () => {
    localStorage.setItem(LANGUAGE_KEY, 'de');
    stubFetch(okOptions);
    const wrapper = await setup();
    const select = wrapper.get('select');
    expect(select.element.value).toBe('de');
    await select.setValue('ru');
    expect(localStorage.getItem(LANGUAGE_KEY)).toBe('ru');
  });

  it('sends the chosen language when creating the job', async () => {
    const fetchFn = stubFetch(okOptions);
    const wrapper = await setup();
    await wrapper.get('select').setValue('ru');
    const input = wrapper.get('input[type="file"]');
    const file = new File([new Uint8Array(1)], 'clip.mp4', { type: 'video/mp4' });
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true });
    await input.trigger('change');
    await flushPromises();
    const call = fetchFn.mock.calls.find((c) => c[0] === '/api/tools/youtube_prep/jobs');
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ language: 'ru' });
  });

  async function createBody(): Promise<Record<string, unknown>> {
    const fetchFn = stubFetch(okOptions);
    const wrapper = await setup();
    const input = wrapper.get('input[type="file"]');
    const file = new File([new Uint8Array(1)], 'clip.mp4', { type: 'video/mp4' });
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true });
    await input.trigger('change');
    await flushPromises();
    const call = fetchFn.mock.calls.find((c) => c[0] === '/api/tools/youtube_prep/jobs');
    return JSON.parse(String(call?.[1]?.body));
  }

  it('sends the browser language as the fallback language', async () => {
    vi.stubGlobal('navigator', { language: 'fr-CA' });
    expect(await createBody()).toMatchObject({ language: 'auto', fallback_language: 'fr' });
  });

  it('omits the fallback language when the browser reports none', async () => {
    vi.stubGlobal('navigator', { language: '' });
    expect(await createBody()).not.toHaveProperty('fallback_language');
  });

  it('offers only auto when the options request fails', async () => {
    stubFetch(() => json(500, {}));
    const wrapper = await setup();
    const options = wrapper.get('select').findAll('option');
    expect(options.map((o) => o.text())).toEqual(['Same as the speech']);
  });
});
