import { flushPromises, mount, type VueWrapper } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory } from 'vue-router';

import UsageFooter from '@/components/UsageFooter.vue';
import { createI18nInstance } from '@/i18n';
import en from '@/i18n/en.json';
import { createAppRouter } from '@/router';
import type { ToolModule } from '@/tools';
import * as tool from '@/tools/youtube-prep/index';

const USAGE = {
  input_tokens: 51234,
  output_tokens: 5120,
  cache_read_tokens: 10,
  cache_write_tokens: 48000,
  audio_minutes: 12.5,
  video_output_tokens: 0,
  cost_usd: 0.31,
};
const PROVIDERS = { transcription: 'local', llm: 'anthropic:claude-opus-5-5' };

const RENDERED = {
  cover_titles: 'a',
  title: 'b',
  description: 'c',
  tags: 'd',
  hashtags: 'e',
  chapters: 'f',
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function mountFooter(props: InstanceType<typeof UsageFooter>['$props']): VueWrapper {
  return mount(UsageFooter, { props, global: { plugins: [createI18nInstance()] } });
}

afterEach(() => vi.unstubAllGlobals());

describe('UsageFooter', () => {
  it('shows providers, tokens, audio minutes and cost', () => {
    const text = mountFooter({ providers: PROVIDERS, usage: USAGE }).text();
    expect(text).toContain('local');
    expect(text).toContain('anthropic:claude-opus-5-5');
    expect(text).toContain('51,234');
    expect(text).toContain('5,120');
    expect(text).toContain('48,000');
    expect(text).toContain('12.5');
    expect(text).toContain('$0.31');
  });

  it('shows n/a when the cost is unknown', () => {
    const wrapper = mountFooter({ providers: PROVIDERS, usage: { ...USAGE, cost_usd: null } });
    expect(wrapper.get('[data-test="usage-cost"]').text()).toContain(en.usage.notAvailable);
  });

  it('keeps sub-cent costs visible', () => {
    const wrapper = mountFooter({ providers: PROVIDERS, usage: { ...USAGE, cost_usd: 0.0042 } });
    expect(wrapper.get('[data-test="usage-cost"]').text()).toContain('$0.0042');
  });

  it('copes with missing providers', () => {
    const text = mountFooter({ usage: USAGE }).text();
    expect(text).toContain('51,234');
  });
});

describe('usage footer on the job page', () => {
  async function mountPage(regenerateUsage: object): Promise<VueWrapper> {
    const done = {
      job_id: 'j1',
      tool_id: 'youtube_prep',
      state: 'done',
      upload: null,
      queue_position: null,
      failure_reason: null,
      providers: PROVIDERS,
      usage: USAGE,
      result: { language: 'en', notices: [], rendered: RENDERED },
    };
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, init?: RequestInit) =>
        Promise.resolve(
          init?.method === 'POST'
            ? json(200, { result: done.result, usage: regenerateUsage })
            : json(200, done),
        ),
      ),
    );
    const router = createAppRouter(createMemoryHistory(), [tool as ToolModule]);
    await router.push('/tools/youtube-prep/jobs/j1');
    await router.isReady();
    const { default: App } = await import('@/App.vue');
    const wrapper = mount(App, { global: { plugins: [router, createI18nInstance()] } });
    await flushPromises();
    return wrapper;
  }

  it('renders the footer for the job and updates it after a regeneration', async () => {
    const wrapper = await mountPage({ ...USAGE, input_tokens: 60000, cost_usd: 0.42 });
    const footer = wrapper.get('[data-test="usage-footer"]');
    expect(footer.text()).toContain('51,234');
    expect(footer.text()).toContain('$0.31');
    await wrapper.get('[data-test="card-title"] [data-test="regenerate"]').trigger('click');
    await flushPromises();
    expect(footer.text()).toContain('60,000');
    expect(footer.text()).toContain('$0.42');
    wrapper.unmount();
  });

  it('merges a partial usage payload into the shown usage', async () => {
    const wrapper = await mountPage({ output_tokens: 7000 });
    await wrapper.get('[data-test="card-title"] [data-test="regenerate"]').trigger('click');
    await flushPromises();
    const text = wrapper.get('[data-test="usage-footer"]').text();
    expect(text).toContain('7,000');
    expect(text).toContain('51,234');
    wrapper.unmount();
  });
});
