import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory } from 'vue-router';

import { ApiError, getJson, postJson } from '@/api/client';
import App from '@/App.vue';
import { createI18nInstance } from '@/i18n';
import en from '@/i18n/en.json';
import { createAppRouter } from '@/router';
import { collectTools, type ToolModule } from '@/tools';

const Stub = { template: '<p data-test="tool-page">tool</p>' };
const testTool: ToolModule = {
  id: 'test_tool',
  routes: [{ path: '/tools/test-tool', name: 'test_tool', component: Stub }],
};

function respond(status: number, body: string, contentType = 'application/json'): void {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue(new Response(body, { status, headers: { 'Content-Type': contentType } })),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe('tool discovery', () => {
  it('collects modules from a glob result and exposes their routes', () => {
    const tools = collectTools({ './test_tool/index.ts': testTool });
    expect(tools.map((t) => t.id)).toEqual(['test_tool']);
    expect(tools[0].routes[0].path).toBe('/tools/test-tool');
  });

  it('returns an empty catalog for an empty glob', () => {
    expect(collectTools({})).toEqual([]);
  });

  it('rejects duplicate tool ids', () => {
    expect(() => collectTools({ './a/index.ts': testTool, './b/index.ts': testTool })).toThrow(
      /duplicate/i,
    );
  });
});

describe('router', () => {
  const make = () => createAppRouter(createMemoryHistory(), [testTool]);

  it('resolves home', async () => {
    const router = make();
    await router.push('/');
    expect(router.currentRoute.value.name).toBe('home');
  });

  it('resolves tool routes', async () => {
    const router = make();
    await router.push('/tools/test-tool');
    expect(router.currentRoute.value.name).toBe('test_tool');
  });

  it('falls back to not-found for unknown paths', async () => {
    const router = make();
    await router.push('/nope/at/all');
    expect(router.currentRoute.value.name).toBe('not-found');
  });
});

describe('i18n', () => {
  it('uses the en catalog', () => {
    const i18n = createI18nInstance();
    expect(i18n.global.locale.value).toBe('en');
    expect(i18n.global.t('notFound.title')).toBe(en.notFound.title);
    expect(en.notFound.title).not.toBe('');
  });
});

describe('App', () => {
  it('renders the app title and the 404 page for unknown routes', async () => {
    const router = createAppRouter(createMemoryHistory(), []);
    await router.push('/missing');
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [router, createI18nInstance()] } });
    expect(wrapper.text()).toContain(en.app.title);
    expect(wrapper.text()).toContain(en.notFound.title);
  });

  it('renders the empty home page when no tools are available', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(new Response('[]', { headers: { 'Content-Type': 'application/json' } })),
    );
    const router = createAppRouter(createMemoryHistory(), []);
    await router.push('/');
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [router, createI18nInstance()] } });
    await flushPromises();
    expect(wrapper.text()).toContain(en.home.empty);
  });
});

describe('api client', () => {
  it('returns parsed JSON on success', async () => {
    respond(200, '{"ok":true}');
    await expect(getJson<{ ok: boolean }>('/api/health')).resolves.toEqual({ ok: true });
    expect(fetch).toHaveBeenCalledWith('/api/health', expect.objectContaining({ method: 'GET' }));
  });

  it('posts JSON bodies', async () => {
    respond(200, '{"id":"j1"}');
    await expect(postJson<{ id: string }>('/api/x', { a: 1 })).resolves.toEqual({ id: 'j1' });
    const init = vi.mocked(fetch).mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(init.body).toBe('{"a":1}');
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json');
  });

  it('throws ApiError parsed from the error envelope', async () => {
    respond(404, '{"error":{"code":"job_not_found","message":"No such job."}}');
    const err = await getJson('/api/jobs/x').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 404, code: 'job_not_found', message: 'No such job.' });
  });

  it('falls back to a generic code for non-JSON errors', async () => {
    respond(502, '<html>bad gateway</html>', 'text/html');
    const err = await getJson('/api/x').catch((e: unknown) => e);
    expect(err).toMatchObject({ status: 502, code: 'http_error' });
  });

  it('falls back when JSON lacks the envelope', async () => {
    respond(500, '{"detail":"x"}');
    const err = await postJson('/api/x', {}).catch((e: unknown) => e);
    expect(err).toMatchObject({ status: 500, code: 'http_error' });
  });
});
