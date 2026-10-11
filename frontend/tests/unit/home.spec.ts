import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory } from 'vue-router';

import { createI18nInstance } from '@/i18n';
import en from '@/i18n/en.json';
import HomePage from '@/pages/HomePage.vue';
import { createAppRouter } from '@/router';
import { type ToolModule, toolsKey } from '@/tools';

const Stub = { template: '<p>tool</p>' };
const makeTool = (id: string, path: string, labels?: ToolModule['labels']): ToolModule => ({
  id,
  routes: [{ path, name: id, component: Stub }],
  labels,
});

const yt = makeTool('youtube_prep', '/tools/youtube-prep');
const ytWithLabels = makeTool('youtube_prep', '/tools/youtube-prep', {
  name: 'tools.youtube_prep.name',
  description: 'tools.youtube_prep.description',
});
const backendTool = (id: string, name: string, description: string) => ({ id, name, description });

function respond(status: number, body: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  );
}

async function mountHome(modules: ToolModule[]) {
  const router = createAppRouter(createMemoryHistory(), modules);
  await router.push('/');
  await router.isReady();
  const wrapper = mount(HomePage, {
    global: {
      plugins: [router, createI18nInstance()],
      provide: { [toolsKey as symbol]: modules },
    },
  });
  return wrapper;
}

afterEach(() => vi.unstubAllGlobals());

describe('HomePage', () => {
  it('shows a loading state until tools arrive', async () => {
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => undefined)));
    const wrapper = await mountHome([yt]);
    expect(wrapper.text()).toContain(en.home.loading);
  });

  it('renders a card per backend tool that has a frontend module', async () => {
    respond(200, [
      backendTool('youtube_prep', 'YouTube Prep', 'Title, tags and chapters.'),
      backendTool('backend_only', 'Backend Only', 'No frontend module.'),
    ]);
    const wrapper = await mountHome([yt]);
    await flushPromises();
    const cards = wrapper.findAll('[data-test="tool-card"]');
    expect(cards).toHaveLength(1);
    expect(cards[0].text()).toContain('YouTube Prep');
    expect(cards[0].text()).toContain('Title, tags and chapters.');
    expect(cards[0].attributes('href')).toBe('/tools/youtube-prep');
    expect(wrapper.text()).not.toContain('Backend Only');
    expect(fetch).toHaveBeenCalledWith('/api/tools', expect.anything());
  });

  it('takes name and description from the catalog keys the tool module declares', async () => {
    respond(200, [backendTool('youtube_prep', 'API name', 'API description.')]);
    const wrapper = await mountHome([ytWithLabels]);
    await flushPromises();
    const card = wrapper.get('[data-test="tool-card"]');
    expect(card.text()).toContain(en.tools.youtube_prep.name);
    expect(card.text()).toContain(en.tools.youtube_prep.description);
    expect(card.text()).not.toContain('API name');
    expect(card.text()).not.toContain('API description.');
  });

  it('falls back to the API values when a declared catalog key is missing', async () => {
    respond(200, [backendTool('youtube_prep', 'API name', 'API description.')]);
    const missing = makeTool('youtube_prep', '/tools/youtube-prep', {
      name: 'tools.nope.name',
      description: 'tools.nope.description',
    });
    const wrapper = await mountHome([missing]);
    await flushPromises();
    const card = wrapper.get('[data-test="tool-card"]');
    expect(card.text()).toContain('API name');
    expect(card.text()).toContain('API description.');
  });

  it('declares catalog keys in the YouTube Prep module', async () => {
    const module = await import('@/tools/youtube-prep/index');
    expect(module.labels).toEqual({
      name: 'tools.youtube_prep.name',
      description: 'tools.youtube_prep.description',
    });
  });

  it('shows a test-only tool without any shared-code change', async () => {
    respond(200, [backendTool('test_tool', 'Test Tool', 'Only for tests.')]);
    const wrapper = await mountHome([yt, makeTool('test_tool', '/tools/test-tool')]);
    await flushPromises();
    const card = wrapper.get('[data-test="tool-card"]');
    expect(card.text()).toContain('Test Tool');
    expect(card.attributes('href')).toBe('/tools/test-tool');
  });

  it('shows the empty state when no tool can be shown', async () => {
    respond(200, [backendTool('backend_only', 'Backend Only', 'x')]);
    const wrapper = await mountHome([yt]);
    await flushPromises();
    expect(wrapper.findAll('[data-test="tool-card"]')).toHaveLength(0);
    expect(wrapper.text()).toContain(en.home.empty);
  });

  it('shows a generic error when the request fails', async () => {
    respond(500, { detail: 'boom' });
    const wrapper = await mountHome([yt]);
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe(en.errors.generic);
    expect(wrapper.findAll('[data-test="tool-card"]')).toHaveLength(0);
  });

  it('shows a generic error when the network is down', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')));
    const wrapper = await mountHome([yt]);
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe(en.errors.generic);
  });
});
