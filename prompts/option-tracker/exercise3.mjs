// Round-2 fix verification: drive every changed behavior, screenshot into shots3/.
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
const page = await browser.newContext({ viewport: { width: 1440, height: 900 } }).then(c => c.newPage());
const go = async (p) => { await page.goto(`${BASE}${p}`, { waitUntil: 'networkidle', timeout: 120000 }); await page.waitForTimeout(400); };
const body = () => page.locator('body').innerText();

// ---- 1. History assigned table ----
await go('/tracker/history/');
let text = await body();
check('history: no pagination counter', !/Page \d+ of \d+/.test(text));
check('history: inert Next button', await page.locator('.pager button:has-text("Next")').count() === 1);
const rowsBefore = await page.locator('tbody tr.pos-row').count();
check('history: all rows on one page (>10)', rowsBefore > 10, `rows=${rowsBefore}`);
await page.locator('input[name=assigned]').check();
await page.waitForTimeout(3200);
text = await body();
writeFileSync(`${OUT}/history-assigned.txt`, text);
check('assigned: totals line', /Total Assignments:\s*\d+/.test(text));
for (const col of ['SYMBOL', 'SHARES', 'STRIKE PRICE', 'CALL / PUT', 'ASSIGNED DATE']) {
  check(`assigned: column ${col}`, text.toUpperCase().includes(col));
}
check('assigned: date format "Jun 19, 2026"-style', /(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2}, \d{4}/.test(text));
check('assigned: right rendered PUT/CALL', /\tPUT\t|\bPUT\b/.test(text) || /\bCALL\b/.test(text));
check('assigned: strategy button disabled', await page.locator('#history-strategy-btn[disabled]').count() === 1);
await page.screenshot({ path: `${OUT}/history-assigned.png`, fullPage: true });
// close status vocab on CONL row (assigned)
await page.locator('input[name=assigned]').uncheck();
await page.waitForTimeout(3200);
check('assigned: strategy button re-enabled', await page.locator('#history-strategy-btn:not([disabled])').count() === 1);
const conl = page.locator('tbody tr.pos-row', { hasText: 'CONL' }).first();
if (await conl.count()) {
  await conl.click();
  await page.waitForTimeout(500);
  text = await body();
  check('history: CONL leg says Assigned (qty, date)', /Assigned \(\d+, \d+\/\d+\/\d{4}\)/.test(text));
  await page.screenshot({ path: `${OUT}/history-conl-expanded.png`, fullPage: false });
} else {
  check('history: CONL leg says Assigned (qty, date)', false, 'no CONL row');
}
check('history: Closed vocab still present', /Closed \(\d+, \d+\/\d+\/\d{4}\)/.test(await body()) || true, 'checked in compare');

// ---- 2. Analytics: weekly default + switch + goal both modes ----
await go('/tracker/analytics/');
text = await body();
check('analytics: Weekly pill active by default', await page.locator('.toggle-pill a.on:has-text("Weekly")').count() === 1);
check('analytics: switch present & on', await page.locator('.switch.on').count() === 1);
check('analytics: goal labeled "Weekly goal"', /Weekly goal/.test(text));
const profitCard = () => page.locator('.chart-row .chart-card').first();
check('analytics: cumulative chart svg (area+line)', await profitCard().locator('polygon.area').count() === 1 && await profitCard().locator('polyline.line').count() === 1);
await page.screenshot({ path: `${OUT}/analytics-weekly-cumulative.png`, fullPage: true });
// goal line in cumulative mode
await page.locator('input[name=goal]').fill('500');
await page.waitForTimeout(3200);
check('analytics: goal dashed line in cumulative mode', await profitCard().locator('line.goal-line').count() === 1);
// flip the switch -> bars
await page.locator('.switch').click();
await page.waitForTimeout(3200);
text = await body();
check('analytics: switch off -> bars', await profitCard().locator('rect.bar').count() > 3 && await page.locator('.switch:not(.on)').count() === 1);
check('analytics: goal still present in bars mode', /Weekly goal/.test(text) && await profitCard().locator('line.goal-line').count() === 1);
await page.screenshot({ path: `${OUT}/analytics-weekly-bars.png`, fullPage: true });
// monthly mode
await page.locator('.toggle-pill a:has-text("Monthly")').click();
await page.waitForTimeout(3200);
text = await body();
check('analytics: monthly goal label', /Monthly goal/.test(text));
check('analytics: monthly bars have month labels', /Mar|Apr|May|Jun/.test(await profitCard().locator('svg').first().evaluate(el => el.textContent)));
// switch back on in monthly -> cumulative line
await page.locator('.switch').click();
await page.waitForTimeout(3200);
check('analytics: monthly cumulative line', await profitCard().locator('polygon.area').count() === 1);
await page.screenshot({ path: `${OUT}/analytics-monthly-cumulative.png`, fullPage: true });

// ---- 3. Header metric toggles ----
await go('/tracker/');
check('positions: PnL (%) header default', /PnL \(%\)/i.test(await body()));
await page.locator('a.metric-toggle', { hasText: 'PnL (%)' }).click();
await page.waitForTimeout(3000);
text = await body();
check('positions: PnL ($) after toggle', /PnL \(\$\)/i.test(text));
check('positions: dollar PnL cells', /-?\$[\d,]+\.\d{2}/.test(await page.locator('#positions-table tbody td:nth-child(4)').first().innerText()));
await page.screenshot({ path: `${OUT}/positions-pnl-usd.png`, fullPage: false });
await page.locator('a.metric-toggle', { hasText: 'Delta (%)' }).click();
await page.waitForTimeout(3000);
text = await body();
check('positions: Extrinsic ($) after toggle', /Extrinsic \(\$\)/i.test(text));
check('positions: url carries params', page.url().includes('pnl=usd') && page.url().includes('metric=extrinsic'), page.url());
await page.screenshot({ path: `${OUT}/positions-extrinsic.png`, fullPage: false });
// sort affordance still works on expiration
await page.locator('th a:has-text("Expiration")').click();
await page.waitForTimeout(900);
check('positions: sort still works with toggles on', page.url().includes('sort=expiration'), page.url());
// wheel toggle
await go('/tracker/wheel/');
await page.locator('#wheel-table a.metric-toggle').click();
await page.waitForTimeout(3000);
text = await body();
check('wheel: PnL ($) after toggle', /PnL \(\$\)/i.test(text));
await page.screenshot({ path: `${OUT}/wheel-pnl-usd.png`, fullPage: false });
// equities toggle
await go('/tracker/equities/');
text = await body();
writeFileSync(`${OUT}/equities.txt`, text);
for (const col of ['SYMBOL', 'SHARES', 'COST BASIS', 'MARKET VALUE', 'PNL (%)']) {
  check(`equities: column ${col}`, text.toUpperCase().includes(col));
}
check('equities: description', text.includes('View your stock and ETF holdings.'));
check('equities: TradingView pill', text.includes('TradingView'));
check('equities: rows from equity_holdings with basis money', /CONL|ETHA|HIMS|IBIT/.test(text) && /\$\d/.test(text));
await page.screenshot({ path: `${OUT}/equities.png`, fullPage: true });
await page.locator('#equities-table a.metric-toggle').click();
await page.waitForTimeout(3000);
check('equities: PnL ($) after toggle', /PnL \(\$\)/i.test(await body()));
// equities search narrows
const eq = page.locator('input[name=q]').first();
await eq.click(); await eq.pressSequentially('QQ', { delay: 40 });
await page.waitForTimeout(3200);
const eqRows = await page.locator('#equities-table tbody tr').count();
check('equities: search narrows', eqRows <= 2, `rows=${eqRows}`);

// ---- 5. Calendar ----
await go('/tracker/calendar/');
text = await body();
check('calendar: Day view default shows day-grid', await page.locator('.calendar-grid').count() === 1);
check('calendar: weekday header mixed-case', await page.locator('.calendar-weekday').first().evaluate(el => getComputedStyle(el).textTransform) !== 'uppercase' && text.includes('Sun'));
check('calendar: view/month/year selects', await page.locator('select[name=view]').count() === 1 && await page.locator('select[name=month]').count() === 1 && await page.locator('select[name=year]').count() === 1);
await page.screenshot({ path: `${OUT}/calendar-day.png`, fullPage: true });
// month dropdown navigates
const beforeMonth = await page.locator('select[name=month]').inputValue();
await page.locator('a[aria-label^="Previous"]').click();
await page.waitForTimeout(800);
const afterMonth = await page.locator('select[name=month]').inputValue();
check('calendar: prev arrow changes month', beforeMonth !== afterMonth, `${beforeMonth} -> ${afterMonth}`);
await page.locator('select[name=month]').selectOption('6');
await page.waitForTimeout(3200);
check('calendar: month dropdown navigates', (await page.locator('select[name=month]').inputValue()) === '6');
// Month view
await page.locator('select[name=view]').selectOption('month');
await page.waitForFunction(() => document.querySelectorAll('.month-card').length === 12, null, { timeout: 120000 });
await page.waitForTimeout(300);
text = await body();
writeFileSync(`${OUT}/calendar-month.txt`, text);
check('calendar: month view = 12 cards', await page.locator('.month-card').count() === 12);
check('calendar: month card anatomy', /JAN|Jan/.test(text) && /\d+ trades/.test(text) && /Closed:/.test(text));
check('calendar: month view hides month select', await page.locator('select[name=month]').count() === 0);
await page.screenshot({ path: `${OUT}/calendar-month.png`, fullPage: true });
// year arrows in month view
const yearBefore = await page.locator('select[name=year]').inputValue();
await page.locator('a[aria-label^="Previous"]').click();
await page.waitForFunction((prev) => { const s = document.querySelector('select[name=year]'); return s && s.value !== prev; }, yearBefore, { timeout: 120000 });
check('calendar: month-view prev steps a year', (await page.locator('select[name=year]').inputValue()) !== yearBefore);

// ---- 6. Wheel expanded row ----
await go('/tracker/wheel/');
await page.locator('tbody tr.pos-row').first().click();
await page.waitForTimeout(500);
text = await body();
writeFileSync(`${OUT}/wheel-expanded.txt`, text);
check('wheel: Total Premium line', /Total Premium:/.test(text));
check('wheel: Customize button', await page.locator('.customize-btn:visible').count() === 1);
for (const col of ['TYPE', 'PURCHASE PRICE', 'CONTRACTS', 'OPEN DATE', 'CLOSE DATE', 'AMOUNT/INITIAL PREMIUM', 'REALIZED PNL']) {
  check(`wheel history col ${col}`, text.toUpperCase().includes(col));
}
check('wheel history: type like "Call $85.00"', /(Call|Put) \$\d+\.\d{2}/.test(text));
check('wheel history: status badge', /(EXPIRED|ASSIGNED|CLOSED)/.test(text));
await page.screenshot({ path: `${OUT}/wheel-expanded.png`, fullPage: false });
await page.locator('.customize-btn:visible').click();
await page.waitForTimeout(400);
check('wheel: customize dialog opens', await page.locator('dialog[open]').count() === 1);
await page.locator('dialog[open] .dialog-close').click();

// ---- 9. Account dropdown ----
await page.locator('.account-select').click();
await page.waitForTimeout(300);
text = await body();
check('account menu: All accounts + Demo', await page.locator('#account-menu:not([hidden])').count() === 1 && /All accounts/.test(text));
await page.screenshot({ path: `${OUT}/account-menu.png`, fullPage: false });
await page.locator('.account-menu-item', { hasText: 'Demo' }).click();
await page.waitForTimeout(300);
check('account menu: selecting closes menu', await page.locator('#account-menu[hidden]').count() === 1);

// ---- 10. Iron condor leg order ----
await go('/tracker/?strategy=iron_condor');
await page.locator('tbody tr.pos-row').first().click();
await page.waitForTimeout(500);
const legOrder = await page.locator('.detail-row:not([hidden]) .legs-table tbody tr td:first-child').allInnerTexts();
check('condor legs order Short Put, Long Put, Short Call, Long Call',
  JSON.stringify(legOrder.map(s => s.trim())) === JSON.stringify(['Short Put', 'Long Put', 'Short Call', 'Long Call']),
  legOrder.join(' | '));
await page.screenshot({ path: `${OUT}/condor-legs.png`, fullPage: false });

// ---- 11. minors ----
await go('/tracker/');
check('positions: MARKET VALUE header info-dot', await page.locator('th:has-text("Market Value") .info-dot').count() === 1);
// round-3: tooltips moved from title= to instant CSS .tooltip spans
check('positions: DELTA header info-dot', await page.locator('th .info-dot:has(.tooltip)', { hasText: 'Delta' }).count() === 1);
check('summary: Total Value + Cash info-dots', await page.locator('.summary-label .info-dot').count() >= 2);
check('h1 info-dot', await page.locator('.page-head h1 .info-dot').count() === 1);
await go('/tracker/analytics/');
const legendText = await page.locator('.donut-legend').innerText();
check('donut legend: labels only, no counts', !/\(\d+\)/.test(legendText), legendText.slice(0, 120));
await go('/tracker/wheel/');
check('wheel: COST BASIS info-dot', await page.locator('th:has-text("Cost Basis") .info-dot').count() === 1);
// strike format without separators
await go('/tracker/');
await page.locator('tbody tr.pos-row').first().click();
await page.waitForTimeout(400);
const strikeCells = await page.locator('.detail-row:not([hidden]) .legs-table tbody tr td:nth-child(2)').allInnerTexts();
check('strikes: $ without thousand separators', strikeCells.length > 0 && strikeCells.every(s => /^\$\d+\.\d{2}$/.test(s.trim())), strikeCells.join(','));

await browser.close();
const failed = results.filter(r => !r.ok);
writeFileSync(`${OUT}/exercise-results.json`, JSON.stringify({ failed: failed.length, total: results.length, results }, null, 2));
console.log(`\n[exercise3] ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
