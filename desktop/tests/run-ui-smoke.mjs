import { mkdir, readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

// Reuse an installed Playwright runtime; the application needs no new dependency.
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.FORGE_PLAYWRIGHT_MODULE || 'playwright');
const fixture = process.argv[2] || 'ui-simple-shell.playwright.js';
const directory = path.dirname(fileURLToPath(import.meta.url));
const source = await readFile(path.resolve(directory, fixture), 'utf8');
const smoke = vm.runInThisContext(`(${source})`, { filename: fixture });
const outputDir = path.resolve(directory, '../output/playwright');
await mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({ headless: true, ...(process.env.FORGE_PLAYWRIGHT_CHANNEL ? { channel: process.env.FORGE_PLAYWRIGHT_CHANNEL } : {}) });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.setDefaultTimeout(15000);
  try {
    const result = await smoke(page, { outputDir });
    if (pageErrors.length) throw new Error(`uncaught browser errors: ${pageErrors.join('\n')}`);
    console.log(JSON.stringify({ ...result, uncaughtPageErrors: pageErrors.length }, null, 2));
  } catch (error) {
    await page.screenshot({ path: path.join(outputDir, `${path.parse(fixture).name}-failure.png`), fullPage: true });
    throw error;
  }
} finally {
  await browser.close();
}
