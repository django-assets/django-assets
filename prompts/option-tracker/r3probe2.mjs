import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'fs';
const OUT = process.argv[2];
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const log = (...a) => console.log('[r3b]', ...a);
const save = async (name) => {
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true }).catch(()=>{});
  writeFileSync(`${OUT}/${name}.txt`, await page.locator('body').innerText().catch(()=>''), 'utf8');
};
await page.goto('https://www.optionincome.io/', { waitUntil: 'networkidle', timeout: 45000 });
await page.locator('a:has-text("Sign in"), button:has-text("Sign in")').first().click();
await page.waitForTimeout(2500);
await page.getByPlaceholder(/email/i).first().fill(process.env.OI_EMAIL);
await page.locator('button:visible').filter({ hasText: /continue/i }).filter({ hasNotText: /google/i }).first().click();
await page.waitForTimeout(3500);
await page.locator('input[type="password"]:visible').first().fill(process.env.OI_PASSWORD);
await page.locator('.cl-formButtonPrimary:visible, .cl-card button[type="submit"]:visible').first().click();
await page.waitForLoadState('networkidle', { timeout: 45000 }).catch(() => {});
await page.waitForTimeout(3000);
await page.goto('https://www.optionincome.io/dashboard#live', { waitUntil: 'networkidle', timeout: 45000 });
await page.waitForTimeout(4000);

// History date range
await page.locator('text="History"').first().click();
await page.waitForTimeout(2500);
const dr = page.locator('button:has-text("Date Range")').first();
log('daterange button count:', await page.locator('button:has-text("Date Range")').count());
await dr.click();
await page.waitForTimeout(1500);
await save('hist-daterange-open');
// dump any popover content
const pop = await page.locator('[role=menu], [role=listbox], [class*=popover], [class*=menu], [class*=dropdown]').allInnerTexts();
writeFileSync(`${OUT}/hist-daterange-popover.txt`, JSON.stringify(pop, null, 1));
// try clicking a preset
const preset = page.getByText(/last 30 days/i).first();
if (await preset.count()) {
  await preset.click(); await page.waitForTimeout(2500); await save('hist-daterange-30d');
} else {
  log('no "last 30 days" preset visible');
}
// positions header toggle: click the PNL header text
await page.locator('text="Option Positions"').first().click();
await page.waitForTimeout(2500);
const pnlHead = page.locator('thead th', { hasText: /PNL/i }).first();
log('pnl th count:', await page.locator('thead th', { hasText: /PNL/i }).count());
const link = pnlHead.locator('a, button, span').first();
await pnlHead.click({ position: { x: 10, y: 10 } }).catch(async e => { log('th click err', e.message.slice(0,80)); });
await page.waitForTimeout(1800);
await save('pos-after-pnl-click');
await browser.close();
log('done');
