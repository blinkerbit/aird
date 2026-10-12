/**
 * Record an animated WebP demo for README.md.
 *
 * Requires: npm install (playwright, @ffmpeg-installer/ffmpeg) && npx playwright install chromium
 * Usage:    npm run demo:record
 */
import { spawn } from 'node:child_process';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import ffmpegPath from '@ffmpeg-installer/ffmpeg';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '..');
const DEMO_TOKEN = 'aird-demo-recording-token';
const PORT = 9876;
const BASE = `http://127.0.0.1:${PORT}`;
const VIEWPORT = { width: 1280, height: 720 };

const demoDir = path.join(os.tmpdir(), 'aird-demo-recording');
const fileRoot = path.join(demoDir, 'files');
const dataHome = path.join(demoDir, 'data');
const videoDir = path.join(demoDir, 'video');
const webmPath = path.join(videoDir, 'capture.webm');
const outWebp = path.join(REPO_ROOT, 'demo.webp');

function ensureDemoFiles() {
  fs.rmSync(demoDir, { recursive: true, force: true });
  fs.mkdirSync(fileRoot, { recursive: true });
  fs.mkdirSync(dataHome, { recursive: true });
  fs.mkdirSync(videoDir, { recursive: true });

  const files = {
    'projects/aird/README.md': `# Aird sample project\n\nSelf-hosted file browser with parallel HTTP transfers.\n`,
    'projects/aird/app.py': '"""Sample module."""\n\ndef greet(name: str) -> str:\n    return f"Hello, {name}!"\n',
    'documents/notes.txt': 'Meeting notes\n- parallel HTTP uploads\n- super search glob patterns\n- secure shares\n',
    'documents/report-q1.md': '# Q1 Report\n\nRevenue up 12%.\n',
    'media/playlist.m3u': '#EXTM3U\n#EXTINF:240,Track One\naudio/track1.mp3\n',
    'archives/config.json': JSON.stringify({ theme: 'dark', uploads: { chunk_mb: 90 } }, null, 2),
    'uploads/incoming/.gitkeep': '',
  };
  for (const [rel, content] of Object.entries(files)) {
    const dest = path.join(fileRoot, rel);
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.writeFileSync(dest, content);
  }
}

function waitForHealth(timeoutMs = 60_000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const tick = () => {
      const req = http.get(`${BASE}/health`, (res) => {
        res.resume();
        if (res.statusCode === 200) resolve();
        else if (Date.now() > deadline) reject(new Error(`health status ${res.statusCode}`));
        else setTimeout(tick, 400);
      });
      req.on('error', () => {
        if (Date.now() > deadline) reject(new Error('server did not start'));
        else setTimeout(tick, 400);
      });
    };
    tick();
  });
}

function serverPython() {
  const venvPy =
    process.platform === 'win32'
      ? path.join(REPO_ROOT, '.venv', 'Scripts', 'python.exe')
      : path.join(REPO_ROOT, '.venv', 'bin', 'python3');
  if (fs.existsSync(venvPy)) return venvPy;
  return process.platform === 'win32' ? 'python' : 'python3';
}

function startServer() {
  const env = {
    ...process.env,
    AIRD_ACCESS_TOKEN: DEMO_TOKEN,
    LOCALAPPDATA: dataHome,
    APPDATA: dataHome,
    XDG_DATA_HOME: dataHome,
  };
  const child = spawn(serverPython(), ['-m', 'aird', '--root', fileRoot, '--port', String(PORT)], {
    cwd: REPO_ROOT,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  child.stdout?.on('data', (d) => process.stderr.write(d));
  child.stderr?.on('data', (d) => process.stderr.write(d));
  return child;
}

async function pause(page, ms) {
  await page.waitForTimeout(ms);
}

async function recordUi(page) {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
  await pause(page, 1800);

  await page.click('#tab-token');
  await pause(page, 400);
  await page.fill('#access-token', DEMO_TOKEN);
  await pause(page, 600);
  await page.click('#form-token button[type="submit"]');
  await page.waitForURL('**/files/**', { timeout: 15_000 });
  await pause(page, 1200);

  await page.locator('#fileTable a.file-link', { hasText: 'projects' }).first().click();
  await page.waitForLoadState('networkidle');
  await pause(page, 1000);
  await page.locator('#fileTable a.file-link', { hasText: 'aird' }).first().click();
  await page.waitForLoadState('networkidle');
  await pause(page, 800);

  await page.locator('a[title="Super Search"]').click();
  await page.waitForLoadState('networkidle');
  await pause(page, 500);
  await page.fill('#pattern', '*.py');
  await pause(page, 400);
  await page.click('#searchBtn');
  await pause(page, 2500);

  await page.locator('a[title="Browse files"]').click();
  await page.waitForLoadState('networkidle');
  await page.goto(`${BASE}/files/projects/aird`, { waitUntil: 'networkidle' });
  await pause(page, 600);

  const uploadZone = page.locator('#uploadZone');
  await uploadZone.scrollIntoViewIfNeeded();
  await uploadZone.hover();
  await pause(page, 900);

  const row = page.locator('#fileTable tbody tr').filter({ hasText: 'app.py' }).first();
  await row.locator('input[type="checkbox"]').check({ force: true });
  await pause(page, 1200);

  await page.locator('a[title="Share files"]').click();
  await page.waitForLoadState('networkidle');
  await pause(page, 2000);

  await page.locator('a[title="Browse files"]').click();
  await page.waitForLoadState('networkidle');
  await pause(page, 800);
  await page.locator('#fileViewToggleBtn').click();
  await pause(page, 1200);
}

function webmToWebp(src, dest) {
  execFileSync(
    ffmpegPath.path,
    [
      '-y',
      '-i', src,
      '-vf', 'fps=12,scale=1280:-2',
      '-c:v', 'libwebp',
      '-lossless', '0',
      '-compression_level', '4',
      '-q:v', '82',
      '-loop', '0',
      '-an',
      '-vsync', 'vfr',
      dest,
    ],
    { stdio: 'inherit' },
  );
}

async function main() {
  ensureDemoFiles();
  const server = startServer();
  let browser;

  try {
    await waitForHealth();
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      viewport: VIEWPORT,
      deviceScaleFactor: 1,
      colorScheme: 'dark',
      recordVideo: { dir: videoDir, size: VIEWPORT },
    });
    const page = await context.newPage();
    await recordUi(page);
    await pause(page, 500);
    const video = page.video();
    await context.close();
    const saved = video ? await video.path() : webmPath;
    if (!fs.existsSync(saved)) {
      throw new Error('no video captured');
    }
    webmToWebp(saved, outWebp);
    const sizeMb = (fs.statSync(outWebp).size / (1024 * 1024)).toFixed(2);
    console.log(`Wrote ${outWebp} (${sizeMb} MB)`);
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
    try {
      await new Promise((r) => server.on('exit', r));
    } catch {
      /* ignore */
    }
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
