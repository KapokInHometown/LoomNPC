/** Optional real-browser runner; Playwright is a test-only dependency. */
import { pathToFileURL } from "node:url";
import { verifyWorkshop, verifyTown, verifyMinimal } from "./web_demo.mjs";

const [scene, url] = process.argv.slice(2);
const cases = { workshop: verifyWorkshop, town: verifyTown, minimal: verifyMinimal };
if (!cases[scene] || !url || !/^http:\/\/(127\.0\.0\.1|localhost):\d+\/?$/.test(url)) {
  throw new Error("Usage: node tests/run_web_demo.mjs workshop|town|minimal http://127.0.0.1:PORT");
}
const moduleName = process.env.LOOM_PLAYWRIGHT_MODULE
  ? pathToFileURL(process.env.LOOM_PLAYWRIGHT_MODULE).href : "playwright";
const { chromium } = await import(moduleName);
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(url);
  await cases[scene](page);
  if (errors.length) throw new Error(errors.join("\n"));
  console.log(`${scene}: browser regression passed`);
} finally {
  await browser.close();
}
