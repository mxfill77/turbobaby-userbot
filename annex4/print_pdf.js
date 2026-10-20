// usage: node print_pdf.js in.html out.pdf
const { chromium } = require('playwright');
(async () => {
  const [, , inp, out] = process.argv;
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto('file://' + inp, { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await page.pdf({
    path: out, format: 'A4', printBackground: true, preferCSSPageSize: true,
    displayHeaderFooter: true, headerTemplate: '<span></span>',
    footerTemplate: '<div style="width:100%;text-align:center;font-size:7.5pt;color:#555;font-family:sans-serif;">ANNEX 4 — REGISTRATION BOOKS AND PHOTOGRAPHS &nbsp;|&nbsp; Page <span class="pageNumber"></span> / <span class="totalPages"></span></div>',
    margin: { top: '14mm', bottom: '16mm', left: '14mm', right: '14mm' },
  });
  await browser.close();
})();
