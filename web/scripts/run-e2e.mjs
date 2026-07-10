import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import process from 'node:process';


const webRoot = resolve(import.meta.dirname, '..');
const repoRoot = resolve(webRoot, '..');
const windowsPython = resolve(repoRoot, '.venv', 'Scripts', 'python.exe');
const unixPython = resolve(repoRoot, '.venv', 'bin', 'python');
const python = existsSync(windowsPython) ? windowsPython : existsSync(unixPython) ? unixPython : 'python';

await assertPortFree('http://127.0.0.1:8000/api/v2/health', 8000);
await assertPortFree('http://127.0.0.1:5173', 5173);

const backend = start(python, [resolve(repoRoot, 'scripts', 'run_e2e_server.py')], repoRoot);
const frontend = start(
  process.execPath,
  [resolve(webRoot, 'node_modules', 'vite', 'bin', 'vite.js'), '--host', '127.0.0.1', '--port', '5173'],
  webRoot,
);

let exitCode = 1;
try {
  await waitFor('http://127.0.0.1:8000/api/v2/health');
  await waitFor('http://127.0.0.1:5173');
  exitCode = await run(
    process.execPath,
    [resolve(webRoot, 'node_modules', '@playwright', 'test', 'cli.js'), 'test'],
    webRoot,
  );
} finally {
  stopTree(frontend);
  stopTree(backend);
}

process.exit(exitCode);


function start(command, args, cwd) {
  return spawn(command, args, {
    cwd,
    stdio: 'ignore',
    detached: process.platform !== 'win32',
    windowsHide: true,
  });
}

function run(command, args, cwd) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, args, { cwd, stdio: 'inherit', windowsHide: true });
    child.once('error', reject);
    child.once('exit', (code) => resolvePromise(code ?? 1));
  });
}

async function waitFor(url) {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // The service is still starting.
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 150));
  }
  throw new Error(`Timed out waiting for ${url}`);
}

async function assertPortFree(url, port) {
  try {
    await fetch(url, { signal: AbortSignal.timeout(300) });
  } catch {
    return;
  }
  throw new Error(`Port ${port} is already in use; stop the existing service before E2E tests`);
}

function stopTree(child) {
  if (!child.pid) return;
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true });
    return;
  }
  try {
    process.kill(-child.pid, 'SIGTERM');
  } catch {
    // The service may already have exited.
  }
}
