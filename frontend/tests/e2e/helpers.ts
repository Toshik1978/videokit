import { expect, type Page } from '@playwright/test';

/** The narrowest supported phone width (FR-038). */
export const NARROW = { width: 360, height: 740 };

export async function expectNoHorizontalScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

/** Every visible button, link and form control is at least 44x44 px. */
export async function expectTouchTargets(page: Page): Promise<void> {
  const small = await page
    .locator('button, a, input, textarea, summary, select')
    .evaluateAll((elements) =>
      elements.flatMap((el) => {
        const box = el.getBoundingClientRect();
        if (box.width === 0 || box.height === 0 || getComputedStyle(el).visibility === 'hidden') {
          return [];
        }
        if (box.width >= 44 && box.height >= 44) return [];
        return [`${el.tagName.toLowerCase()} ${Math.round(box.width)}x${Math.round(box.height)}`];
      }),
    );
  expect(small).toEqual([]);
}
