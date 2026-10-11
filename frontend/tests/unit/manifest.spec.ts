import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

const root = resolve(__dirname, '../..');
const read = (path: string): string => readFileSync(resolve(root, path), 'utf8');

interface Icon {
  src: string;
  sizes: string;
  type: string;
  purpose?: string;
}

const manifest = JSON.parse(read('public/manifest.webmanifest')) as {
  name: string;
  short_name: string;
  display: string;
  start_url: string;
  theme_color: string;
  background_color: string;
  icons: Icon[];
};

function pngSize(path: string): { width: number; height: number } {
  const buf = readFileSync(path);
  expect(buf.subarray(1, 4).toString('latin1')).toBe('PNG');
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}

describe('web app manifest', () => {
  it('has the required installability fields', () => {
    expect(manifest.name).toBe('videokit');
    expect(manifest.short_name).toBeTruthy();
    expect(manifest.display).toBe('standalone');
    expect(manifest.start_url).toBe('/');
    expect(manifest.theme_color).toMatch(/^#[0-9a-f]{6}$/i);
    expect(manifest.background_color).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it('declares 192 and 512 px icons that exist with those dimensions', () => {
    for (const size of [192, 512]) {
      const icon = manifest.icons.find((i) => i.sizes === `${size}x${size}`);
      expect(icon, `icon ${size}`).toBeDefined();
      expect(icon?.type).toBe('image/png');
      const file = resolve(root, 'public', (icon as Icon).src.replace(/^\//, ''));
      expect(existsSync(file)).toBe(true);
      expect(pngSize(file)).toEqual({ width: size, height: size });
    }
  });

  it('ships the SVG source of the icon', () => {
    expect(existsSync(resolve(root, 'public/icons/icon.svg'))).toBe(true);
  });
});

describe('index.html', () => {
  const html = read('index.html');

  it('links the manifest and the touch icon', () => {
    expect(html).toMatch(/<link rel="manifest" href="\/manifest\.webmanifest"/);
    expect(html).toMatch(/<link rel="apple-touch-icon" href="\/icons\/[^"]+\.png"/);
  });

  it('opts in to standalone mode and sets the theme color', () => {
    expect(html).toContain('name="apple-mobile-web-app-capable" content="yes"');
    expect(html).toContain('name="mobile-web-app-capable" content="yes"');
    expect(html).toContain(`name="theme-color" content="${manifest.theme_color}"`);
  });
});
