import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createI18nInstance } from '@/i18n';
import en from '@/i18n/en.json';
import ShareButton from '@/tools/audio-extract/ShareButton.vue';

const URL_ = '/api/tools/audio_extract/jobs/j1/audio';

let share: ReturnType<typeof vi.fn>;
let fetchMock: ReturnType<typeof vi.fn>;

function stubNavigator(canShare: boolean | undefined) {
  share = vi.fn(() => Promise.resolve());
  vi.stubGlobal('navigator', {
    ...(canShare === undefined ? {} : { canShare: vi.fn(() => canShare) }),
    share,
  });
}

function mountButton() {
  return mount(ShareButton, {
    props: { url: URL_, fileName: 'trip.mp3' },
    global: { plugins: [createI18nInstance()] },
  });
}

function domError(name: string): Error {
  const err = new Error(name);
  err.name = name;
  return err;
}

beforeEach(() => {
  fetchMock = vi.fn(() => Promise.resolve(new Response(new Blob(['mp3']), { status: 200 })));
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe('ShareButton', () => {
  it('is hidden without navigator.canShare', () => {
    stubNavigator(undefined);
    expect(mountButton().find('[data-test="share"]').exists()).toBe(false);
  });

  it('is hidden when the browser cannot share files', () => {
    stubNavigator(false);
    expect(mountButton().find('[data-test="share"]').exists()).toBe(false);
  });

  it('shares a file with the download name and MP3 type', async () => {
    stubNavigator(true);
    const wrapper = mountButton();
    const button = wrapper.get('[data-test="share"]');
    expect(button.text()).toBe(en.audioExtract.share);
    await button.trigger('click');
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledWith(URL_);
    const { files } = share.mock.calls[0][0] as { files: File[] };
    expect(files).toHaveLength(1);
    expect(files[0].name).toBe('trip.mp3');
    expect(files[0].type).toBe('audio/mpeg');
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
  });

  it('shows Preparing while fetching', async () => {
    stubNavigator(true);
    let release!: (r: Response) => void;
    fetchMock.mockReturnValueOnce(new Promise<Response>((r) => (release = r)));
    const wrapper = mountButton();
    await wrapper.get('[data-test="share"]').trigger('click');
    expect(wrapper.get('[data-test="share"]').text()).toBe(en.audioExtract.sharePreparing);
    expect(wrapper.get('[data-test="share"]').attributes('disabled')).toBeDefined();
    release(new Response(new Blob(['x'])));
    await flushPromises();
    expect(wrapper.get('[data-test="share"]').text()).toBe(en.audioExtract.share);
  });

  it('keeps the file on NotAllowedError and shares at once on the next tap', async () => {
    stubNavigator(true);
    share.mockRejectedValueOnce(domError('NotAllowedError'));
    const wrapper = mountButton();
    await wrapper.get('[data-test="share"]').trigger('click');
    await flushPromises();
    expect(wrapper.get('[data-test="share"]').text()).toBe(en.audioExtract.shareReady);
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
    await wrapper.get('[data-test="share"]').trigger('click');
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(share).toHaveBeenCalledTimes(2);
    expect(wrapper.get('[data-test="share"]').text()).toBe(en.audioExtract.share);
  });

  it('is silent when the sheet is dismissed', async () => {
    stubNavigator(true);
    share.mockRejectedValueOnce(domError('AbortError'));
    const wrapper = mountButton();
    await wrapper.get('[data-test="share"]').trigger('click');
    await flushPromises();
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
    expect(wrapper.get('[data-test="share"]').text()).toBe(en.audioExtract.share);
  });

  it('shows the generic message when the fetch fails', async () => {
    stubNavigator(true);
    fetchMock.mockResolvedValueOnce(new Response('nope', { status: 404 }));
    const wrapper = mountButton();
    await wrapper.get('[data-test="share"]').trigger('click');
    await flushPromises();
    expect(share).not.toHaveBeenCalled();
    expect(wrapper.get('[role="alert"]').text()).toBe(en.errors.generic);
  });

  it('shows the generic message for other share errors', async () => {
    stubNavigator(true);
    share.mockRejectedValueOnce(domError('DataError'));
    const wrapper = mountButton();
    await wrapper.get('[data-test="share"]').trigger('click');
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe(en.errors.generic);
  });
});
