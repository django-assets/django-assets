import { chromium } from 'playwright';
import { writeFileSync } from 'fs';
const OUT = process.argv[2];
const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
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
// hover the Account Summary info icon
const icon = page.locator('svg[class*=info], [class*=info]').first();
// more robust: find the circled-i next to Account Summary heading
const head = page.locator('text=Account Summary').first();
const box = await head.boundingBox();
await page.mouse.move(box.x + box.width + 18, box.y + box.height / 2);
await page.waitForTimeout(1500);
await page.screenshot({ path: `${OUT}/ref-tooltip-summary.png`, fullPage: false });
const text = await page.locator('body').innerText();
writeFileSync(`${OUT}/ref-tooltip-summary.txt`, text);
// also hover Cash dot
const cash = page.locator('text=/^Cash$/').first();
const cb = await cash.boundingBox().catch(()=>null);
if (cb) { await page.mouse.move(cb.x + cb.width + 14, cb.y + cb.height/2); await page.waitForTimeout(1500); await page.screenshot({ path: `${OUT}/ref-tooltip-cash.png` }); writeFileSync(`${OUT}/ref-tooltip-cash.txt`, await page.locator('body').innerText()); }
await browser.close();
console.log('done');
