// Round-3 fix verification (extends exercise3.mjs): drive every round-3
// finding, screenshot into shots4/.
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'fs';

const OUT = process.argv[2];
const BASE = 'http://127.0.0.1:8642';
mkdirSync(OUT, { recursive: true });
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok, detail: String(detail).slice(0, 220) });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : '  — ' + detail}`);
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const go = async (p) => { await page.goto(`${BASE}${p}`, { waitUntil: 'networkidle', timeout: 120000 }); await page.waitForTimeout(400); };
const body = () => page.locator('body').innerText();

// ---- P1. Date Range dropdown (history) ----
await go('/tracker/history/');
check('P1: no native range <select>', await page.locator('select[name=range]').count() === 0);
const rangeBtn = page.locator('#history-range-btn');
check('P1: Date Range button default label', /Date Range/.test(await rangeBtn.innerText()));
await rangeBtn.click();
await page.waitForTimeout(300);
const menuText = (await page.locator('#history-range-menu').innerText()).trim();
const expected = ['Select date range', 'This Week', 'Last Week', 'This Month', 'Last Month', 'This Quarter', 'YTD', 'Custom Date Range'];
check('P1: menu vocabulary exact', expected.every(x => menuText.includes(x)), menuText.replace(/\n/g, '|'));
check('P1: menu order', (() => {
  let last = -1;
  for (const item of expected) { const i = menuText.indexOf(item); if (i < last) return false; last = i; }
  return true;
})(), menuText.replace(/\n/g, '|'));
check('P1: custom inputs hidden initially', await page.locator('#history-range-menu .range-custom:visible').count() === 0);
await page.screenshot({ path: `${OUT}/history-range-menu.png`, fullPage: false });
// pick This Month -> button label updates, menu closes
await page.locator('#history-range-menu .range-item', { hasText: 'This Month' }).click();
await page.waitForTimeout(4500);
check('P1: This Month applied (label + url)', /This Month/.test(await page.locator('#history-range-btn').innerText()) && page.url().includes('range=this_month'), page.url());
check('P1: menu closed after pick', await page.locator('#history-range-menu:visible').count() === 0);
await page.screenshot({ path: `${OUT}/history-range-thismonth.png`, fullPage: false });
// custom range: reveal date inputs + Apply
await page.locator('#history-range-btn').click();
await page.waitForTimeout(300);
await page.locator('#history-range-menu .range-item-custom').click();
await page.waitForTimeout(300);
check('P1: custom reveals two date inputs + Apply',
  await page.locator('#history-range-menu input[type=date]:visible').count() === 2 &&
  await page.locator('#history-range-menu .range-apply:visible').count() === 1);
await page.screenshot({ path: `${OUT}/history-range-custom-open.png`, fullPage: false });
await page.locator('#history-range-menu input[name=start]').fill('2026-05-01');
await page.locator('#history-range-menu input[name=end]').fill('2026-05-31');
await page.locator('#history-range-menu .range-apply').click();
await page.waitForTimeout(1800);
let text = await body();
writeFileSync(`${OUT}/history-custom.txt`, text);
check('P1: custom apply (url carries range+dates)', page.url().includes('range=custom') && page.url().includes('start=2026-05-01') && page.url().includes('end=2026-05-31'), page.url());
check('P1: custom apply label', /Custom Date Range/.test(await page.locator('#history-range-btn').innerText()));
const mayDates = (text.match(/\d{2}\/\d{2}\/\d{4}-(\d{2})\/\d{2}\/\d{4}/g) || []).map(m => m.split('-')[1].slice(0, 2));
check('P1: custom window filters rows to May closes', mayDates.length > 0 && mayDates.every(m => m === '05'), mayDates.join(','));
await page.screenshot({ path: `${OUT}/history-range-custom-applied.png`, fullPage: false });
// clear via "Select date range"
await page.locator('#history-range-btn').click();
await page.waitForTimeout(300);
await page.locator('#history-range-menu .range-clear').click();
await page.waitForTimeout(4500);
check('P1: Select date range clears', /Date Range/.test(await page.locator('#history-range-btn').innerText()) && !/This Month|Custom/.test(await page.locator('#history-range-btn').innerText()));
// menus also exist on analytics + pnl flow
await go('/tracker/analytics/');
check('P1: analytics has Date Range menu', await page.locator('#analytics-range-btn').count() === 1 && await page.locator('#analytics-range-menu').count() === 1);
await go('/tracker/analytics/flow/');
check('P1: pnl flow has Date Range menu', await page.locator('#flow-range-btn').count() === 1 && await page.locator('#flow-range-menu').count() === 1);

// ---- P2. History grand totals under filters ----
await go('/tracker/history/');
text = await body();
const grand = (text.match(/Total Strategies:\s*(\d+)[\s\S]*?Total Realized PnL:\s*([+\-$\d,.]+)/) || []).slice(1);
check('P2: totals present unfiltered', grand.length === 2, JSON.stringify(grand));
const search = page.locator('input[name=q]').first();
await search.click();
await search.pressSequentially('HIMS', { delay: 40 });
await page.waitForTimeout(1800);
text = await body();
const filtered = (text.match(/Total Strategies:\s*(\d+)[\s\S]*?Total Realized PnL:\s*([+\-$\d,.]+)/) || []).slice(1);
check('P2: totals unchanged under search filter', JSON.stringify(grand) === JSON.stringify(filtered), `${JSON.stringify(grand)} vs ${JSON.stringify(filtered)}`);
await page.screenshot({ path: `${OUT}/history-grand-totals-filtered.png`, fullPage: false });
await go('/tracker/history/?range=this_week');
text = await body();
const dated = (text.match(/Total Strategies:\s*(\d+)[\s\S]*?Total Realized PnL:\s*([+\-$\d,.]+)/) || []).slice(1);
check('P2: totals unchanged under date filter', JSON.stringify(grand) === JSON.stringify(dated), `${JSON.stringify(grand)} vs ${JSON.stringify(dated)}`);

// ---- P3. Empty-state strings ----
await go('/tracker/?q=QQQQQ');
check('P3: positions empty string (search)', (await body()).includes('No live option positions match symbol "QQQQQ".'));
await go('/tracker/?strategy=long_call&strategy=long_put');
text = await body();
check('P3: positions empty string (filters)', !/QQQQQ/.test(text) && (text.includes('No live option positions match the selected filters.') || (await page.locator('#positions-table tbody tr.pos-row').count()) > 0));
await go('/tracker/wheel/?q=ZZZZ');
check('P3: wheel empty string', (await body()).includes('No equity positions match "ZZZZ".'));
await go('/tracker/equities/?q=ZZZZ');
check('P3: equities empty string', (await body()).includes('No equity positions match "ZZZZ".'));
await page.screenshot({ path: `${OUT}/equities-empty.png`, fullPage: false });
await go('/tracker/history/?q=ZZZZ');
check('P3: history keeps its string', (await body()).includes('No closed strategies match the selected filters.'));

// ---- P4. Mixed-case sortable history headers ----
await go('/tracker/history/');
const tradeDateTh = page.locator('th.th-mixed', { hasText: 'Trade Date' });
const pnlTh = page.locator('th.th-mixed', { hasText: 'Realized PnL' });
check('P4: Trade Date header mixed case', await tradeDateTh.count() === 1 && (await tradeDateTh.evaluate(el => getComputedStyle(el).textTransform)) === 'none');
check('P4: Realized PnL header mixed case', await pnlTh.count() === 1 && (await pnlTh.evaluate(el => getComputedStyle(el).textTransform)) === 'none');
check('P4: other headers stay uppercase', (await page.locator('th', { hasText: 'Initial Premium' }).first().evaluate(el => getComputedStyle(el).textTransform)) === 'uppercase');
check('P4: sort arrows kept', /[⇅↓↑]/.test(await tradeDateTh.innerText()) && /[⇅↓↑]/.test(await pnlTh.innerText()));
await page.screenshot({ path: `${OUT}/history-headers.png`, fullPage: false });

// ---- P5. Chart axis labels ----
const tickRe = /^(-?\$\d{1,3}(,\d{3})*\.\d{2}K|\$0\.00)$/;
const monthRe = /^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$/;
const axisLabels = async () => {
  const card = page.locator('.chart-row .chart-card').first();
  const labels = await card.locator('svg text.tick-label').allTextContents();
  return labels.map(s => (s || '').trim()).filter(s => s && !s.startsWith('Goal'));
};
const checkAxes = async (name) => {
  const labels = await axisLabels();
  const y = labels.filter(s => s.startsWith('$') || s.startsWith('-$'));
  const x = labels.filter(s => !s.startsWith('$') && !s.startsWith('-$'));
  check(`P5: ${name} y ticks $x.xxK/$0.00`, y.length >= 3 && y.every(s => tickRe.test(s)), y.join(','));
  check(`P5: ${name} x labels are month names`, x.length >= 2 && x.every(s => monthRe.test(s)), x.join(','));
};
await go('/tracker/analytics/');                    // weekly cumulative (default)
await checkAxes('weekly cumulative');
await page.screenshot({ path: `${OUT}/analytics-weekly-cumulative.png`, fullPage: false });
await go('/tracker/analytics/?chart=bars&mode=weekly');
await checkAxes('weekly bars');
await page.screenshot({ path: `${OUT}/analytics-weekly-bars.png`, fullPage: false });
await go('/tracker/analytics/?chart=bars&mode=monthly');
await checkAxes('monthly bars');
await go('/tracker/analytics/?chart=cumulative&mode=monthly');
await checkAxes('monthly cumulative');
await page.screenshot({ path: `${OUT}/analytics-monthly-cumulative.png`, fullPage: false });

// ---- P6. Instant CSS tooltips ----
await go('/tracker/');
check('P6: no title= tooltips on info dots', await page.locator('.info-dot[title]').count() === 0);
const summaryDot = page.locator('.summary-head .info-dot').first();
await summaryDot.hover();
await page.waitForTimeout(150);
const tip = page.locator('.summary-head .tooltip');
check('P6: summary tooltip visible on hover', await tip.isVisible());
check('P6: summary tooltip exact text', (await tip.innerText()).replace(/\s+/g, ' ').trim() === 'For reference only. Refer to your broker for accurate values. Learn more.', await tip.innerText());
check('P6: "Learn more." is a link', await tip.locator('a', { hasText: 'Learn more.' }).count() === 1);
await page.screenshot({ path: `${OUT}/summary-tooltip.png`, fullPage: false });
const mvDot = page.locator('th .info-dot').first();
await mvDot.hover();
await page.waitForTimeout(150);
check('P6: header dot tooltip visible', await mvDot.locator('.tooltip').isVisible());
await page.screenshot({ path: `${OUT}/header-tooltip.png`, fullPage: false });

// ---- P7. Outline vs solid dots ----
const dotStyle = await summaryDot.evaluate(el => { const s = getComputedStyle(el); return { bg: s.backgroundColor, border: s.borderTopWidth + ' ' + s.borderTopStyle }; });
check('P7: heading dot is outline (transparent bg + border)', /rgba\(0, 0, 0, 0\)|transparent/.test(dotStyle.bg) && /1px solid/.test(dotStyle.border), JSON.stringify(dotStyle));
const solidCount = await page.locator('td .info-dot-solid').count();
check('P7: rolled-PnL rows use solid dot', solidCount >= 1, `solid=${solidCount}`);
if (solidCount) {
  const solidStyle = await page.locator('td .info-dot-solid').first().evaluate(el => getComputedStyle(el).backgroundColor);
  check('P7: solid dot is filled blue', !/rgba\(0, 0, 0, 0\)/.test(solidStyle), solidStyle);
}
check('P7: no other solid dots outside rolled rows', await page.locator('th .info-dot-solid, h1 .info-dot-solid, .summary-card .info-dot-solid').count() === 0);
await page.screenshot({ path: `${OUT}/dots.png`, fullPage: false });

// ---- P8. Responsive ----
await page.setViewportSize({ width: 1280, height: 900 });
await go('/tracker/');
const wrap = page.locator('#positions-table');
const fit = await wrap.evaluate(el => ({ sw: el.scrollWidth, cw: el.clientWidth }));
check('P8: 1280px — positions table fits (no inner scroll)', fit.sw <= fit.cw, JSON.stringify(fit));
check('P8: 1280px — SHARE column visible', await page.locator('th', { hasText: 'Share' }).last().isVisible());
await page.screenshot({ path: `${OUT}/positions-1280.png`, fullPage: false });
await page.setViewportSize({ width: 900, height: 900 });
await go('/tracker/');
const pageFit = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
check('P8: 900px — no page-level horizontal scroll', pageFit.sw <= pageFit.cw, JSON.stringify(pageFit));
const summaryCols = await page.locator('.summary-body').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length);
check('P8: 900px — summary grid wraps to 2 columns', summaryCols === 2, `cols=${summaryCols}`);
check('P8: 900px — sidebar is a top strip (row layout)', (await page.locator('.sidebar').evaluate(el => getComputedStyle(el).flexDirection)) === 'row');
await page.screenshot({ path: `${OUT}/positions-900.png`, fullPage: true });
await page.setViewportSize({ width: 1440, height: 900 });

// ---- P9. Heading order + donut legend order ----
await go('/tracker/');
const h1Html = await page.locator('.page-head h1').innerHTML();
check('P9: heading order count -> badge -> info dot',
  h1Html.indexOf('pos-count') < h1Html.indexOf('badge-demo') && h1Html.indexOf('badge-demo') < h1Html.indexOf('info-dot'),
  h1Html.slice(0, 160));
check('P9: heading shows (N) Demo data', /Option Positions\s*\(\d+\)\s*Demo data/.test((await page.locator('.page-head h1').innerText()).replace(/\s+/g, ' ')));
await page.screenshot({ path: `${OUT}/positions-heading.png`, fullPage: false });
await go('/tracker/analytics/');
const legend = (await page.locator('.donut-legend .donut-legend-item').allInnerTexts()).map(s => s.trim());
const refOrder = ['Call Credit Spread', 'Call Debit Spread', 'Iron Condor', 'Long Call', 'Long Put', 'Put Credit Spread', 'Put Debit Spread', 'Covered Call', 'Cash Secured Put'];
const present = refOrder.filter(x => legend.includes(x));
check('P9: donut legend follows reference order', JSON.stringify(legend.filter(x => refOrder.includes(x))) === JSON.stringify(present), legend.join(' | '));
check('P9: extras (if any) come after the fixed vocabulary', legend.every((x, i) => refOrder.includes(x) || legend.slice(i + 1).every(y => !refOrder.includes(y))), legend.join(' | '));
await page.screenshot({ path: `${OUT}/donut-legend.png`, fullPage: false });

await browser.close();
const failed = results.filter(r => !r.ok);
writeFileSync(`${OUT}/exercise4-results.json`, JSON.stringify({ failed: failed.length, total: results.length, results }, null, 2));
console.log(`\n[exercise4] ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
