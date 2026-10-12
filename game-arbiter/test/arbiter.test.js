'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { setTimeout: delay } = require('node:timers/promises');
const { createArbiter, expandCommand } = require('../index');

const authorization = 'test-arbiter-secret';
const id = '12345678-abcd-1234-abcd-123456789abc';
const otherId = '87654321-abcd-1234-abcd-123456789abc';
const start = {
  jobId: id, placeId: 123, universeId: 456, creatorId: 12, placeVersion: 1,
  maxPlayerCount: 10, year: 2016, matchmakingContextId: 1,
  rccPort: 31000, gameServerPort: 51000, proxyPort: 7100,
};
const silent = { error() {} };

async function until(predicate) {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await delay(20);
  }
  assert.fail('Condition was not met within five seconds');
}

async function fixture(t, overrides = {}) {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'arbiter test '));
  const output = path.join(cwd, 'arguments.json');
  const script = 'require("node:fs").writeFileSync(process.argv[1], JSON.stringify(process.argv.slice(2))); setInterval(() => {}, 1000)';
  const config = {
    authorization, host: '127.0.0.1', port: 0, rccCwd: cwd, stopTimeoutMs: 100,
    soapTimeoutMs: 500,
    rccCommand: [process.execPath, '-e', script, output, '{jobId}', '{placeId}', '{rccPort}', '{rccCwd}', 'literal ; echo not-a-shell'],
    ...overrides,
  };
  const app = createArbiter(config, silent);
  t.after(async () => {
    try { await app.close(); }
    finally { fs.rmSync(cwd, { recursive: true, force: true }); }
  });
  const address = await app.listen();
  const url = `http://127.0.0.1:${address.port}`;
  async function request(route, body, headers = {}) {
    const response = await fetch(url + route, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { 'PJX-ArbiterAUTH': authorization, 'Content-Type': 'application/json', ...headers },
      body: body === undefined ? undefined : typeof body === 'string' ? body : JSON.stringify(body),
    });
    return { status: response.status, body: await response.json() };
  }
  return { app, request, url, output, cwd };
}

test('configuration rejects example secrets, shell commands and unknown placeholders', () => {
  const config = { authorization, rccCommand: [process.execPath] };
  for (const secret of ['', 'change-me', undefined]) {
    assert.throws(() => createArbiter({ ...config, authorization: secret }), /authorization/);
  }
  assert.throws(() => createArbiter({ ...config, rccCommand: 'RCCService.exe -Console' }), /array/);
  assert.throws(() => createArbiter({ ...config, rccCommand: ['{unknown}'] }), /Unknown/);
  assert.throws(() => createArbiter({ ...config, port: -1 }), /port/);
  assert.deepEqual(expandCommand(['{placeId}', '{jobId}', 'a b; c'], { placeId: 12, jobId: id }), ['12', id, 'a b; c']);
  assert.throws(() => expandCommand(['{toString}'], {}), /Unknown/);
});

test('all endpoints require the shared header and the list never returns secrets', async t => {
  const { request } = await fixture(t);
  for (const [route, body] of [['/servers', undefined], ['/start-game-server', start], ['/kill-game-server', { jobId: id }], ['/evict-player', { gameId: id, userId: 12, messageVersionId: 0 }]]) {
    for (const secret of ['', 'wrong', 'xxxxxxxxxxxxxxxxxxx']) {
      assert.equal((await request(route, body, { 'PJX-ArbiterAUTH': secret })).status, 401);
    }
  }
  const response = await request('/servers');
  assert.deepEqual(response, { status: 200, body: { servers: [] } });
  assert.equal(JSON.stringify(response).includes(authorization), false);
  assert.equal((await request('/not-an-endpoint')).status, 404);
});

test('invalid JSON, types, IDs, ports and oversized bodies cannot start a process', async t => {
  const { request, output } = await fixture(t);
  for (const body of ['{', 'null', '[]', '{}', { ...start, jobId: 'not-a-uuid' }, { ...start, rccPort: 65536 }, { ...start, rccPort: 1.5 }, { ...start, placeId: '123' }, { ...start, maxPlayerCount: 0 }]) {
    assert.equal((await request('/start-game-server', body)).status, 400);
  }
  assert.equal((await request('/start-game-server', start, { 'Content-Type': 'text/plain' })).status, 415);
  assert.equal((await request('/start-game-server', { padding: 'x'.repeat(70000) })).status, 413);
  assert.equal((await request('/kill-game-server', { jobId: 'bad' })).status, 400);
  assert.equal((await request('/evict-player', { gameId: id, userId: '12', messageVersionId: 0 })).status, 400);
  assert.equal(fs.existsSync(output), false);
  assert.deepEqual((await request('/servers')).body.servers, []);
});

test('concurrent retries share one process, substitute arguments and reject conflicting jobs', async t => {
  const { request, output, cwd } = await fixture(t);
  const results = await Promise.all(Array.from({ length: 8 }, () => request('/start-game-server', start)));
  assert.ok(results.every(result => result.status === 200));
  assert.equal(results.filter(result => !result.body.alreadyRunning).length, 1);
  assert.equal(new Set(results.map(result => result.body.pid)).size, 1);
  const retry = await request('/start-game-server', { ...start, jobId: id.toUpperCase() });
  assert.equal(retry.body.alreadyRunning, true);
  assert.equal(retry.body.pid, results[0].body.pid);
  assert.equal((await request('/start-game-server', { ...start, placeId: 789 })).status, 409);
  assert.equal((await request('/start-game-server', { ...start, jobId: otherId })).status, 409);
  await until(() => {
    try { return Array.isArray(JSON.parse(fs.readFileSync(output, 'utf8'))); }
    catch { return false; }
  });
  assert.deepEqual(JSON.parse(fs.readFileSync(output, 'utf8')), [id, '123', '31000', cwd, 'literal ; echo not-a-shell']);
  const list = (await request('/servers')).body.servers;
  assert.equal(list.length, 1);
  assert.equal(list[0].jobId, id);
  assert.equal(JSON.stringify(list).includes(authorization), false);
});

test('killing a job waits for exit, releases its ports and is safe to retry', async t => {
  const { request } = await fixture(t);
  const launched = await request('/start-game-server', start);
  const results = await Promise.all(Array.from({ length: 4 }, () => request('/kill-game-server', { jobId: id })));
  assert.ok(results.every(result => result.status === 200));
  assert.deepEqual((await request('/servers')).body.servers, []);
  assert.throws(() => process.kill(launched.body.pid, 0), /ESRCH/);
  assert.deepEqual((await request('/kill-game-server', { jobId: id })).body, { jobId: id, stopped: false });
  assert.equal((await request('/start-game-server', { ...start, jobId: otherId })).status, 200);
});

test('normal process exit removes the registry entry', async t => {
  const { request } = await fixture(t, { rccCommand: [process.execPath, '-e', 'setTimeout(() => process.exit(0), 100)'] });
  assert.equal((await request('/start-game-server', start)).status, 200);
  await until(async () => (await request('/servers')).body.servers.length === 0);
});

test('a missing RCC executable returns an error, not a successful or stuck job', async t => {
  const { request } = await fixture(t, { rccCommand: [path.join(os.tmpdir(), 'arbiter-rcc-does-not-exist')] });
  for (let i = 0; i < 2; i++) {
    assert.equal((await request('/start-game-server', start)).status, 502);
    assert.deepEqual((await request('/servers')).body.servers, []);
  }
});

test('eviction targets the registered RCC job and reports SOAP faults and timeouts', async t => {
  let soapBody = '';
  let mode = 'ok';
  const soap = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', chunk => chunks.push(chunk));
    req.on('end', () => {
      soapBody = Buffer.concat(chunks).toString();
      if (mode === 'hang') return;
      res.writeHead(mode === 'http-error' ? 503 : 200, { 'Content-Type': 'text/xml' });
      res.end(mode === 'fault' ? '<soap:Envelope><soap:Body><soap:Fault>failure</soap:Fault></soap:Body></soap:Envelope>' : '<ExecuteResponse />');
    });
  });
  await new Promise(resolve => soap.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { soap.close(resolve); soap.closeAllConnections(); }));
  const { request } = await fixture(t);
  await request('/start-game-server', { ...start, rccPort: soap.address().port });
  const body = { gameId: id, userId: 12, messageVersionId: 0 };
  assert.equal((await request('/evict-player', { ...body, gameId: otherId })).status, 404);
  assert.equal((await request('/evict-player', { ...body, messageVersionId: -1 })).status, 400);
  assert.equal((await request('/evict-player', body)).status, 200);
  assert.match(soapBody, new RegExp(`<jobID>${id}</jobID>`));
  assert.ok(soapBody.includes('player.userId == 12'));
  assert.ok(soapBody.includes('player:Kick()'));
  assert.ok(soapBody.includes('http://projex.zip/'));
  assert.equal(soapBody.includes(authorization), false);
  for (const failure of ['fault', 'http-error', 'hang']) {
    mode = failure;
    assert.equal((await request('/evict-player', body)).status, 502);
  }
  assert.equal((await request('/servers')).body.servers.length, 1);
});

test('eviction fails when no RCC SOAP listener is reachable', async t => {
  const unused = http.createServer();
  await new Promise(resolve => unused.listen(0, '127.0.0.1', resolve));
  const port = unused.address().port;
  await new Promise(resolve => unused.close(resolve));
  const { request } = await fixture(t);
  await request('/start-game-server', { ...start, rccPort: port });
  assert.equal((await request('/evict-player', { gameId: id, userId: 12, messageVersionId: 0 })).status, 502);
});

test('closing the arbiter stops its children and can be called twice', async t => {
  const { app, request } = await fixture(t);
  const result = await request('/start-game-server', start);
  await Promise.all([app.close(), app.close()]);
  assert.throws(() => process.kill(result.body.pid, 0), /ESRCH/);
});

test('shutdown cannot leave a child from a request whose body was still arriving', async t => {
  const { app, url, output } = await fixture(t);
  const received = new Promise(resolve => app.server.once('request', resolve));
  const body = JSON.stringify(start);
  let req;
  const response = new Promise((resolve, reject) => {
    req = http.request(url + '/start-game-server', {
      method: 'POST', headers: { 'PJX-ArbiterAUTH': authorization, 'Content-Type': 'application/json' },
    }, res => { res.resume(); res.on('end', () => resolve(res.statusCode)); });
    req.on('error', reject);
  });
  req.write(body.slice(0, 10));
  await received;
  const closed = app.close();
  req.end(body.slice(10));
  assert.equal(await response, 503);
  await closed;
  assert.equal(fs.existsSync(output), false);
});
