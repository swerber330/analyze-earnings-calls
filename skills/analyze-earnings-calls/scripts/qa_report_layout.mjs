#!/usr/bin/env node

import { spawn } from "node:child_process";
import { access, mkdtemp, rm } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const delay = (milliseconds) => new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));

function check(condition, message) {
  if (!condition) throw new Error(message);
}

async function chromiumPath() {
  const candidates = [
    process.env.CHROMIUM_PATH,
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      await access(candidate);
      return candidate;
    } catch {
      // Keep looking.
    }
  }
  throw new Error("Chromium was not found. Set CHROMIUM_PATH to a Chromium executable.");
}

async function freePort() {
  return new Promise((resolvePort, rejectPort) => {
    const server = createServer();
    server.once("error", rejectPort);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => resolvePort(address.port));
    });
  });
}

async function pageTarget(port, expectedUrl) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/list`);
      const targets = await response.json();
      const target = targets.find((item) => item.type === "page" && item.url === expectedUrl)
        ?? targets.find((item) => item.type === "page");
      if (target?.webSocketDebuggerUrl) return target;
    } catch {
      // Chromium is still starting.
    }
    await delay(100);
  }
  throw new Error("Timed out waiting for Chromium's debugging target.");
}

async function cdpClient(webSocketDebuggerUrl) {
  const socket = new WebSocket(webSocketDebuggerUrl);
  await new Promise((resolveOpen, rejectOpen) => {
    socket.addEventListener("open", resolveOpen, { once: true });
    socket.addEventListener("error", rejectOpen, { once: true });
  });
  let nextId = 1;
  const pending = new Map();
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const { resolveCall, rejectCall } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) rejectCall(new Error(message.error.message));
    else resolveCall(message.result);
  });
  const send = (method, params = {}) => new Promise((resolveCall, rejectCall) => {
    const id = nextId;
    nextId += 1;
    pending.set(id, { resolveCall, rejectCall });
    socket.send(JSON.stringify({ id, method, params }));
  });
  return { socket, send };
}

async function measure(send, width, height, mobile) {
  await send("Emulation.setDeviceMetricsOverride", {
    width,
    height,
    deviceScaleFactor: 1,
    mobile,
    screenWidth: width,
    screenHeight: height,
  });
  await send("Page.reload", { ignoreCache: true });
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const readiness = await send("Runtime.evaluate", {
      expression: "document.readyState === 'complete' && Boolean(document.querySelector('h2'))",
      returnByValue: true,
    });
    if (readiness.result.value) break;
    if (attempt === 99) throw new Error("Timed out waiting for the report DOM after reload.");
    await delay(100);
  }
  const expression = String.raw`(async () => {
    await document.fonts.ready;
    const style = (node) => node ? getComputedStyle(node) : null;
    const citations = [...document.querySelectorAll('.citation')].map((node) => node.textContent.trim());
    const tables = [...document.querySelectorAll('.responsive-table')].map((table) => {
      const row = table.tBodies[0]?.rows[0];
      const cells = row ? [...row.cells] : [];
      return {
        className: table.className,
        tableWidth: Math.round(table.getBoundingClientRect().width),
        rowDisplay: row ? style(row).display : null,
        cellDisplays: cells.map((cell) => style(cell).display),
        cellWidths: cells.map((cell) => Math.round(cell.getBoundingClientRect().width)),
      };
    });
    const sectionIds = ['executive', 'sentiment', 'hedging', 'guidance', 'kpis', 'qa', 'risks', 'sources'];
    const openingCta = document.querySelector('.cta-opening');
    const executive = document.querySelector('#executive');
    const navLinks = [...document.querySelectorAll('.report-nav a')];
    const hedgeGap = document.querySelector('.hedge-row.gap');
    const hedgeGapReason = hedgeGap?.querySelector('small');
    const citationRects = [...document.querySelectorAll('.citation')].map((node) => node.getBoundingClientRect());
    const sourceIdLink = document.querySelector('.source-table tbody th a');
    const timelineCards = [...document.querySelectorAll('.language-quarter')];
    return {
      innerWidth,
      documentScrollWidth: document.documentElement.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
      bodyFontSize: parseFloat(style(document.body).fontSize),
      h1FontSize: parseFloat(style(document.querySelector('h1')).fontSize),
      h2FontSize: parseFloat(style(document.querySelector('h2')).fontSize),
      heroHeight: Math.round(document.querySelector('.hero').getBoundingClientRect().height),
      citationCount: citations.length,
      maxCitationLength: citations.length ? Math.max(...citations.map((value) => value.length)) : 0,
      minCitationHeight: citationRects.length ? Math.min(...citationRects.map((rect) => Math.round(rect.height))) : 0,
      timelineCardCount: timelineCards.length,
      timelineCardsComplete: timelineCards.every((card) => card.textContent.includes('Sentiment') && card.textContent.includes('Hedge rate')),
      navLinkCount: navLinks.length,
      navLinksFullyVisible: navLinks.every((link) => { const rect = link.getBoundingClientRect(); return rect.left >= 0 && rect.right <= innerWidth; }),
      hedgeGapReadable: !hedgeGap || (style(hedgeGap).gridTemplateColumns.split(' ').length <= 2 && hedgeGapReason.getBoundingClientRect().width >= 250),
      sourceIdColor: sourceIdLink ? style(sourceIdLink).color : null,
      locatorSummaryCount: document.querySelectorAll('.locator-list summary').length,
      tables,
      sectionPositions: sectionIds.map((id) => Math.round(document.querySelector('#' + id).getBoundingClientRect().top + scrollY)),
      reportNav: Boolean(document.querySelector('.report-nav')),
      openingCtaInHero: Boolean(document.querySelector('.hero .cta-opening')),
      openingCtaInExecutive: Boolean(openingCta && executive?.contains(openingCta)),
    };
  })()`;
  const evaluation = await send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  check(
    !evaluation.exceptionDetails,
    `Browser measurement threw an exception: ${JSON.stringify(evaluation.exceptionDetails)}`,
  );
  return evaluation.result.value;
}


async function measurePrint(send) {
  await send("Emulation.setDeviceMetricsOverride", {
    width: 794,
    height: 1123,
    deviceScaleFactor: 1,
    mobile: false,
    screenWidth: 794,
    screenHeight: 1123,
  });
  await send("Emulation.setEmulatedMedia", { media: "print" });
  const evaluation = await send("Runtime.evaluate", {
    expression: String.raw`(() => {
      const style = (node) => getComputedStyle(node);
      const locatorLists = [...document.querySelectorAll('.locator-print')];
      const tableRows = [...document.querySelectorAll('.responsive-table tbody tr')];
      const sentimentDetail = document.querySelector('.sentiment-detail-block');
      return {
        navHidden: style(document.querySelector('.report-nav')).display === 'none',
        sectionsFlow: [...document.querySelectorAll('section')].every((section) => style(section).breakBefore === 'auto'),
        methodStripsAvoidBreak: [...document.querySelectorAll('.method-strip')].every((strip) => style(strip).breakInside === 'avoid'),
        sentimentDetailGrouped: Boolean(sentimentDetail && sentimentDetail.querySelector('.metric-grid, .empty-state') && sentimentDetail.querySelector('.method-strip') && style(sentimentDetail).breakInside === 'avoid'),
        locatorListsPrintable: locatorLists.length > 0 && locatorLists.every((list) => style(list).display !== 'none'),
        tablesStacked: tableRows.every((row) => style(row).display === 'block'),
        documentFits: document.documentElement.scrollWidth <= innerWidth + 1,
      };
    })()`,
    returnByValue: true,
  });
  check(!evaluation.exceptionDetails, `Print measurement threw an exception: ${JSON.stringify(evaluation.exceptionDetails)}`);
  const printed = await send("Page.printToPDF", { printBackground: true, preferCSSPageSize: true });
  const pdfText = Buffer.from(printed.data, "base64").toString("latin1");
  const pageCount = (pdfText.match(/\/Type\s*\/Page\b/g) ?? []).length;
  return { ...evaluation.result.value, pageCount };
}

function validate(result, viewport) {
  check(result.documentScrollWidth <= result.innerWidth + 1, `${viewport}: document overflows horizontally (${result.documentScrollWidth}px > ${result.innerWidth}px).`);
  check(result.reportNav, `${viewport}: report navigation is missing.`);
  check(result.navLinkCount === 8 && result.navLinksFullyVisible, `${viewport}: report navigation is incomplete or clipped.`);
  check(!result.openingCtaInHero && result.openingCtaInExecutive, `${viewport}: opening CTA is not placed after the executive analysis.`);
  check(result.citationCount > 0 && result.maxCitationLength <= 8, `${viewport}: citation labels are missing or too long.`);
  check(result.tables.length === 3, `${viewport}: expected three responsive dense tables.`);
  check(result.timelineCardCount === 4 && result.timelineCardsComplete, `${viewport}: unified four-call language timeline is missing or incomplete.`);
  check(result.locatorSummaryCount > 0, `${viewport}: precise locator index is missing.`);
  check(result.sectionPositions.every((value, index, values) => index === 0 || value > values[index - 1]), `${viewport}: report sections are not in insight-first order.`);
}

async function main() {
  check(process.argv.length === 3, "Usage: node scripts/qa_report_layout.mjs report.html");
  const reportPath = resolve(process.argv[2]);
  await access(reportPath);
  const reportUrl = pathToFileURL(reportPath).href;
  const chromium = await chromiumPath();
  const port = await freePort();
  const profile = await mkdtemp(`${tmpdir()}/earnings-layout-qa-`);
  const chrome = spawn(chromium, [
    "--headless=new",
    "--no-sandbox",
    "--disable-gpu",
    "--hide-scrollbars",
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    reportUrl,
  ], { stdio: "ignore" });
  let socket;
  try {
    const target = await pageTarget(port, reportUrl);
    const client = await cdpClient(target.webSocketDebuggerUrl);
    socket = client.socket;
    await client.send("Page.enable");
    await client.send("Runtime.enable");
    const desktop = await measure(client.send, 1440, 1000, false);
    const mobile = await measure(client.send, 390, 844, true);
    const print = await measurePrint(client.send);
    validate(desktop, "desktop");
    validate(mobile, "mobile");
    check(desktop.tables.every((table) => table.rowDisplay === "table-row"), "desktop: dense tables do not retain table semantics.");
    check(mobile.tables.every((table) => table.rowDisplay === "block"), "mobile: dense rows did not become readable cards.");
    check(mobile.tables.every((table) => table.cellDisplays.every((display) => display === "block")), "mobile: at least one dense-table cell is still compressed into a column.");
    check(mobile.minCitationHeight >= 28, "mobile: citation targets are shorter than 28px.");
    check(mobile.hedgeGapReadable, "mobile: hedge-gap reason is squeezed into an unreadable column.");
    check(mobile.sourceIdColor === "rgb(32, 212, 245)", `mobile: source-ID link contrast color is wrong (${mobile.sourceIdColor}).`);
    check(print.navHidden && print.sectionsFlow && print.methodStripsAvoidBreak && print.sentimentDetailGrouped, "print: navigation or pagination grouping rules are incorrect.");
    check(print.locatorListsPrintable && print.tablesStacked && print.documentFits, "print: locators or dense tables are clipped or hidden.");
    check(print.pageCount > 0, "print: Chromium produced no countable PDF pages.");
    check(mobile.tables.every((table) => table.cellWidths.every((width) => width >= 330)), "mobile: at least one dense-table cell is narrower than the readable card width.");
    check(mobile.heroHeight <= 620, `mobile: cover is too tall (${mobile.heroHeight}px).`);
    check(mobile.bodyFontSize >= 16 && mobile.h2FontSize >= 40, "mobile: body or section typography is too small.");
    console.log(JSON.stringify({ status: "ok", desktop, mobile, print }, null, 2));
  } finally {
    if (socket?.readyState === WebSocket.OPEN) socket.close();
    chrome.kill("SIGTERM");
    await rm(profile, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(`layout QA failed: ${error.message}`);
  process.exitCode = 1;
});
