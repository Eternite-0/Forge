// Real localhost Core + Chromium integration; no HTTP response mocking.
// Prerequisites: installed backend dependencies, desktop npm ci, Playwright.
// Run from any directory with FORGE_TEST_PYTHON=/path/to/python and optionally
// FORGE_PLAYWRIGHT_MODULE=/path/to/playwright-or-launch-wrapper.cjs.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHmac, randomBytes } from 'node:crypto';
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const backend = path.resolve(desktop, '../backend');
const python = process.env.FORGE_TEST_PYTHON || 'python';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.FORGE_PLAYWRIGHT_MODULE || 'playwright');
const temporary = await mkdtemp(path.join(os.tmpdir(), 'forge-core-transcript-'));
const workspace = path.join(temporary, 'workspace');
await mkdir(workspace);
const secret = randomBytes(32).toString('hex');
// Do not inherit real provider, tool, workspace or credential configuration.
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('FORGE_')));
Object.assign(env, { PYTHONPATH: path.join(backend, 'src'), FORGE_WORKSPACE_ROOT: workspace, FORGE_CORE_SECRET: secret, PYTHONDONTWRITEBYTECODE: '1' });
const base = 'http://127.0.0.1:8765';
const checks = [], responses = [], pageErrors = [], external = [], writes = [];
let core, ui, browser, page;
const logs = [];
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
function start(command, args, options) {
  const child = spawn(command, args, { ...options, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', data => logs.push(data.toString()));
  child.stderr.on('data', data => logs.push(data.toString()));
  child.on('error', error => logs.push(error.message));
  return child;
}
async function stop(child) {
  if (!child || child.exitCode !== null) return;
  const done = new Promise(resolve => child.once('exit', resolve));
  child.kill();
  await Promise.race([done, pause(5000)]);
  if (child.exitCode === null) { child.kill('SIGKILL'); await done; }
}
async function freePort(port) {
  await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => server.close(resolve));
  });
}
async function ready(url, child) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (child.exitCode !== null) throw new Error(`Server exited before readiness: ${child.exitCode}`);
    try { const response = await fetch(url); if (response.ok) return response; } catch {}
    await pause(100);
  }
  throw new Error(`Server not ready: ${url}`);
}
function launchCore() {
  return start(python, ['-m', 'uvicorn', 'forge_api.main:app', '--host', '127.0.0.1', '--port', '8765', '--loop', 'asyncio'], { cwd: temporary, env });
}
async function seed(startAt, count) {
  // Durable Store writes in a separate offline process. No model/tool execution.
  const child = start(python, ['-c', `
import sys
from forge_core.settings import Settings
from forge_core.store import Store
from forge_core.domain import ConversationMessage
settings = Settings.load()
store = Store(settings.db_path, workspace=settings.workspace)
with store.unit_of_work() as work:
    for number in range(int(sys.argv[1]), int(sys.argv[1]) + int(sys.argv[2])):
        work.add_message(ConversationMessage(id=f"integration-{number:04}", role="assistant",
            content=f"Durable offline message {number:04}",
            created_at="2026-11-17T00:00:00Z", metadata={"execution_mode": "mock"}))
`, String(startAt), String(count)], { cwd: temporary, env });
  const code = await new Promise(resolve => child.once('exit', resolve));
  assert.equal(code, 0, 'offline Store seed succeeds');
}
const ok = (value, label) => { assert.ok(value, label); checks.push(label); };
try {
  await freePort(8765); await freePort(5173);
  core = launchCore();
  let health = await (await ready(`${base}/health`, core)).json();
  const oldToken = createHmac('sha256', Buffer.from(secret, 'hex')).update(health.session).digest('hex');
  ok((await fetch(`${base}/api/conversation`)).status === 401, 'unauthenticated Core read denied');
  await seed(0, 260);
  ui = start(process.execPath, [path.join(desktop, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', '5173', '--strictPort'], { cwd: desktop, env });
  await ready('http://127.0.0.1:5173/', ui);
  browser = await chromium.launch({ headless: true });
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(30000);
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('request', request => {
    if (!['GET', 'OPTIONS'].includes(request.method())) writes.push(`${request.method()} ${new URL(request.url()).pathname}`);
  });
  // No local HTTP is mocked; only unexpected external network is blocked.
  await page.route('**/*', route => {
    const origin = new URL(route.request().url()).origin;
    if (['http://127.0.0.1:5173', base].includes(origin)) return route.continue();
    external.push(origin); return route.abort();
  });
  page.on('response', async response => {
    if (new URL(response.url()).pathname === '/api/conversation' && response.ok()) {
      try { const body = await response.json(); responses.push({ url: response.url(), mode: body.mode, count: body.messages.length, has_newer: body.has_newer }); } catch {}
    }
  });
  const connect = async () => {
    await page.getByLabel('Core 启动密钥').fill(secret);
    await page.getByRole('button', { name: '连接', exact: true }).click();
    await page.locator('.simple-connection').waitFor({ state: 'hidden' });
  };
  const ids = () => page.locator('[data-scroll-key^="message:integration-"]').evaluateAll(elements => elements.map(element => element.dataset.scrollKey.slice('message:'.length)));
  const waitCount = async count => page.waitForFunction(expected => document.querySelectorAll('[data-scroll-key^="message:integration-"]').length === expected, count);
  const older = page.getByRole('button', { name: '加载更早的消息与记录', exact: true });
  await page.goto('http://127.0.0.1:5173/'); await connect(); await waitCount(100);
  ok((await ids())[0] === 'integration-0160', 'baseline is the real most-recent 100-message page');
  await seed(260, 450); await waitCount(550);
  // Browser rendering and Node's asynchronous response.json() observation can
  // settle in either order. Wait for the recorder, not another server poll.
  for (let attempt = 0; attempt < 100 && responses.filter(response => response.mode === 'delta' && response.count > 0).length < 5; attempt++) await pause(20);
  ok(responses.filter(response => response.mode === 'delta' && response.count > 0).length >= 5, '450 committed messages drain through at least five actual forward batches');
  await older.click(); await waitCount(650);
  await older.click(); await waitCount(710);
  const expected = count => Array.from({ length: count }, (_, index) => `integration-${String(index).padStart(4, '0')}`);
  assert.deepEqual(await ids(), expected(710)); checks.push('older page plus forward drain has exact order, no gaps or duplicates');
  await stop(core); core = launchCore();
  health = await (await ready(`${base}/health`, core)).json();
  ok(createHmac('sha256', Buffer.from(secret, 'hex')).update(health.session).digest('hex') !== oldToken, 'Core restart rotates the authenticated session');
  ok((await fetch(`${base}/api/conversation`, { headers: { Authorization: `Bearer ${oldToken}` } })).status === 401, 'previous session bearer denied after restart');
  await seed(710, 5);
  // Browser mode deliberately reconnects via the visible secret-entry flow.
  // This is not a claim about native Tauri automatic reconnection.
  await page.reload(); await connect(); await waitCount(100);
  for (let count = 100; count < 715;) {
    await older.click(); count = Math.min(count + 100, 715); await waitCount(count);
  }
  assert.deepEqual(await ids(), expected(715)); checks.push('same durable workspace survives Core restart and authenticated reconnect without gaps or duplicates');
  const token = createHmac('sha256', Buffer.from(secret, 'hex')).update(health.session).digest('hex');
  const turnUrl = `${base}/api/conversation/turn?request_id=integration-committed-turn`;
  ok((await fetch(turnUrl)).status === 401, 'turn lookup also requires authentication');
  const authorized = { headers: { Authorization: `Bearer ${token}` } };
  const unknown = await fetch(turnUrl, authorized);
  ok((await unknown.json()).state === 'unknown', 'missing committed turn is honestly unknown');
  // Commit through the real Router and Store with an explicit deterministic
  // Mock provider, not by fabricating a router_turns database row.
  const commit = start(python, ['-c', `
import asyncio, json
from forge_core.settings import Settings
from forge_core.store import Store
from forge_core.registry import ModelRegistry, TeamComposer
from forge_core.router import RouterService
from forge_core.runtime import ModelResponse
class OfflineProvider:
    execution_mode = "mock"
    async def generate(self, request):
        return ModelResponse(text=json.dumps({"intent":"chat", "reply":"Offline committed response",
            "title":None, "goal":None, "quest_id":None, "candidates":[]}), tool_calls=[], usage={})
settings = Settings.load()
store = Store(settings.db_path, workspace=settings.workspace)
router = RouterService(store, TeamComposer(ModelRegistry(store)), provider=OfflineProvider(),
    model_id="explicit-offline-mock", context_limit=65536)
asyncio.run(router.handle_user_message("Offline committed request", request_id="integration-committed-turn"))
`], { cwd: temporary, env });
  assert.equal(await new Promise(resolve => commit.once('exit', resolve)), 0, 'offline real Router commits a turn');
  const committed = await fetch(turnUrl, authorized), turn = await committed.json();
  ok(committed.status === 200 && committed.headers.get('cache-control') === 'no-store' && turn.state === 'committed'
    && turn.user_message.content === 'Offline committed request' && turn.assistant_message.content === 'Offline committed response',
  'authenticated no-store lookup reads the actual offline Router-committed turn');
  ok(pageErrors.length === 0, `no uncaught browser errors: ${pageErrors.join('; ')}`);
  ok(external.length === 0 && writes.length === 0, 'browser performs no provider traffic or mutation requests');
  await page.screenshot({ path: path.join(temporary, 'reconnected.png') });
  console.log(JSON.stringify({ checks, responses, workspace, evidence: 'Actual local Core/Store/HTTP/auth + Chromium; deterministic offline rows, no model or native Tauri claims.' }, null, 2));
} catch (error) {
  if (page && !page.isClosed()) await page.screenshot({ path: path.join(temporary, 'failure.png') }).catch(() => {});
  await writeFile(path.join(temporary, 'failure.json'), JSON.stringify({ error: String(error), checks, responses, pageErrors, external, writes }, null, 2));
  throw error;
} finally {
  await browser?.close(); await stop(ui); await stop(core);
  await writeFile(path.join(temporary, 'servers.log'), logs.join(''));
  console.log(`Integration artifacts: ${temporary}`);
}
