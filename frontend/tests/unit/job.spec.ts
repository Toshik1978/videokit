import { mount, type VueWrapper } from '@vue/test-utils';
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
  description: 'Para one.\n\nPara two.',
  tags: 'a, b',
  hashtags: '#a #b #c',
  chapters: '0:00 "Intro"\n1:00 "Middle"\n2:00 "End"',
};

function job(overrides: Record<string, unknown> = {}) {
  return {
    job_id: 'j1',
    tool_id: 'youtube_prep',
    state: 'queued',
    upload: { offset: 500, length: 500 },
    queue_position: 2,
    failure_reason: null,
    result: null,
    ...overrides,
  };
}

function doneJob(notices: string[] = []) {
  return job({
    state: 'done',
    queue_position: null,
    result: { language: 'en', notices, rendered: RENDERED },
  });
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

type Reply = () => Response;
let jobReplies: Reply[];
let cancelReply: Reply;
let fetchMock: ReturnType<typeof vi.fn>;

function stubFetch() {
  fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/api/jobs/j1/cancel' && init?.method === 'POST') {
      return Promise.resolve(cancelReply());
    }
    const reply = jobReplies.length > 1 ? jobReplies.shift()! : jobReplies[0];
    return Promise.resolve(reply());
  });
  vi.stubGlobal('fetch', fetchMock);
}

const jobGets = () => fetchMock.mock.calls.filter(([url]) => url === '/api/jobs/j1').length;

async function tick(ms = 0) {
  await vi.advanceTimersByTimeAsync(ms);
}

async function mountPage(): Promise<VueWrapper> {
  const router = createAppRouter(createMemoryHistory(), [tool as ToolModule]);
  await router.push('/tools/youtube-prep/jobs/j1');
  await router.isReady();
  const { default: App } = await import('@/App.vue');
  const wrapper = mount(App, { global: { plugins: [router, createI18nInstance()] } });
  await tick();
  await tick();
  return wrapper;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
  jobReplies = [() => json(200, job())];
  cancelReply = () => json(202, job({ state: 'cancelled', queue_position: null }));
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('job route', () => {
  it('is registered under the kebab-case jobs path', () => {
    expect(tool.routes.map((r) => r.path)).toContain('/tools/youtube-prep/jobs/:id');
  });
});

describe('job status and polling', () => {
  it('shows stage and queue position, polls every 2 s while not terminal', async () => {
    stubFetch();
    const wrapper = await mountPage();
    expect(wrapper.text()).toContain(en.job.state.queued);
    expect(wrapper.text()).toContain('2');
    expect(jobGets()).toBe(1);
    await tick(2000);
    expect(jobGets()).toBe(2);
    await tick(2000);
    expect(jobGets()).toBe(3);
    wrapper.unmount();
  });

  it('shows the upload percentage while uploading', async () => {
    jobReplies = [
      () => json(200, job({ state: 'uploading', upload: { offset: 250, length: 500 } })),
    ];
    stubFetch();
    const wrapper = await mountPage();
    expect(wrapper.text()).toContain('50%');
    wrapper.unmount();
  });

  it('stops polling at a terminal state and renders the failure from the catalog', async () => {
    jobReplies = [
      () => json(200, job({ state: 'transcribing', queue_position: null })),
      () =>
        json(
          200,
          job({
            state: 'failed',
            failure_code: 'not_a_video',
            failure_reason: 'Raw server reason.',
          }),
        ),
    ];
    stubFetch();
    const wrapper = await mountPage();
    expect(wrapper.find('[data-test="cancel"]').exists()).toBe(true);
    await tick(2000);
    const failure = wrapper.get('[data-test="failure"]');
    expect(failure.text()).toBe(en.job.failures.not_a_video);
    expect(failure.text()).not.toContain('Raw server reason.');
    expect(wrapper.get('[data-test="failure-detail"]').text()).toContain('Raw server reason.');
    expect(wrapper.find('[data-test="cancel"]').exists()).toBe(false);
    const calls = jobGets();
    await tick(10000);
    expect(jobGets()).toBe(calls);
    wrapper.unmount();
  });

  it.each([
    'not_a_video',
    'server_restarted',
    'extraction_failed',
    'transcription_failed',
    'generation_failed',
    'generation_invalid',
    'internal_error',
  ] as const)('renders failure code %s from the catalog', async (code) => {
    jobReplies = [
      () => json(200, job({ state: 'failed', failure_code: code, failure_reason: 'x' })),
    ];
    stubFetch();
    const wrapper = await mountPage();
    expect(wrapper.get('[data-test="failure"]').text()).toBe(en.job.failures[code]);
    wrapper.unmount();
  });

  it.each([null, 'some_future_code'])(
    'falls back to the generic message for failure code %s',
    async (code) => {
      jobReplies = [
        () => json(200, job({ state: 'failed', failure_code: code, failure_reason: 'Detail.' })),
      ];
      stubFetch();
      const wrapper = await mountPage();
      expect(wrapper.get('[data-test="failure"]').text()).toBe(en.errors.generic);
      expect(wrapper.get('[data-test="failure-detail"]').text()).toContain('Detail.');
      wrapper.unmount();
    },
  );

  it('shows a coded polling error from the catalog, not the server message', async () => {
    jobReplies = [
      () => json(500, { error: { code: 'internal_error', message: 'Raw server text.' } }),
      () => json(200, job()),
    ];
    stubFetch();
    const wrapper = await mountPage();
    expect(wrapper.get('[role="alert"]').text()).toBe(en.errors.codes.internal_error);
    wrapper.unmount();
  });

  it('stops polling when unmounted', async () => {
    stubFetch();
    const wrapper = await mountPage();
    wrapper.unmount();
    const calls = jobGets();
    await tick(10000);
    expect(jobGets()).toBe(calls);
  });

  it('shows "expired" on 404 and stops polling', async () => {
    jobReplies = [() => json(404, { error: { code: 'job_not_found', message: 'No such job.' } })];
    stubFetch();
    const wrapper = await mountPage();
    expect(wrapper.text()).toContain(en.job.expired);
    expect(en.job.expired).toMatch(/upload the video again/i);
    await tick(10000);
    expect(jobGets()).toBe(1);
    wrapper.unmount();
  });

  it("shows the expired message for another tool's job and stops polling", async () => {
    jobReplies = [
      () =>
        json(200, {
          ...job({ state: 'done', queue_position: null }),
          tool_id: 'audio_extract',
          result: { duration_s: 10, size_bytes: 1000, download_name: 'clip.mp3' },
        }),
    ];
    stubFetch();
    const wrapper = await mountPage();
    expect(wrapper.text()).toContain(en.job.expired);
    expect(wrapper.find('textarea').exists()).toBe(false);
    await tick(10000);
    expect(jobGets()).toBe(1);
    wrapper.unmount();
  });

  it('keeps polling after a transient error', async () => {
    jobReplies = [() => new Response('x', { status: 502 }), () => json(200, job())];
    stubFetch();
    const wrapper = await mountPage();
    expect(wrapper.get('[role="alert"]').text()).toBe(en.errors.generic);
    await tick(2000);
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
    expect(wrapper.text()).toContain(en.job.state.queued);
    wrapper.unmount();
  });
});

describe('cancel', () => {
  it('posts to the cancel endpoint and shows the cancelled state', async () => {
    stubFetch();
    const wrapper = await mountPage();
    await wrapper.get('[data-test="cancel"]').trigger('click');
    await tick();
    const cancelCall = fetchMock.mock.calls.find(([url]) => url === '/api/jobs/j1/cancel');
    expect(cancelCall).toBeTruthy();
    expect(wrapper.text()).toContain(en.job.state.cancelled);
    expect(wrapper.find('[data-test="cancel"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it('shows a catalog error when cancelling fails with a coded error', async () => {
    cancelReply = () =>
      json(409, { error: { code: 'not_cancellable', message: 'Cannot cancel this job.' } });
    stubFetch();
    const wrapper = await mountPage();
    await wrapper.get('[data-test="cancel"]').trigger('click');
    await tick();
    expect(wrapper.get('[role="alert"]').text()).toBe(en.errors.codes.not_cancellable);
    wrapper.unmount();
  });

  it('shows the generic error for an unknown error code', async () => {
    cancelReply = () => json(409, { error: { code: 'brand_new_code', message: 'Raw text.' } });
    stubFetch();
    const wrapper = await mountPage();
    await wrapper.get('[data-test="cancel"]').trigger('click');
    await tick();
    expect(wrapper.get('[role="alert"]').text()).toBe(en.errors.generic);
    wrapper.unmount();
  });
});

describe('result cards', () => {
  beforeEach(() => {
    jobReplies = [() => json(200, doneJob())];
  });

  it('renders six cards with textareas holding the rendered text', async () => {
    stubFetch();
    const wrapper = await mountPage();
    const keys = ['cover_titles', 'title', 'description', 'tags', 'hashtags', 'chapters'] as const;
    for (const key of keys) {
      const area = wrapper.get(`[data-test="card-${key}"] textarea`);
      expect((area.element as HTMLTextAreaElement).value).toBe(RENDERED[key]);
    }
    expect(wrapper.findAll('textarea')).toHaveLength(6);
    wrapper.unmount();
  });

  it('keeps edits local: no request is sent when editing', async () => {
    stubFetch();
    const wrapper = await mountPage();
    const before = fetchMock.mock.calls.length;
    await wrapper.get('[data-test="card-title"] textarea').setValue('Edited');
    await tick(5000);
    expect(fetchMock.mock.calls.length).toBe(before);
    expect(
      (wrapper.get('[data-test="card-title"] textarea').element as HTMLTextAreaElement).value,
    ).toBe('Edited');
    wrapper.unmount();
  });

  it('copies the edited text with the clipboard API', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    stubFetch();
    const wrapper = await mountPage();
    await wrapper.get('[data-test="card-title"] textarea').setValue('Edited');
    await wrapper.get('[data-test="card-title"] [data-test="copy"]').trigger('click');
    await tick();
    expect(writeText).toHaveBeenCalledWith('Edited');
    expect(wrapper.get('[data-test="card-title"]').text()).toContain(en.job.copied);
    wrapper.unmount();
  });

  it('falls back to selecting the text and shows a press-copy hint', async () => {
    vi.stubGlobal('navigator', {});
    stubFetch();
    const wrapper = await mountPage();
    const card = wrapper.get('[data-test="card-tags"]');
    const area = card.get('textarea').element as HTMLTextAreaElement;
    const select = vi.spyOn(area, 'select');
    await card.get('[data-test="copy"]').trigger('click');
    await tick();
    expect(select).toHaveBeenCalled();
    expect(card.text()).toContain(en.job.pressCopy);
    wrapper.unmount();
  });

  it('falls back when the clipboard API rejects', async () => {
    vi.stubGlobal('navigator', {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    });
    stubFetch();
    const wrapper = await mountPage();
    const card = wrapper.get('[data-test="card-tags"]');
    await card.get('[data-test="copy"]').trigger('click');
    await tick();
    expect(card.text()).toContain(en.job.pressCopy);
    wrapper.unmount();
  });

  it('shows no notices when there are none', async () => {
    stubFetch();
    const wrapper = await mountPage();
    expect(wrapper.find('[data-test="notice"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it('shows the no-speech notice above the cards', async () => {
    jobReplies = [() => json(200, doneJob(['no_speech']))];
    stubFetch();
    const wrapper = await mountPage();
    const text = wrapper.findAll('[data-test="notice"]').map((n) => n.text());
    expect(text).toEqual([en.job.notices.no_speech]);
    wrapper.unmount();
  });

  it('shows the too-short notice inside the chapters card instead of an empty textarea', async () => {
    const tooShort = job({
      state: 'done',
      queue_position: null,
      result: {
        language: 'en',
        notices: ['no_speech', 'too_short_for_chapters'],
        rendered: { ...RENDERED, chapters: '' },
      },
    });
    jobReplies = [() => json(200, tooShort)];
    stubFetch();
    const wrapper = await mountPage();
    const chapters = wrapper.get('[data-test="card-chapters"]');
    expect(chapters.get('[data-test="card-notice"]').text()).toBe(
      en.job.notices.too_short_for_chapters,
    );
    expect(chapters.find('textarea').exists()).toBe(false);
    expect(chapters.find('[data-test="copy"]').exists()).toBe(false);
    expect(chapters.find('[data-test="regenerate"]').exists()).toBe(false);
    expect(wrapper.findAll('[data-test="notice"]').map((n) => n.text())).toEqual([
      en.job.notices.no_speech,
    ]);
    // The other cards are unaffected.
    expect(wrapper.findAll('textarea')).toHaveLength(5);
    expect(wrapper.get('[data-test="card-title"]').find('[data-test="copy"]').exists()).toBe(true);
    wrapper.unmount();
  });
});
