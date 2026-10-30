import { flushPromises, mount, type VueWrapper } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory } from 'vue-router';

import { createI18nInstance } from '@/i18n';
import en from '@/i18n/en.json';
import { createAppRouter } from '@/router';
import type { ToolModule } from '@/tools';
import * as tool from '@/tools/video-generation/index';

const CLIP_URL = '/api/tools/video_generation/jobs/j1/clip';
const INPUTS_URL = '/api/tools/video_generation/jobs/j1/inputs';
const THUMB_URL = '/api/tools/video_generation/jobs/j1/thumbnail';

function job(overrides: Record<string, unknown> = {}) {
  return {
    job_id: 'j1',
    tool_id: 'video_generation',
    state: 'queued',
    upload: null,
    queue_position: 2,
    failure_reason: null,
    result: null,
    ...overrides,
  };
}

function doneJob(overrides: Record<string, unknown> = {}) {
  const base = {
    file: 'clip.mp4',
    thumbnail: 'thumbnail.jpg',
    download_name: 'slow zoom.mp4',
    duration_s: 8,
    width: 1080,
    height: 1920,
    aspect_ratio: '9:16',
    resolution: '1080p',
    size_bytes: 7340032,
  };
  return job({
    state: 'done',
    queue_position: null,
    usage: {
      input_tokens: 0,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      audio_minutes: 0,
      video_output_tokens: 1234,
      cost_usd: 1.2,
    },
    result: base,
    ...overrides,
    ...(overrides.result ? { result: { ...base, ...(overrides.result as object) } } : {}),
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
let inputsReply: Reply;
let toolsReply: Reply;
let fetchMock: ReturnType<typeof vi.fn>;

function stubFetch() {
  fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/api/jobs/j1/cancel' && init?.method === 'POST') {
      return Promise.resolve(json(202, job({ state: 'cancelled', queue_position: null })));
    }
    if (url === INPUTS_URL) return Promise.resolve(inputsReply());
    if (url === '/api/tools') return Promise.resolve(toolsReply());
    const reply = jobReplies.length > 1 ? jobReplies.shift()! : jobReplies[0];
    return Promise.resolve(reply());
  });
  vi.stubGlobal('fetch', fetchMock);
}

const jobGets = () => fetchMock.mock.calls.filter(([url]) => url === '/api/jobs/j1').length;
const cancels = () => fetchMock.mock.calls.filter(([url]) => url === '/api/jobs/j1/cancel').length;

async function tick(ms = 0) {
  await vi.advanceTimersByTimeAsync(ms);
}

async function mountJob(): Promise<VueWrapper> {
  const router = createAppRouter(createMemoryHistory(), [tool as ToolModule]);
  await router.push('/tools/video-generation/jobs/j1');
  await router.isReady();
  const { default: App } = await import('@/App.vue');
  const wrapper = mount(App, { global: { plugins: [router, createI18nInstance()] } });
  await tick();
  await tick();
  return wrapper;
}

function stubNavigator(canShare: boolean) {
  vi.stubGlobal('navigator', { canShare: vi.fn(() => canShare), share: vi.fn() });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
  toolsReply = () =>
    json(200, [{ id: 'video_generation', name: 'Video Generation', description: 'x' }]);
  jobReplies = [() => json(200, job())];
  inputsReply = () => json(404, { error: { code: 'job_not_found', message: 'x' } });
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('video generation job page when the tool is not configured', () => {
  it('shows the not-found page and never asks for the job when /api/tools does not list the tool', async () => {
    toolsReply = () => json(200, [{ id: 'youtube_prep', name: 'YouTube Prep', description: 'x' }]);
    stubFetch();
    const wrapper = await mountJob();
    expect(wrapper.text()).toContain(en.notFound.title);
    expect(wrapper.text()).not.toContain(en.videoGeneration.expired);
    expect(jobGets()).toBe(0);
    await tick(6000);
    expect(jobGets()).toBe(0);
    wrapper.unmount();
  });
});

describe('video generation job page', () => {
  it('shows the queued stage with its position and polls every 2 s until terminal', async () => {
    jobReplies = [() => json(200, job()), () => json(200, doneJob())];
    stubFetch();
    const wrapper = await mountJob();
    expect(wrapper.text()).toContain(en.job.state.queued);
    expect(wrapper.text()).toContain('Position in queue: 2');
    expect(jobGets()).toBe(1);
    await tick(2000);
    expect(jobGets()).toBe(2);
    await tick(6000);
    expect(jobGets()).toBe(2);
    wrapper.unmount();
  });

  it('shows the tool-specific generating label, not the shared metadata one', async () => {
    jobReplies = [() => json(200, job({ state: 'generating', queue_position: null }))];
    stubFetch();
    const wrapper = await mountJob();
    expect(wrapper.get('.stage').text()).toBe(en.videoGeneration.stage.generating);
    expect(wrapper.text()).not.toContain(en.job.state.generating);
    wrapper.unmount();
  });

  it('never shows a queue position of 0', async () => {
    jobReplies = [() => json(200, job({ state: 'queued', queue_position: 0 }))];
    stubFetch();
    const wrapper = await mountJob();
    expect(wrapper.text()).not.toContain('Position in queue');
    wrapper.unmount();
  });

  it('cancels a queued job straight away, without the charge warning', async () => {
    stubFetch();
    const wrapper = await mountJob();
    await wrapper.get('[data-test="cancel"]').trigger('click');
    await flushPromises();
    expect(wrapper.text()).not.toContain(en.videoGeneration.cancelWarning);
    expect(cancels()).toBe(1);
    expect(wrapper.text()).toContain(en.job.state.cancelled);
    expect(wrapper.find('[data-test="cancel"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it('warns about the provider charge before cancelling a generating job', async () => {
    jobReplies = [() => json(200, job({ state: 'generating', queue_position: null }))];
    stubFetch();
    const wrapper = await mountJob();
    await wrapper.get('[data-test="cancel"]').trigger('click');
    expect(cancels()).toBe(0);
    expect(wrapper.get('[data-test="cancel-warning"]').text()).toContain(
      en.videoGeneration.cancelWarning,
    );
    await wrapper.get('[data-test="cancel-confirm"]').trigger('click');
    await flushPromises();
    expect(cancels()).toBe(1);
    expect(wrapper.text()).toContain(en.job.state.cancelled);
    expect(wrapper.find('[data-test="cancel-warning"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it('keeps generating when the warning is dismissed', async () => {
    jobReplies = [() => json(200, job({ state: 'generating', queue_position: null }))];
    stubFetch();
    const wrapper = await mountJob();
    await wrapper.get('[data-test="cancel"]').trigger('click');
    await wrapper.get('[data-test="cancel-keep"]').trigger('click');
    expect(cancels()).toBe(0);
    expect(wrapper.find('[data-test="cancel-warning"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="cancel"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it('closes the warning if the job finishes while it is open', async () => {
    jobReplies = [
      () => json(200, job({ state: 'generating', queue_position: null })),
      () => json(200, doneJob()),
    ];
    stubFetch();
    const wrapper = await mountJob();
    await wrapper.get('[data-test="cancel"]').trigger('click');
    await tick(2000);
    expect(wrapper.find('[data-test="cancel-warning"]').exists()).toBe(false);
    expect(wrapper.find('video').exists()).toBe(true);
    wrapper.unmount();
  });

  it('shows the preview, facts, actual cost and download when done', async () => {
    jobReplies = [() => json(200, doneJob())];
    stubFetch();
    stubNavigator(false);
    const wrapper = await mountJob();
    const video = wrapper.get('video');
    expect(video.attributes('src')).toBe(CLIP_URL);
    expect(video.attributes('poster')).toBe(THUMB_URL);
    expect(video.attributes('controls')).toBeDefined();
    expect(video.attributes('playsinline')).toBeDefined();
    expect((video.element as HTMLVideoElement).muted).toBe(true);
    expect(video.attributes('aria-label')).toBe(en.videoGeneration.result.preview);
    expect(wrapper.get('[data-test="duration"]').text()).toBe('8 s');
    expect(wrapper.get('[data-test="orientation"]').text()).toBe(
      en.videoGeneration.options.vertical,
    );
    expect(wrapper.get('[data-test="resolution"]').text()).toBe(en.videoGeneration.options.p1080);
    expect(wrapper.get('[data-test="cost"]').text()).toBe('$1.20');
    const link = wrapper.get('[data-test="download"]');
    expect(link.attributes('href')).toBe(CLIP_URL);
    expect(link.attributes('download')).toBe('slow zoom.mp4');
    expect(link.text()).toBe(en.videoGeneration.result.download);
    expect(wrapper.find('[data-test="cancel"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it('rounds the measured duration to whole seconds for display', async () => {
    jobReplies = [() => json(200, doneJob({ result: { duration_s: 8.041667 } }))];
    stubFetch();
    const wrapper = await mountJob();
    expect(wrapper.get('[data-test="duration"]').text()).toBe('8 s');
    wrapper.unmount();
  });

  it('rounds a duration just under a second boundary up', async () => {
    jobReplies = [() => json(200, doneJob({ result: { duration_s: 9.96 } }))];
    stubFetch();
    const wrapper = await mountJob();
    expect(wrapper.get('[data-test="duration"]').text()).toBe('10 s');
    wrapper.unmount();
  });

  it('shows a horizontal 720p clip and an unknown cost as not available', async () => {
    jobReplies = [
      () =>
        json(
          200,
          doneJob({
            usage: { cost_usd: null },
            result: {
              download_name: 'clip.mp4',
              duration_s: 5,
              aspect_ratio: '16:9',
              resolution: '720p',
            },
          }),
        ),
    ];
    stubFetch();
    const wrapper = await mountJob();
    expect(wrapper.get('[data-test="duration"]').text()).toBe('5 s');
    expect(wrapper.get('[data-test="orientation"]').text()).toBe(
      en.videoGeneration.options.horizontal,
    );
    expect(wrapper.get('[data-test="resolution"]').text()).toBe(en.videoGeneration.options.p720);
    expect(wrapper.get('[data-test="cost"]').text()).toBe(en.usage.notAvailable);
    wrapper.unmount();
  });

  it('offers Share when the browser can share files', async () => {
    jobReplies = [() => json(200, doneJob())];
    stubFetch();
    stubNavigator(true);
    const wrapper = await mountJob();
    expect(wrapper.find('[data-test="share"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it('hides Share when the browser cannot share files', async () => {
    jobReplies = [() => json(200, doneJob())];
    stubFetch();
    stubNavigator(false);
    const wrapper = await mountJob();
    expect(wrapper.find('[data-test="share"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it('shares the MP4 under its download name', async () => {
    jobReplies = [() => json(200, doneJob())];
    stubFetch();
    const share = vi.fn(() => Promise.resolve());
    vi.stubGlobal('navigator', { canShare: vi.fn(() => true), share });
    const wrapper = await mountJob();
    fetchMock.mockImplementationOnce(() => Promise.resolve(new Response(new Blob(['mp4']))));
    await wrapper.get('[data-test="share"]').trigger('click');
    await tick();
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledWith(CLIP_URL);
    const files = (share.mock.calls[0] as unknown as [{ files: File[] }])[0].files;
    expect(files[0].name).toBe('slow zoom.mp4');
    expect(files[0].type).toBe('video/mp4');
    wrapper.unmount();
  });

  it('navigates to the clip on download click when the job still exists', async () => {
    jobReplies = [() => json(200, doneJob())];
    stubFetch();
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign });
    const wrapper = await mountJob();
    await wrapper.get('[data-test="download"]').trigger('click');
    await tick();
    expect(assign).toHaveBeenCalledWith(CLIP_URL);
    wrapper.unmount();
  });

  it('shows the clip-specific expired message instead of downloading an expired clip', async () => {
    jobReplies = [
      () => json(200, doneJob()),
      () => json(404, { error: { code: 'job_not_found', message: 'Raw server text.' } }),
    ];
    stubFetch();
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign });
    const wrapper = await mountJob();
    await wrapper.get('[data-test="download"]').trigger('click');
    await tick();
    expect(assign).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain(en.videoGeneration.expired);
    expect(wrapper.text()).not.toContain(en.job.expired);
    expect(wrapper.text()).not.toContain('Raw server text.');
    expect(wrapper.find('video').exists()).toBe(false);
    wrapper.unmount();
  });

  it('shows a catalog error, not server text, when the download check fails', async () => {
    jobReplies = [
      () => json(200, doneJob()),
      () => json(500, { error: { code: 'internal_error', message: 'Raw server text.' } }),
    ];
    stubFetch();
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign });
    const wrapper = await mountJob();
    await wrapper.get('[data-test="download"]').trigger('click');
    await tick();
    expect(assign).not.toHaveBeenCalled();
    expect(wrapper.get('[role="alert"]').text()).not.toContain('Raw server text.');
    expect(wrapper.find('video').exists()).toBe(true);
    wrapper.unmount();
  });

  it('shows a catalog error when cancelling fails and keeps polling', async () => {
    stubFetch();
    fetchMock.mockImplementation((url: string, init?: RequestInit) =>
      Promise.resolve(
        url === '/api/jobs/j1/cancel' && init?.method === 'POST'
          ? json(409, { error: { code: 'job_not_cancellable', message: 'Raw server text.' } })
          : json(200, job()),
      ),
    );
    const wrapper = await mountJob();
    await wrapper.get('[data-test="cancel"]').trigger('click');
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).not.toContain('Raw server text.');
    expect(wrapper.find('[data-test="cancel"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it('shows the clip-specific expired message for a 404 on load', async () => {
    jobReplies = [() => json(404, { error: { code: 'job_not_found', message: 'x' } })];
    stubFetch();
    const wrapper = await mountJob();
    expect(wrapper.text()).toContain(en.videoGeneration.expired);
    expect(en.videoGeneration.expired).not.toMatch(/upload/i);
    wrapper.unmount();
  });

  it("shows the expired message for another tool's job", async () => {
    jobReplies = [() => json(200, { ...doneJob(), tool_id: 'youtube_prep' })];
    stubFetch();
    const wrapper = await mountJob();
    expect(wrapper.text()).toContain(en.videoGeneration.expired);
    expect(wrapper.find('video').exists()).toBe(false);
    wrapper.unmount();
  });

  it.each([
    'provider_out_of_credit',
    'content_blocked',
    'provider_timeout',
    'invalid_picture',
    'provider_unavailable',
    'resume_impossible',
    'video_failed',
  ] as const)('renders failure code %s from the catalog', async (code) => {
    jobReplies = [
      () =>
        json(
          200,
          job({
            state: 'failed',
            queue_position: null,
            failure_code: code,
            failure_reason: 'Raw provider text.',
          }),
        ),
    ];
    stubFetch();
    const wrapper = await mountJob();
    expect(wrapper.get('[data-test="failure"]').text()).toBe(en.job.failures[code]);
    expect(wrapper.text()).not.toContain('Raw provider text.');
    expect(wrapper.find('video').exists()).toBe(false);
    expect(wrapper.find('[data-test="download"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="cancel"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it('falls back to the generic message for an unknown failure code', async () => {
    jobReplies = [
      () => json(200, job({ state: 'failed', queue_position: null, failure_code: 'brand_new' })),
    ];
    stubFetch();
    const wrapper = await mountJob();
    expect(wrapper.get('[data-test="failure"]').text()).toBe(en.errors.generic);
    wrapper.unmount();
  });

  it('shows a cancelled job without actions', async () => {
    jobReplies = [() => json(200, job({ state: 'cancelled', queue_position: null }))];
    stubFetch();
    const wrapper = await mountJob();
    expect(wrapper.text()).toContain(en.job.state.cancelled);
    expect(wrapper.find('[data-test="cancel"]').exists()).toBe(false);
    expect(wrapper.find('video').exists()).toBe(false);
    wrapper.unmount();
  });
});

describe('video generation job page: Try again', () => {
  const INPUTS = {
    prompt: 'slow zoom',
    duration_s: 8,
    aspect_ratio: '9:16',
    resolution: '1080p',
    pictures: ['/api/tools/video_generation/jobs/j1/pictures/1'],
  };
  const tryAgain = (wrapper: VueWrapper) => wrapper.find('[data-test="try-again"]');
  const inputsGets = () => fetchMock.mock.calls.filter(([url]) => url === INPUTS_URL).length;

  it.each([
    ['done', doneJob()],
    ['failed', job({ state: 'failed', queue_position: null, failure_reason: 'x' })],
    ['cancelled', job({ state: 'cancelled', queue_position: null })],
  ])('offers Try again on a %s job whose inputs are still served', async (_state, body) => {
    jobReplies = [() => json(200, body)];
    inputsReply = () => json(200, INPUTS);
    stubFetch();
    const wrapper = await mountJob();
    const link = tryAgain(wrapper);
    expect(link.exists()).toBe(true);
    expect(link.text()).toBe(en.videoGeneration.result.tryAgain);
    expect(link.attributes('href')).toBe('/tools/video-generation?from=j1');
    expect(inputsGets()).toBe(1);
    wrapper.unmount();
  });

  it.each([
    ['done', doneJob()],
    ['failed', job({ state: 'failed', queue_position: null, failure_reason: 'x' })],
    ['cancelled', job({ state: 'cancelled', queue_position: null })],
  ])('hides Try again on a %s job whose inputs are gone', async (_state, body) => {
    jobReplies = [() => json(200, body)];
    stubFetch();
    const wrapper = await mountJob();
    expect(inputsGets()).toBe(1);
    expect(tryAgain(wrapper).exists()).toBe(false);
    wrapper.unmount();
  });

  it('does not ask for the inputs of a job that is still running', async () => {
    jobReplies = [() => json(200, job({ state: 'generating', queue_position: null }))];
    inputsReply = () => json(200, INPUTS);
    stubFetch();
    const wrapper = await mountJob();
    expect(inputsGets()).toBe(0);
    expect(tryAgain(wrapper).exists()).toBe(false);
    wrapper.unmount();
  });

  it('offers Try again once a running job finishes', async () => {
    jobReplies = [
      () => json(200, job({ state: 'generating', queue_position: null })),
      () => json(200, doneJob()),
    ];
    inputsReply = () => json(200, INPUTS);
    stubFetch();
    const wrapper = await mountJob();
    expect(tryAgain(wrapper).exists()).toBe(false);
    await tick(2000);
    await flushPromises();
    expect(tryAgain(wrapper).exists()).toBe(true);
    wrapper.unmount();
  });

  it('hides Try again, quietly, when the inputs cannot be read right now', async () => {
    jobReplies = [() => json(200, doneJob())];
    inputsReply = () => json(500, { error: { code: 'internal_error', message: 'boom' } });
    stubFetch();
    const wrapper = await mountJob();
    expect(tryAgain(wrapper).exists()).toBe(false);
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
    wrapper.unmount();
  });
});
