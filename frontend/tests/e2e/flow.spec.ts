import { expect, type Locator, type Page, test } from '@playwright/test';

import { expectNoHorizontalScroll, expectTouchTargets, NARROW } from './helpers';

const SECTIONS = ['cover_titles', 'title', 'description', 'tags', 'hashtags', 'chapters'];
const MAX_CLICKS = 9; // SC-005

const video = process.env.VIDEOKIT_E2E_VIDEO ?? '';

type WithCopied = { __copied: string[] };

/** Replaces the clipboard with a recorder, or removes it (what a non-HTTPS origin looks like). */
async function stubClipboard(page: Page, mode: 'record' | 'missing'): Promise<void> {
  await page.addInitScript((m) => {
    const writes: string[] = [];
    (window as unknown as WithCopied).__copied = writes;
    const clipboard =
      m === 'missing'
        ? undefined
        : {
            writeText: (text: string) => {
              writes.push(text);
              return Promise.resolve();
            },
          };
    Object.defineProperty(navigator, 'clipboard', { value: clipboard, configurable: true });
  }, mode);
}

const copied = (page: Page): Promise<string[]> =>
  page.evaluate(() => (window as unknown as WithCopied).__copied);

/** Uploads the fixture from the YouTube Prep page and waits for the finished job page. */
async function uploadAndWaitForResult(page: Page): Promise<void> {
  const input = page.locator('input[type=file]');
  await expect(input).toBeEnabled();
  await input.setInputFiles(video);
  await page.waitForURL(/\/tools\/youtube-prep\/jobs\//);
  await expect(page.getByTestId('card-chapters')).toBeVisible({ timeout: 60_000 });
}

test('home to six copied sections in at most 9 clicks, with an edit', async ({ page }) => {
  expect(video).not.toBe('');
  await stubClipboard(page, 'record');
  let clicks = 0;
  const click = async (target: Locator): Promise<void> => {
    clicks += 1;
    await target.click();
  };

  await page.goto('/');
  await click(page.locator('[data-test=tool-card][href="/tools/youtube-prep"]'));
  await expect(page).toHaveURL(/\/tools\/youtube-prep$/);
  await uploadAndWaitForResult(page); // the file picker and the wait are not clicks

  // Edit one section in place: the copy must contain the edit.
  await page.getByTestId('card-title').locator('textarea').fill('My edited title');

  for (const section of SECTIONS) {
    const card = page.getByTestId(`card-${section}`);
    await click(card.getByTestId('copy'));
    await expect(card.getByRole('status')).toHaveText('Copied to clipboard.');
  }

  const written = await copied(page);
  expect(written).toHaveLength(SECTIONS.length);
  expect(written[1]).toBe('My edited title');
  expect(written[5]).toMatch(/^0:00 /);
  expect(clicks).toBeLessThanOrEqual(MAX_CLICKS);
});

test('copy falls back to selecting the text when the clipboard is unavailable', async ({
  page,
}) => {
  expect(video).not.toBe('');
  await stubClipboard(page, 'missing');
  await page.goto('/tools/youtube-prep');
  await uploadAndWaitForResult(page);

  const card = page.getByTestId('card-description');
  await card.getByTestId('copy').click();

  await expect(card.getByRole('status')).toContainText('Text selected');
  const area = card.locator('textarea');
  await expect(area).toBeFocused();
  const selected = await area.evaluate((el: HTMLTextAreaElement) =>
    el.value.slice(el.selectionStart, el.selectionEnd),
  );
  expect(selected.length).toBeGreaterThan(0);
  expect(selected).toBe(await area.inputValue());
});

test.describe('at 360 px wide', () => {
  test.use({ viewport: NARROW });

  test('no page scrolls horizontally and touch targets are at least 44x44', async ({ page }) => {
    expect(video).not.toBe('');
    await page.goto('/');
    await expect(page.getByTestId('tool-card').first()).toBeVisible();
    await expectNoHorizontalScroll(page);
    await expectTouchTargets(page);

    await page.goto('/tools/youtube-prep');
    await expect(page.locator('input[type=file]')).toBeEnabled();
    await expectNoHorizontalScroll(page);
    await expectTouchTargets(page);

    await uploadAndWaitForResult(page);
    await expectNoHorizontalScroll(page);
    await expectTouchTargets(page);

    await page.goto('/no-such-page');
    await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible();
    await expectNoHorizontalScroll(page);
    await expectTouchTargets(page);
  });
});
