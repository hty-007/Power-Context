import { spawn, spawnSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { once } from 'node:events';
import { mkdir } from 'node:fs/promises';
import { createServer } from 'node:net';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { powerContext } from './powercontext.mjs';

async function localPort() {
  const server = createServer();
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const { port } = server.address();
  await new Promise(resolve => server.close(resolve));
  return port;
}

export async function startPowerContext(runtime) {
  const executable = process.env.POWERCONTEXT_EXE || 'powercontext';
  const version = spawnSync(executable, ['--version'], { encoding: 'utf8', windowsHide: true, timeout: 30_000 });
  if (version.status !== 0 || version.stdout.trim() !== '1.4.1') {
    throw new Error('Install powercontext[cli,server]==1.4.1, or set POWERCONTEXT_EXE to that executable.');
  }
  const folder = join(runtime, 'powercontext');
  await mkdir(folder, { recursive: true });
  const port = await localPort();
  const token = randomBytes(32).toString('hex');
  const env = {
    ...process.env,
    POWERCONTEXT_HOME: join(folder, 'home'),
    POWERCONTEXT_SERVER_DATABASE_KIND: 'sqlite',
    POWERCONTEXT_SERVER_DATABASE_URL: `sqlite+aiosqlite:///${join(folder, 'tasks.db').replaceAll('\\', '/')}`,
    POWERCONTEXT_SERVER_MCP_ENABLED: 'false',
    POWERCONTEXT_SERVER_ACCESS_MODE: 'enforced',
    POWERCONTEXT_SERVER_AUTH_TOKEN: token,
  };
  const child = spawn(executable, ['server', 'run', '--host', '127.0.0.1', '--port', String(port)], {
    cwd: folder, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  });
  const url = `http://127.0.0.1:${port}`;
  let log = '';
  for (const stream of [child.stdout, child.stderr]) {
    stream.setEncoding('utf8').on('data', data => { log = (log + data).slice(-20_000); });
  }
  let exited = false;
  const closed = new Promise(resolve => {
    child.once('error', error => { log += error.message; exited = true; });
    child.once('close', () => { exited = true; resolve(); });
  });
  const stop = async () => {
    if (!exited) child.kill();
    await closed;
  };
  const deadline = Date.now() + 90_000;
  const pc = powerContext(url, token);
  while (!exited && Date.now() < deadline) {
    try {
      await pc.request('GET', '/v1/capabilities');
      return { version: version.stdout.trim(), pid: child.pid, url, token, stop };
    } catch {
      await sleep(450);
    }
  }
  await stop();
  throw new Error(`PowerContext startup failed.\n${log.replaceAll(token, '[redacted]')}`);
}
