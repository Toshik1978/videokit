import { flushPromises, mount, type VueWrapper } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory } from 'vue-router';

import { createI18nInstance } from '@/i18n';
import en from '@/i18n/en.json';
import { createAppRouter } from '@/router';
import type { ToolModule } from '@/tools';
import * as tool from '@/tools/video-generation/index';

const OPTIONS = {
  durations_s: [5, 6, 7, 8, 9, 10],
  default_duration_s: 8,
  aspect_ratios: ['9:16', '16:9'],
  resolutions: ['1080p', '720p'],
  max_pictures: 3,
  max_picture_bytes: 1000,
  picture_types: ['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif'],
  max_prompt_chars: 2000,
  price_per_second_usd: { '720p': 0.1, '1080p': 0.15 },
  price_per_picture_usd: 0.002,
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function jobBody(overrides: Record<string, unknown> = {}) {
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

let optionsReply: () => Response;
let createReply: () => Response;
let fetchMock: ReturnType<typeof vi.fn>;

function stubFetch() {
  fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/api/tools/video_generation/options') return Promise.resolve(optionsReply());
    if (url === '/api/tools/video_generation/jobs' && init?.method === 'POST') {
      return Promise.resolve(createReply());
    }
    if (url === '/api/jobs/j1/cancel') {
      return Promise.resolve(json(202, jobBody({ state: 'cancelled', queue_position: null })));
    }
    if (url === '/api/jobs/j1') return Promise.resolve(json(200, jobBody()));
    return Promise.resolve(json(404, { error: { code: 'job_not_found', message: 'x' } }));
  });
  vi.stubGlobal('fetch', fetchMock);
}

const posts = () => fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');

async function mountAt(path = '/tools/video-generation'): Promise<{
  wrapper: VueWrapper;
  current: () => string;
}> {
  const router = createAppRouter(createMemoryHistory(), [tool as ToolModule]);
  await router.push(path);
  await router.isReady();
  const { default: App } = await import('@/App.vue');
  const wrapper = mount(App, {
    attachTo: document.body,
    global: { plugins: [router, createI18nInstance()] },
  });
  await flushPromises();
  return { wrapper, current: () => router.currentRoute.value.fullPath };
}

function picture(name: string, type = 'image/png', size = 10): File {
  return new File([new Uint8Array(size)], name, { type });
}

async function pick(wrapper: VueWrapper, ...files: File[]): Promise<void> {
  const input = wrapper.get('[data-test="picture-input"]');
  Object.defineProperty(input.element, 'files', { value: files, configurable: true });
  await input.trigger('change');
  await flushPromises();
}

async function type(wrapper: VueWrapper, prompt: string): Promise<void> {
  await wrapper.get('[data-test="prompt"]').setValue(prompt);
}

const names = (wrapper: VueWrapper) =>
  wrapper
    .findAll('[data-test="picture"] img')
    .map((img) => img.attributes('src')?.replace('blob:', ''));

beforeEach(() => {
  optionsReply = () => json(200, OPTIONS);
  createReply = () => json(201, { job_id: 'j1' });
  Object.assign(URL, {
    createObjectURL: vi.fn((file: File) => `blob:${file.name}`),
    revokeObjectURL: vi.fn(),
  });
  stubFetch();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe('video generation module', () => {
  it('has the backend id, both routes and the home-card labels', () => {
    expect(tool.id).toBe('video_generation');
    expect(tool.routes.map((r) => r.path)).toEqual([
      '/tools/video-generation',
      '/tools/video-generation/jobs/:id',
    ]);
    expect(tool.labels).toEqual({
      name: 'tools.video_generation.name',
      description: 'tools.video_generation.description',
    });
  });
});

describe('video generation form', () => {
  it('shows the defaults: 8 s, vertical, 1080p, and offers the provider durations', async () => {
    const { wrapper } = await mountAt();
    expect(wrapper.text()).toContain(en.videoGeneration.heading);
    const select = wrapper.get<HTMLSelectElement>('[data-test="duration"]');
    expect(select.element.value).toBe('8');
    expect(select.findAll('option').map((o) => o.element.value)).toEqual(
      OPTIONS.durations_s.map(String),
    );
    expect(wrapper.get<HTMLInputElement>('[data-test="aspect-9:16"]').element.checked).toBe(true);
    expect(wrapper.get<HTMLInputElement>('[data-test="resolution-1080p"]').element.checked).toBe(
      true,
    );
    wrapper.unmount();
  });

  it('counts the prompt against the maximum and caps its length', async () => {
    const { wrapper } = await mountAt();
    await type(wrapper, 'hello');
    expect(wrapper.get('[data-test="prompt-count"]').text()).toBe('5 / 2000');
    expect(wrapper.get('[data-test="prompt"]').attributes('maxlength')).toBe('2000');
    wrapper.unmount();
  });

  it('disables Generate for an empty or blank prompt, with or without pictures', async () => {
    const { wrapper } = await mountAt();
    const generate = wrapper.get<HTMLButtonElement>('[data-test="generate"]');
    expect(generate.element.disabled).toBe(true);
    await type(wrapper, '   ');
    expect(generate.element.disabled).toBe(true);
    await pick(wrapper, picture('a.png'));
    expect(generate.element.disabled).toBe(true);
    await type(wrapper, 'a clip');
    expect(generate.element.disabled).toBe(false);
    wrapper.unmount();
  });

  it('updates the estimate live from duration, resolution and picture count', async () => {
    const { wrapper } = await mountAt();
    const estimate = () => wrapper.get('[data-test="estimate"]').text();
    expect(estimate()).toBe('Estimated cost: about $1.20');
    await wrapper.get('[data-test="duration"]').setValue('5');
    expect(estimate()).toBe('Estimated cost: about $0.75');
    await wrapper.get('[data-test="resolution-720p"]').setValue(true);
    expect(estimate()).toBe('Estimated cost: about $0.50');
    await pick(wrapper, picture('a.png'), picture('b.png'));
    expect(estimate()).toBe('Estimated cost: about $0.504');
    wrapper.unmount();
  });

  it('says the cost is unknown when the options carry no price', async () => {
    optionsReply = () => json(200, { ...OPTIONS, price_per_second_usd: null });
    const { wrapper } = await mountAt();
    expect(wrapper.get('[data-test="estimate"]').text()).toBe(en.videoGeneration.estimateUnknown);
    expect(wrapper.text()).not.toContain('$');
    wrapper.unmount();
  });

  it('shows a catalog message when the options cannot be loaded and offers no Generate', async () => {
    optionsReply = () => json(500, { error: { code: 'internal_error', message: 'boom' } });
    const { wrapper } = await mountAt();
    expect(wrapper.get('[role="alert"]').text()).toBe(en.errors.codes.internal_error);
    expect(wrapper.find('[data-test="generate"]').exists()).toBe(false);
    wrapper.unmount();
  });
});

describe('picture picker', () => {
  it('accepts the provider picture types and lists thumbnails with position numbers', async () => {
    const { wrapper } = await mountAt();
    expect(wrapper.get('[data-test="picture-input"]').attributes('accept')).toBe(
      OPTIONS.picture_types.join(','),
    );
    await pick(wrapper, picture('a.png'), picture('b.png'));
    const items = wrapper.findAll('[data-test="picture"]');
    expect(items).toHaveLength(2);
    expect(items[0]!.text()).toContain('1');
    expect(items[1]!.text()).toContain('2');
    expect(names(wrapper)).toEqual(['a.png', 'b.png']);
    wrapper.unmount();
  });

  it('moves and removes pictures, and sends them in the order shown', async () => {
    const { wrapper } = await mountAt();
    await pick(wrapper, picture('a.png'), picture('b.png'), picture('c.png'));
    await wrapper.findAll('[data-test="move-up"]')[2]!.trigger('click');
    expect(names(wrapper)).toEqual(['a.png', 'c.png', 'b.png']);
    await wrapper.findAll('[data-test="move-down"]')[0]!.trigger('click');
    expect(names(wrapper)).toEqual(['c.png', 'a.png', 'b.png']);
    await wrapper.findAll('[data-test="remove"]')[1]!.trigger('click');
    expect(names(wrapper)).toEqual(['c.png', 'b.png']);

    await type(wrapper, 'picture 2 then picture 1');
    await wrapper.get('[data-test="generate"]').trigger('click');
    await flushPromises();

    const [path, init] = posts()[0]!;
    expect(path).toBe('/api/tools/video_generation/jobs');
    const form = init.body as FormData;
    expect((form.getAll('pictures') as File[]).map((f) => f.name)).toEqual(['c.png', 'b.png']);
    wrapper.unmount();
  });

  it('disables the edge moves so a picture cannot leave the list', async () => {
    const { wrapper } = await mountAt();
    await pick(wrapper, picture('a.png'), picture('b.png'));
    const up = wrapper.findAll<HTMLButtonElement>('[data-test="move-up"]');
    const down = wrapper.findAll<HTMLButtonElement>('[data-test="move-down"]');
    expect(up[0]!.element.disabled).toBe(true);
    expect(down[1]!.element.disabled).toBe(true);
    expect(up[1]!.element.disabled).toBe(false);
    expect(down[0]!.element.disabled).toBe(false);
    wrapper.unmount();
  });

  it('gives every control an accessible name with the picture position', async () => {
    const { wrapper } = await mountAt();
    await pick(wrapper, picture('a.png'), picture('b.png'));
    expect(wrapper.findAll('[data-test="move-up"]')[1]!.attributes('aria-label')).toBe(
      'Move picture 2 up',
    );
    expect(wrapper.findAll('[data-test="remove"]')[0]!.attributes('aria-label')).toBe(
      'Remove picture 1',
    );
    wrapper.unmount();
  });

  it('refuses pictures over the maximum count with a catalog message', async () => {
    const { wrapper } = await mountAt();
    await pick(wrapper, picture('a.png'), picture('b.png'), picture('c.png'), picture('d.png'));
    expect(names(wrapper)).toEqual(['a.png', 'b.png', 'c.png']);
    expect(wrapper.get('[data-test="picture-error"]').text()).toBe('You can add up to 3 pictures.');
    expect(wrapper.get<HTMLButtonElement>('[data-test="add-pictures"]').element.disabled).toBe(
      true,
    );
    wrapper.unmount();
  });

  it('refuses a picture over the size limit, and keeps the valid ones', async () => {
    const { wrapper } = await mountAt();
    await pick(
      wrapper,
      picture('big.png', 'image/png', 1001),
      picture('ok.png', 'image/png', 1000),
    );
    expect(names(wrapper)).toEqual(['ok.png']);
    expect(wrapper.get('[data-test="picture-error"]').text()).toBe(
      'This picture is too large. The maximum size is 0.001 MB.',
    );
    wrapper.unmount();
  });

  it('refuses a file type the provider does not take, and an empty file', async () => {
    const { wrapper } = await mountAt();
    await pick(wrapper, picture('doc.pdf', 'application/pdf'));
    expect(names(wrapper)).toEqual([]);
    expect(wrapper.get('[data-test="picture-error"]').text()).toBe(
      en.videoGeneration.pictures.unsupported,
    );
    await pick(wrapper, picture('empty.png', 'image/png', 0));
    expect(names(wrapper)).toEqual([]);
    wrapper.unmount();
  });

  it('clears the message once a valid picture is added', async () => {
    const { wrapper } = await mountAt();
    await pick(wrapper, picture('doc.pdf', 'application/pdf'));
    await pick(wrapper, picture('a.png'));
    expect(wrapper.find('[data-test="picture-error"]').exists()).toBe(false);
    wrapper.unmount();
  });
});

describe('submitting', () => {
  it('sends the prompt and options as typed, appends no picture part without pictures, and opens the job', async () => {
    const { wrapper, current } = await mountAt();
    await type(wrapper, '  slow zoom  ');
    await wrapper.get('[data-test="duration"]').setValue('6');
    await wrapper.get('[data-test="aspect-16:9"]').setValue(true);
    await wrapper.get('[data-test="resolution-720p"]').setValue(true);
    await wrapper.get('[data-test="generate"]').trigger('click');
    await flushPromises();

    const form = posts()[0]![1].body as FormData;
    expect(form.get('prompt')).toBe('  slow zoom  ');
    expect(form.get('duration_s')).toBe('6');
    expect(form.get('aspect_ratio')).toBe('16:9');
    expect(form.get('resolution')).toBe('720p');
    expect(form.has('pictures')).toBe(false);
    await vi.waitFor(() => expect(current()).toBe('/tools/video-generation/jobs/j1'));
    wrapper.unmount();
  });

  it('shows the catalog message for an API error code and stays on the form', async () => {
    createReply = () => json(422, { error: { code: 'invalid_picture', message: 'server text' } });
    const { wrapper, current } = await mountAt();
    await type(wrapper, 'a clip');
    await wrapper.get('[data-test="generate"]').trigger('click');
    await flushPromises();
    const alert = wrapper.get('[data-test="submit-error"]');
    expect(alert.text()).toBe(en.errors.codes.invalid_picture);
    expect(alert.text()).not.toContain('server text');
    expect(current()).toBe('/tools/video-generation');
    expect(wrapper.get<HTMLButtonElement>('[data-test="generate"]').element.disabled).toBe(false);
    wrapper.unmount();
  });

  it('cannot be submitted twice while the request is in flight', async () => {
    let resolve!: (r: Response) => void;
    fetchMock.mockImplementation((_url: string, init?: RequestInit) =>
      init?.method === 'POST'
        ? new Promise<Response>((r) => (resolve = r))
        : Promise.resolve(json(200, OPTIONS)),
    );
    const { wrapper } = await mountAt();
    await type(wrapper, 'a clip');
    await wrapper.get('[data-test="generate"]').trigger('click');
    expect(wrapper.get<HTMLButtonElement>('[data-test="generate"]').element.disabled).toBe(true);
    expect(wrapper.get('[data-test="generate"]').text()).toBe(en.videoGeneration.submitting);
    await wrapper.get('[data-test="generate"]').trigger('click');
    expect(posts()).toHaveLength(1);
    resolve(json(201, { job_id: 'j1' }));
    await flushPromises();
    wrapper.unmount();
  });
});
