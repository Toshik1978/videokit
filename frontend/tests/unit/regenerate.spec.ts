import { flushPromises, mount, type VueWrapper } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory } from 'vue-router';

import { createI18nInstance } from '@/i18n';
import en from '@/i18n/en.json';
import { createAppRouter } from '@/router';
import type { ToolModule } from '@/tools';
import * as tool from '@/tools/youtube-prep/index';

const RENDERED = {
  cover_titles: 'One\nTwo\nThree',
  title: 'My title',
  description: 'Para one.',
  tags: 'a, b',
  hashtags: '#a #b #c',
  chapters: '0:00 "Intro"',
};

const DONE_JOB = {
  job_id: 'j1',
  tool_id: 'youtube_prep',
  state: 'done',
  upload: { offset: 5, length: 5 },
  queue_position: null,
  failure_reason: null,
  result: { language: 'en', notices: [], rendered: RENDERED },
};

const URL = '/api/tools/youtube_prep/jobs/j1/regenerate';

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function errorBody(code: string) {
  return { error: { code, message: 'raw server text' } };
}

let regenReply: () => Promise<Response>;
let fetchMock: ReturnType<typeof vi.fn>;

async function mountPage(): Promise<VueWrapper> {
  fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === URL && init?.method === 'POST') return regenReply();
    return Promise.resolve(json(200, DONE_JOB));
  });
  vi.stubGlobal('fetch', fetchMock);
  const router = createAppRouter(createMemoryHistory(), [tool as ToolModule]);
  await router.push('/tools/youtube-prep/jobs/j1');
  await router.isReady();
  const { default: App } = await import('@/App.vue');
  const wrapper = mount(App, { global: { plugins: [router, createI18nInstance()] } });
  await flushPromises();
  return wrapper;
}

const card = (w: VueWrapper, key: string) => w.get(`[data-test="card-${key}"]`);
const area = (w: VueWrapper, key: string) =>
  card(w, key).get('textarea').element as HTMLTextAreaElement;
const regenCalls = () => fetchMock.mock.calls.filter(([url]) => url === URL);

beforeEach(() => {
  regenReply = () =>
    Promise.resolve(
      json(200, {
        result: { language: 'en', notices: [], rendered: { ...RENDERED, title: 'New title' } },
        usage: { input_tokens: 1 },
      }),
    );
});
afterEach(() => vi.unstubAllGlobals());

describe('regenerate', () => {
  it('has a regenerate button and a hint field limited to 500 chars on every card', async () => {
    const wrapper = await mountPage();
    expect(wrapper.findAll('[data-test="regenerate"]')).toHaveLength(6);
    const hint = card(wrapper, 'title').get('[data-test="hint"]');
    expect(hint.attributes('maxlength')).toBe('500');
    wrapper.unmount();
  });

  it('posts section and hint, then replaces only that card and discards its edits', async () => {
    const wrapper = await mountPage();
    await card(wrapper, 'title').get('textarea').setValue('Edited title');
    await card(wrapper, 'tags').get('textarea').setValue('Edited tags');
    await card(wrapper, 'title').get('[data-test="hint"]').setValue('shorter');
    await card(wrapper, 'title').get('[data-test="regenerate"]').trigger('click');
    await flushPromises();

    expect(JSON.parse((regenCalls()[0][1] as RequestInit).body as string)).toEqual({
      section: 'title',
      hint: 'shorter',
    });
    expect(area(wrapper, 'title').value).toBe('New title');
    expect(area(wrapper, 'tags').value).toBe('Edited tags');
    wrapper.unmount();
  });

  it('replaces the text even when the new text equals the old one', async () => {
    regenReply = () =>
      Promise.resolve(
        json(200, { result: { language: 'en', notices: [], rendered: RENDERED }, usage: {} }),
      );
    const wrapper = await mountPage();
    await card(wrapper, 'title').get('textarea').setValue('Edited title');
    await card(wrapper, 'title').get('[data-test="regenerate"]').trigger('click');
    await flushPromises();
    expect(area(wrapper, 'title').value).toBe('My title');
    wrapper.unmount();
  });

  it('shows a loading state and blocks double submission while pending', async () => {
    let resolve!: (r: Response) => void;
    regenReply = () => new Promise<Response>((r) => (resolve = r));
    const wrapper = await mountPage();
    const button = card(wrapper, 'title').get('[data-test="regenerate"]');
    await button.trigger('click');
    expect(button.attributes('disabled')).toBeDefined();
    expect(button.text()).toBe(en.regenerate.running);
    await button.trigger('click');
    expect(regenCalls()).toHaveLength(1);
    resolve(json(200, { result: DONE_JOB.result, usage: {} }));
    await flushPromises();
    expect(button.attributes('disabled')).toBeUndefined();
    wrapper.unmount();
  });

  it.each([
    ['job_not_done', 409, en.errors.codes.job_not_done],
    ['regeneration_in_progress', 409, en.errors.codes.regeneration_in_progress],
    ['invalid_section', 422, en.errors.codes.invalid_section],
    ['generation_failed', 502, en.errors.codes.generation_failed],
  ])('maps %s to a catalog message and keeps the previous text', async (code, status, message) => {
    regenReply = () => Promise.resolve(json(status, errorBody(code)));
    const wrapper = await mountPage();
    await card(wrapper, 'title').get('textarea').setValue('Edited title');
    await card(wrapper, 'title').get('[data-test="regenerate"]').trigger('click');
    await flushPromises();
    expect(card(wrapper, 'title').get('[role="alert"]').text()).toBe(message);
    expect(card(wrapper, 'title').text()).not.toContain('raw server text');
    expect(area(wrapper, 'title').value).toBe('Edited title');
    wrapper.unmount();
  });

  it('shows the expired message on 404', async () => {
    regenReply = () => Promise.resolve(json(404, errorBody('job_not_found')));
    const wrapper = await mountPage();
    await card(wrapper, 'title').get('[data-test="regenerate"]').trigger('click');
    await flushPromises();
    expect(card(wrapper, 'title').get('[role="alert"]').text()).toBe(en.job.expired);
    wrapper.unmount();
  });

  it('shows the generic message for non-API failures', async () => {
    regenReply = () => Promise.resolve(new Response('x', { status: 502 }));
    const wrapper = await mountPage();
    await card(wrapper, 'title').get('[data-test="regenerate"]').trigger('click');
    await flushPromises();
    expect(card(wrapper, 'title').get('[role="alert"]').text()).toBe(en.errors.generic);
    wrapper.unmount();
  });
});
