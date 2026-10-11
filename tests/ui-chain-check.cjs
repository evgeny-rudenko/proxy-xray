const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  for (const [name, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
    await page.setViewportSize({ width, height });
    await page.goto('http://127.0.0.1:18080/', { waitUntil: 'networkidle' });
    await page.locator('[data-fragment="v5-current"]').waitFor();
    const layout = await page.evaluate(() => ({
      overflow: document.documentElement.scrollWidth > window.innerWidth,
      panels: document.querySelectorAll('[data-fragment]').length,
      clipped: [...document.querySelectorAll('.v5-button,.v5-current-name,.v5-kpi')]
        .filter(node => node.scrollWidth > node.clientWidth + 1).length,
    }));
    await page.screenshot({ path: `/tmp/proxy-chain-${name}.png`, fullPage: true });
    console.log(JSON.stringify({ name, ...layout }));
    if (layout.overflow || layout.clipped || layout.panels < 8) throw new Error('Layout check failed');
  }
  let loads = 0;
  page.on('load', () => loads++);
  const response = await page.waitForResponse(async response =>
    response.url().endsWith('/fragments/dashboard-v5') && response.status() === 200,
    { timeout: 20000 });
  if (!response.ok() || loads) throw new Error('Incremental refresh reloaded the page');
  if (errors.length) throw new Error(JSON.stringify(errors));
  console.log('Incremental refresh: OK; no browser errors');
  await browser.close();
})().catch(error => { console.error(error.message); process.exit(1); });
