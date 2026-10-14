import { mount, type VueWrapper } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory } from 'vue-router';

import { createI18nInstance } from '@/i18n';
import en from '@/i18n/en.json';
import { createAppRouter } from '@/router';
import type { ToolModule } from '@/tools';
import * as tool from '@/tools/audio-extract/index';

function job(overrides: Record<string, unknown> = {}) {
  return {
    job_id: 'j1',
    tool_id: 'audio_extract',
    state: 'queued',
    upload: { offset: 500, length: 500 },
    queue_position: 1,
    failure_reason: null,
    result: null,
    ...overrides,
  };
}

function doneJob(result: Record<string, unknown> = {}) {
  return job({
    state: 'done',
    queue_position: null,
    result: {
      file: 'audio.mp3',
      download_name: 'trip.mp3',
      duration_s: 754.2,
      size_bytes: 18101234,
      bitrate_kbps: 192,
      ...result,
    },
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
let fetchMock: ReturnType<typeof vi.fn>;

function stubFetch() {
  fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/api/jobs/j1/cancel' && init?.method === 'POST') {
      return Promise.resolve(json(202, job({ state: 'cancelled', queue_position: null })));
    }
    if (url === '/api/config') {
      return Promise.resolve(
        json(200, { max_upload_bytes: 1000, upload_chunk_bytes: 256, job_ttl_hours: 24 }),
      );
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

async function mountAt(path: string): Promise<{ wrapper: VueWrapper; push: () => string }> {
  const router = createAppRouter(createMemoryHistory(), [tool as ToolModule]);
  await router.push(path);
  await router.isReady();
  const { default: App } = await import('@/App.vue');
  const wrapper = mount(App, { global: { plugins: [router, createI18nInstance()] } });
  await tick();
  await tick();
  return { wrapper, push: () => router.currentRoute.value.fullPath };
}

const mountJob = async () => (await mountAt('/tools/audio-extract/jobs/j1')).wrapper;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
  jobReplies = [() => json(200, job())];
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('audio extract module', () => {
  it('has the backend id and both routes, and the home-card labels', () => {
    expect(tool.id).toBe('audio_extract');
    expect(tool.routes.map((r) => r.path)).toEqual([
      '/tools/audio-extract',
      '/tools/audio-extract/jobs/:id',
    ]);
    expect(tool.labels).toEqual({
      name: 'tools.audio_extract.name',
      description: 'tools.audio_extract.description',
    });
  });

  it('renders the upload page with heading and intro', async () => {
    stubFetch();
    const { wrapper } = await mountAt('/tools/audio-extract');
    expect(wrapper.text()).toContain(en.audioExtract.heading);
    expect(wrapper.text()).toContain(en.audioExtract.intro);
    expect(wrapper.find('input[type="file"]').exists()).toBe(true);
    wrapper.unmount();
  });
});

describe('audio extract job page', () => {
  it('shows the stage and polls every 2 s until terminal', async () => {
    jobReplies = [
      () => json(200, job({ state: 'extracting', queue_position: null })),
      () => json(200, doneJob()),
    ];
    stubFetch();
    const wrapper = await mountJob();
    expect(wrapper.text()).toContain(en.job.state.extracting);
    expect(wrapper.find('[data-test="cancel"]').exists()).toBe(true);
    expect(jobGets()).toBe(1);
    await tick(2000);
    expect(jobGets()).toBe(2);
    await tick(4000);
    expect(jobGets()).toBe(2);
    wrapper.unmount();
  });

  it('shows duration, size and the download link when done, with no usage footer', async () => {
    jobReplies = [() => json(200, doneJob())];
    stubFetch();
    const wrapper = await mountJob();
    const link = wrapper.get('[data-test="download"]');
    expect(link.attributes('href')).toBe('/api/tools/audio_extract/jobs/j1/audio');
    expect(link.attributes('download')).toBeDefined();
    expect(link.text()).toBe(en.audioExtract.download);
    expect(wrapper.text()).toContain('12:34');
    expect(wrapper.text()).toContain('18.1 MB');
    expect(wrapper.find('[data-test="cancel"]').exists()).toBe(false);
    expect(wrapper.text()).not.toMatch(/cost|\$/i);
    wrapper.unmount();
  });

  it('formats an hour-long duration as h:mm:ss', async () => {
    jobReplies = [() => json(200, doneJob({ duration_s: 3725.4 }))];
    stubFetch();
    const wrapper = await mountJob();
    expect(wrapper.text()).toContain('1:02:05');
    wrapper.unmount();
  });

  it('navigates to the download URL on click when the job still exists', async () => {
    jobReplies = [() => json(200, doneJob())];
    stubFetch();
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign });
    const wrapper = await mountJob();
    await wrapper.get('[data-test="download"]').trigger('click');
    await tick();
    expect(assign).toHaveBeenCalledWith('/api/tools/audio_extract/jobs/j1/audio');
    wrapper.unmount();
  });

  it('shows the expired message instead of navigating when the job is gone', async () => {
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
    expect(wrapper.text()).toContain(en.job.expired);
    expect(wrapper.find('[data-test="download"]').exists()).toBe(false);
    expect(wrapper.text()).not.toContain('Raw server text.');
    wrapper.unmount();
  });

  it('shows the expired message for a 404 on load', async () => {
    jobReplies = [() => json(404, { error: { code: 'job_not_found', message: 'x' } })];
    stubFetch();
    const wrapper = await mountJob();
    expect(wrapper.text()).toContain(en.job.expired);
    wrapper.unmount();
  });

  it("shows the expired message for another tool's job", async () => {
    jobReplies = [() => json(200, { ...doneJob(), tool_id: 'youtube_prep' })];
    stubFetch();
    const wrapper = await mountJob();
    expect(wrapper.text()).toContain(en.job.expired);
    expect(wrapper.find('[data-test="download"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it('renders the no_audio failure from the catalog', async () => {
    jobReplies = [
      () =>
        json(
          200,
          job({
            state: 'failed',
            queue_position: null,
            failure_code: 'no_audio',
            failure_reason: 'Raw reason.',
          }),
        ),
    ];
    stubFetch();
    const wrapper = await mountJob();
    expect(wrapper.get('[data-test="failure"]').text()).toBe(en.job.failures.no_audio);
    expect(wrapper.find('[data-test="download"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="share"]').exists()).toBe(false);
    wrapper.unmount();
  });
});
