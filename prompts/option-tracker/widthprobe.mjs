import { chromium } from 'playwright';
const OUT = '/tmp/claude-1001/-home-selden-django-assets-django-assets/da5d34ba-7dd5-44f4-95f1-43b45b540065/scratchpad/r3ref';
const browser = await chromium.launch();
for (const w of [1680, 1280, 900]) {
  const page = await (await browser.newContext({ viewport: { width: w, height: 900 } })).newPage();
  await page.goto('http://127.0.0.1:8642/tracker/', { waitUntil: 'networkidle', timeout: 120000 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/clone-positions-w${w}.png`, fullPage: false });
  const hscroll = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  console.log(`w${w} bodyHScroll=${hscroll}`);
  await page.close();
}
await browser.close();
