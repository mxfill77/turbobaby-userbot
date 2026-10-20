// usage: node print_pdf.js <in.html> <out.pdf>   (relative paths are fine)
// Fails (exit 1, no PDF) if any image did not load or a Noto face is missing: a PDF with broken-image
// icons under a ✓ in the index is worse than no PDF.
const path = require('path');
const { pathToFileURL } = require('url');
const { chromium } = require('playwright');

(async () => {
  const [, , inp, out] = process.argv;
  if (!inp || !out) {
    console.error('usage: node print_pdf.js <in.html> <out.pdf>');
    process.exit(2);
  }
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.goto(pathToFileURL(path.resolve(inp)).href, { waitUntil: 'networkidle' });
    await page.evaluate(() => document.fonts.ready);
    const broken = await page.evaluate(() =>
      [...document.images].filter((i) => !i.complete || i.naturalWidth === 0).map((i) => i.src));
    const faces = await page.evaluate(() => {
      const loaded = [...document.fonts].filter((f) => f.status === 'loaded').map((f) => f.family.replace(/['"]/g, ''));
      return ['Noto Sans', 'Noto Sans Thai', 'Noto Serif Thai'].filter((f) => !loaded.includes(f));
    });
    if (broken.length || faces.length) {
      throw new Error(`not printed: ${broken.length} image(s) failed to load ${JSON.stringify(broken.slice(0, 3))}; `
        + `fonts missing: ${JSON.stringify(faces)}`);
    }
    await page.pdf({
      path: path.resolve(out), format: 'A4', printBackground: true, preferCSSPageSize: true,
      displayHeaderFooter: true, headerTemplate: '<span></span>',
      footerTemplate: '<div style="width:100%;text-align:center;font-size:7.5pt;color:#555;font-family:sans-serif;">'
        + 'ANNEX 4 — REGISTRATION BOOKS AND PHOTOGRAPHS &nbsp;|&nbsp; Page <span class="pageNumber"></span> / '
        + '<span class="totalPages"></span></div>',
      margin: { top: '14mm', bottom: '16mm', left: '14mm', right: '14mm' },
    });
  } catch (e) {
    console.error(e.message || e);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
