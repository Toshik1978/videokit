import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import JobStatus from '@/components/JobStatus.vue';
import { createI18nInstance } from '@/i18n';
import en from '@/i18n/en.json';

function summary(overrides: Record<string, unknown> = {}) {
  return {
    state: 'generating',
    upload: null,
    queue_position: null,
    failure_reason: null,
    failure_code: null,
    ...overrides,
  };
}

function mountStatus(job: ReturnType<typeof summary>, props: Record<string, unknown> = {}) {
  return mount(JobStatus, {
    props: { job, ...props },
    global: { plugins: [createI18nInstance()] },
  });
}

describe('JobStatus tool-specific options', () => {
  it('uses the shared stage label by default', () => {
    expect(mountStatus(summary()).get('.stage').text()).toBe(en.job.state.generating);
  });

  it('lets a tool override a stage label and leaves the other stages alone', () => {
    const labels = { generating: 'Custom stage' };
    expect(mountStatus(summary(), { stageLabels: labels }).get('.stage').text()).toBe(
      'Custom stage',
    );
    const queued = summary({ state: 'queued', queue_position: 1 });
    expect(mountStatus(queued, { stageLabels: labels }).get('.stage').text()).toBe(
      en.job.state.queued,
    );
  });

  it('shows the failure detail by default and hides it with showDetail false', () => {
    const failed = summary({
      state: 'failed',
      failure_code: 'internal_error',
      failure_reason: 'Raw reason.',
    });
    expect(mountStatus(failed).find('[data-test="failure-detail"]').exists()).toBe(true);
    expect(
      mountStatus(failed, { showDetail: false }).find('[data-test="failure-detail"]').exists(),
    ).toBe(false);
  });

  it('does not show a queue position below 1', () => {
    const wrapper = mountStatus(summary({ state: 'queued', queue_position: 0 }));
    expect(wrapper.text()).not.toContain('Position in queue');
  });
});
