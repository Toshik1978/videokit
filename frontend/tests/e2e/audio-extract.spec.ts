import { expect, test } from '@playwright/test';

const video = process.env.VIDEOKIT_E2E_VIDEO ?? '';

test('upload a video, get an MP3 named after it, on a phone-sized screen', async ({
  page,
  browserName,
}) => {
  expect(video).not.toBe('');
  await page.goto('/tools/audio-extract');
  const input = page.locator('input[type=file]');
  await expect(input).toBeEnabled();
  await input.setInputFiles(video);
  await page.waitForURL(/\/tools\/audio-extract\/jobs\//);

  const download = page.getByTestId('download');
  await expect(download).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId('duration')).toBeVisible();

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);

  const box = await download.boundingBox();
  expect(box?.width).toBeGreaterThanOrEqual(44);
  expect(box?.height).toBeGreaterThanOrEqual(44);

  // The link triggers a navigation to an attachment; WebKit does not always surface it as a
  // download event in mobile emulation, so the file name is checked on the response there.
  if (browserName === 'webkit') {
    const response = await page.request.get((await download.getAttribute('href')) ?? '');
    expect(response.headers()['content-type']).toBe('audio/mpeg');
    expect(response.headers()['content-disposition']).toContain('clip.mp3');
    return;
  }
  const [saved] = await Promise.all([page.waitForEvent('download'), download.click()]);
  expect(saved.suggestedFilename()).toBe('clip.mp3');
});
