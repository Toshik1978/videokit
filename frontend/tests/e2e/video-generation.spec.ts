import { deflateSync } from 'node:zlib';

import { expect, type Page, test } from '@playwright/test';

import { expectNoHorizontalScroll, expectTouchTargets, NARROW } from './helpers';

const PROMPT = 'A cat walks across a sunny roof';
const SLOW_PROMPT = 'A slow sunrise over the sea'; // the e2e provider never finishes a "slow" prompt

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});

function crc32(bytes: Buffer): number {
  let c = 0xffffffff;
  for (const byte of bytes) c = CRC_TABLE[(c ^ byte) & 0xff]! ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type: string, data: Buffer): Buffer {
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const head = Buffer.alloc(4);
  head.writeUInt32BE(data.length);
  const tail = Buffer.alloc(4);
  tail.writeUInt32BE(crc32(body));
  return Buffer.concat([head, body, tail]);
}

/** A 16x16 solid-colour PNG, built here so no binary fixture lives in the repository. */
function solidPng(r: number, g: number, b: number): Buffer {
  const size = 16;
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header.set([8, 2, 0, 0, 0], 8); // 8-bit RGB, no interlace
  const row = Buffer.concat([Buffer.from([0]), Buffer.from(Array(size).fill([r, g, b]).flat())]);
  const raw = Buffer.concat(Array(size).fill(row) as Buffer[]);
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

const picture = (name: string, rgb: [number, number, number]) => ({
  name,
  mimeType: 'image/png',
  buffer: solidPng(...rgb),
});

async function addPictures(page: Page, names: Array<[string, [number, number, number]]>) {
  await page.getByTestId('picture-input').setInputFiles(names.map(([n, rgb]) => picture(n, rgb)));
  await expect(page.getByTestId('picture')).toHaveCount(names.length);
}

/** The browser share sheet is not available everywhere; stub it so the Share button renders. */
async function stubShare(page: Page): Promise<void> {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'canShare', { value: () => true, configurable: true });
    Object.defineProperty(navigator, 'share', {
      value: () => Promise.resolve(),
      configurable: true,
    });
  });
}

/** Records the file names appended as `pictures` to any form, in order (Playwright cannot read a file upload body). */
async function recordPictureOrder(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const names: string[] = [];
    (window as unknown as { __pictures: string[] }).__pictures = names;
    const append = FormData.prototype.append;
    FormData.prototype.append = function (this: FormData, ...args: [string, ...unknown[]]) {
      if (args[0] === 'pictures' && args[1] instanceof File) names.push(args[1].name);
      return (append as (...a: unknown[]) => void).apply(this, args);
    } as typeof FormData.prototype.append;
  });
}

async function openForm(page: Page): Promise<void> {
  await page.goto('/tools/video-generation');
  await expect(page.getByTestId('prompt')).toBeVisible();
}

test('two pictures, reorder, remove one, generate, then download the clip named after the prompt', async ({
  page,
  browserName,
}) => {
  await stubShare(page);
  await recordPictureOrder(page);
  await openForm(page);
  await page.getByTestId('prompt').fill(PROMPT);

  await addPictures(page, [
    ['red.png', [255, 0, 0]],
    ['green.png', [0, 255, 0]],
    ['blue.png', [0, 0, 255]],
  ]);
  await page.getByTestId('move-down').first().click(); // green, red, blue
  await page.getByTestId('picture').nth(2).getByTestId('remove').click(); // green, red
  await expect(page.getByTestId('picture')).toHaveCount(2);
  await expectNoHorizontalScroll(page);
  await expectTouchTargets(page);

  await page.getByTestId('generate').click();
  // Sent in the displayed order, without the removed one.
  expect(
    await page.evaluate(() => (window as unknown as { __pictures: string[] }).__pictures),
  ).toEqual(['green.png', 'red.png']);
  await page.waitForURL(/\/tools\/video-generation\/jobs\//);

  const download = page.getByTestId('download');
  await expect(download).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId('duration')).toBeVisible();
  await expect(page.getByTestId('share')).toBeVisible();
  await expectNoHorizontalScroll(page);
  await expectTouchTargets(page);

  const expectedName = 'A cat walks across a sunny roof.mp4';
  // The link navigates to an attachment; WebKit does not always surface it as a download event in
  // mobile emulation, so the file name is checked on the response there.
  if (browserName === 'webkit') {
    const response = await page.request.get((await download.getAttribute('href')) ?? '');
    expect(response.headers()['content-type']).toBe('video/mp4');
    expect(response.headers()['content-disposition']).toContain(expectedName);
    return;
  }
  const [saved] = await Promise.all([page.waitForEvent('download'), download.click()]);
  expect(saved.suggestedFilename()).toBe(expectedName);
});

test('the home page lists Video Generation and its card opens the form', async ({ page }) => {
  await page.goto('/');
  const card = page.locator('[data-test="tool-card"]', { hasText: 'Video Generation' });
  await expect(card).toBeVisible();
  await card.click();
  await expect(page).toHaveURL(/\/tools\/video-generation$/);
  await expect(page.getByTestId('prompt')).toBeVisible();
});

test('a started clip is in Recent clips after a reload, ready, and opens its page', async ({
  page,
}) => {
  const prompt = `${PROMPT} ${Date.now()}`;
  await openForm(page);
  await page.getByTestId('prompt').fill(prompt);
  await page.getByTestId('generate').click();
  await page.waitForURL(/\/tools\/video-generation\/jobs\//);
  const jobUrl = page.url();
  await expect(page.getByTestId('download')).toBeVisible({ timeout: 60_000 });

  await openForm(page);
  await page.reload();
  const clip = page.getByTestId('recent-clip').filter({ hasText: prompt });
  await expect(clip).toBeVisible();
  await expect(clip.getByTestId('recent-status')).toHaveText('Ready');
  await expect(clip.getByRole('img')).toBeVisible();
  await expect(page.getByTestId('retention')).toBeVisible();
  await expectNoHorizontalScroll(page);
  await expectTouchTargets(page);

  await clip.click();
  await expect(page).toHaveURL(jobUrl);
  await expect(page.getByTestId('download')).toBeVisible();
});

test.describe('at 360 px wide', () => {
  test.use({ viewport: NARROW });

  test('the form, the running job with its cancel warning and the finished job fit the screen', async ({
    page,
  }) => {
    await stubShare(page);
    await openForm(page);
    await page.getByTestId('prompt').fill(SLOW_PROMPT);
    await addPictures(page, [
      ['one.png', [200, 60, 60]],
      ['two.png', [60, 200, 60]],
    ]);
    await expectNoHorizontalScroll(page);
    await expectTouchTargets(page);

    await page.getByTestId('generate').click();
    await page.waitForURL(/\/tools\/video-generation\/jobs\//);
    const cancel = page.getByTestId('cancel');
    await expect(cancel).toBeVisible();
    await expectNoHorizontalScroll(page);
    await expectTouchTargets(page);

    // Once generating, cancelling asks first because the provider may still charge.
    await expect(page.getByText('Generating', { exact: false }).first()).toBeVisible();
    await cancel.click();
    await expect(page.getByTestId('cancel-warning')).toBeVisible();
    await expectNoHorizontalScroll(page);
    await expectTouchTargets(page);
    await page.getByTestId('cancel-confirm').click();
    await expect(page.getByTestId('cancel')).toHaveCount(0);
    await expectNoHorizontalScroll(page);
    await expectTouchTargets(page);

    await openForm(page);
    await page.getByTestId('prompt').fill(PROMPT);
    await page.getByTestId('generate').click();
    await expect(page.getByTestId('download')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId('share')).toBeVisible();
    await expectNoHorizontalScroll(page);
    await expectTouchTargets(page);

    // The list of the clips made above (cancelled and ready) fits the screen too.
    await openForm(page);
    await expect(page.getByTestId('recent-clip').first()).toBeVisible();
    await expect(page.getByTestId('recent-status').filter({ hasText: 'Cancelled' })).toBeVisible();
    await expectNoHorizontalScroll(page);
    await expectTouchTargets(page);
  });
});
