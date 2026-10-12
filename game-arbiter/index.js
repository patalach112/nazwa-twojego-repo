'use strict';

const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { timingSafeEqual } = require('node:crypto');

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function integer(value, name, min = 0, max = Number.MAX_SAFE_INTEGER) {
  if (!Number.isSafeInteger(value) || value < min || value > max) {
    throw new HttpError(400, `${name} must be an integer between ${min} and ${max}`);
  }
  return value;
}

function jobId(value) {
  if (typeof value !== 'string' || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value)) {
    throw new HttpError(400, 'jobId/gameId must be a UUID');
  }
  return value.toLowerCase();
}

function startRequest(body) {
  const result = { jobId: jobId(body.jobId) };
  for (const name of ['placeId', 'universeId', 'creatorId', 'placeVersion', 'matchmakingContextId', 'year', 'maxPlayerCount']) {
    result[name] = integer(body[name], name, ['placeId', 'maxPlayerCount', 'year'].includes(name) ? 1 : 0);
  }
  for (const name of ['rccPort', 'gameServerPort', 'proxyPort']) {
    result[name] = integer(body[name], name, 1, 65535);
  }
  return result;
}

function expandCommand(command, values) {
  return command.map(part => part.replace(/\{(\w+)\}/g, (_, name) => {
    if (!Object.hasOwn(values, name)) throw new Error(`Unknown rccCommand placeholder: {${name}}`);
    return String(values[name]);
  }));
}

function readJson(req) {
  if ((req.headers['content-type'] || '').split(';')[0].trim().toLowerCase() !== 'application/json') {
    req.resume();
    throw new HttpError(415, 'Use Content-Type: application/json');
  }
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    let tooLarge = false;
    req.on('data', chunk => {
      size += chunk.length;
      if (size > 65536) {
        chunks.length = 0;
        tooLarge = true;
        reject(new HttpError(413, 'Request body is too large'));
      } else if (!tooLarge) {
        chunks.push(chunk);
      }
    });
    req.on('error', reject);
    req.on('end', () => {
      if (tooLarge) return;
      try {
        const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
        if (!body || typeof body !== 'object' || Array.isArray(body)) throw new Error('Not an object');
        resolve(body);
      } catch {
        reject(new HttpError(400, 'Request body must be a JSON object'));
      }
    });
  });
}

function xml(value) {
  return String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' }[c]));
}

function evictPlayer(config, entry, userId) {
  const script = `for _, player in pairs(game:GetService("Players"):GetPlayers()) do if player.userId == ${userId} then player:Kick() end end`;
  const body = `<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body><Execute xmlns="${xml(config.soapNamespace)}">
    <jobID>${entry.request.jobId}</jobID>
    <script><name>EvictPlayer</name><script>${xml(script)}</script></script>
  </Execute></soap:Body>
</soap:Envelope>`;
  return new Promise((resolve, reject) => {
    const req = http.request({
      hostname: '127.0.0.1', port: entry.request.rccPort, method: 'POST', path: '/',
      signal: AbortSignal.timeout(config.soapTimeoutMs),
      headers: { 'Content-Type': 'text/xml; charset=utf-8', 'Content-Length': Buffer.byteLength(body) },
    }, res => {
      let response = '';
      res.setEncoding('utf8');
      res.on('data', chunk => {
        response += chunk;
        if (response.length > 1024 * 1024) res.destroy(new Error('RCC response is too large'));
      });
      res.on('error', () => reject(new HttpError(502, 'RCC SOAP request failed')));
      res.on('end', () => {
        if (res.statusCode < 200 || res.statusCode >= 300 || /<(?:[\w-]+:)?Fault(?:\s|>)/i.test(response)) {
          reject(new HttpError(502, 'RCC rejected the eviction request'));
        } else {
          resolve();
        }
      });
    });
    req.on('error', () => reject(new HttpError(502, 'Could not reach RCC for eviction')));
    req.end(body);
  });
}

function terminate(child, timeoutMs) {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
  return new Promise((resolve, reject) => {
    let forceTimer;
    let failureTimer;
    const finish = error => {
      clearTimeout(forceTimer);
      clearTimeout(failureTimer);
      child.removeListener('exit', onExit);
      child.removeListener('error', onError);
      if (error) reject(new HttpError(502, 'Could not stop the RCC process'));
      else resolve();
    };
    const onExit = () => finish();
    const onError = error => finish(error);
    child.once('exit', onExit);
    child.once('error', onError);
    try {
      if (!child.kill('SIGTERM')) throw new Error('SIGTERM failed');
      forceTimer = setTimeout(() => {
        try {
          if (!child.kill('SIGKILL')) throw new Error('SIGKILL failed');
          failureTimer = setTimeout(() => finish(new Error('RCC did not exit')), 1000);
        } catch (error) { finish(error); }
      }, timeoutMs);
    } catch (error) { finish(error); }
  });
}

function createArbiter(input, logger = console) {
  const config = { host: '127.0.0.1', port: 4310, soapNamespace: 'http://projex.zip/', soapTimeoutMs: 10000, stopTimeoutMs: 5000, ...input };
  if (typeof config.authorization !== 'string' || !config.authorization || config.authorization.startsWith('change-me')) {
    throw new Error('Set authorization to the website\'s ArbiterAuthorization, not the example secret');
  }
  if (!Array.isArray(config.rccCommand) || !config.rccCommand.length ||
      config.rccCommand.some(part => typeof part !== 'string') || !config.rccCommand[0]) {
    throw new Error('rccCommand must be an array containing the executable and its arguments');
  }
  integer(config.port, 'port', 0, 65535);
  integer(config.soapTimeoutMs, 'soapTimeoutMs', 1, 120000);
  integer(config.stopTimeoutMs, 'stopTimeoutMs', 1, 60000);
  if (typeof config.host !== 'string' || !config.host) throw new Error('host must be a nonempty string');
  if (typeof config.soapNamespace !== 'string' || !config.soapNamespace) throw new Error('soapNamespace must be a nonempty string');
  config.rccCwd = path.resolve(config.rccCwd || path.join(__dirname, '../RCCService'));
  const fields = ['jobId', 'placeId', 'universeId', 'creatorId', 'placeVersion', 'matchmakingContextId', 'year', 'maxPlayerCount', 'rccPort', 'gameServerPort', 'proxyPort', 'rccCwd'];
  expandCommand(config.rccCommand, Object.fromEntries(fields.map(name => [name, ''])));
  const secret = Buffer.from(config.authorization);
  const jobs = new Map();
  let closing = false;
  let closePromise;

  const forget = entry => { if (jobs.get(entry.request.jobId) === entry) jobs.delete(entry.request.jobId); };
  async function start(body) {
    if (closing) throw new HttpError(503, 'Arbiter is shutting down');
    const request = startRequest(body);
    const existing = jobs.get(request.jobId);
    if (existing) {
      if (JSON.stringify(existing.request) !== JSON.stringify(request)) throw new HttpError(409, 'This jobId already has different launch parameters');
      if (existing.stopping) throw new HttpError(409, 'This job is stopping');
      await existing.ready;
      return { jobId: request.jobId, pid: existing.child.pid, alreadyRunning: true };
    }
    for (const entry of jobs.values()) {
      if (['rccPort', 'gameServerPort', 'proxyPort'].some(name => entry.request[name] === request[name])) {
        throw new HttpError(409, 'A requested port is already reserved by another job');
      }
    }
    const entry = { request, startedAt: new Date().toISOString() };
    jobs.set(request.jobId, entry);
    entry.ready = new Promise((resolve, reject) => {
      try {
        const [executable, ...args] = expandCommand(config.rccCommand, { ...request, rccCwd: config.rccCwd });
        entry.child = spawn(executable, args, { cwd: config.rccCwd, shell: false, stdio: ['ignore', 'inherit', 'inherit'] });
        entry.child.once('spawn', resolve);
        entry.child.on('error', error => {
          logger.error(`RCC ${request.jobId}: ${error.code || error.message}`);
          if (!entry.child.pid) {
            forget(entry);
            reject(new HttpError(502, 'Could not launch RCC; check rccCommand and rccCwd'));
          }
        });
        entry.child.once('exit', () => forget(entry));
      } catch {
        forget(entry);
        reject(new HttpError(502, 'Could not launch RCC; check rccCommand and rccCwd'));
      }
    });
    await entry.ready;
    return { jobId: request.jobId, pid: entry.child.pid, alreadyRunning: false };
  }

  async function stop(id) {
    const entry = jobs.get(id);
    if (!entry) return { jobId: id, stopped: false };
    await entry.ready;
    if (!entry.stopping) {
      entry.stopping = terminate(entry.child, config.stopTimeoutMs).catch(error => {
        entry.stopping = null;
        throw error;
      });
    }
    await entry.stopping;
    forget(entry);
    return { jobId: id, stopped: true };
  }

  const server = http.createServer(async (req, res) => {
    const send = (status, body) => {
      if (res.destroyed) return;
      const headers = { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' };
      if (closing) headers.Connection = 'close';
      res.writeHead(status, headers);
      res.end(JSON.stringify(body));
    };
    try {
      const provided = Buffer.from(req.headers['pjx-arbiterauth'] || '');
      if (provided.length !== secret.length || !timingSafeEqual(provided, secret)) {
        req.resume();
        throw new HttpError(401, 'Unauthorized');
      }
      if (closing) {
        req.resume();
        throw new HttpError(503, 'Arbiter is shutting down');
      }
      const route = req.url.split('?')[0];
      if (req.method === 'GET' && route === '/servers') {
        send(200, { servers: [...jobs.values()].map(entry => ({ ...entry.request, pid: entry.child.pid, startedAt: entry.startedAt })) });
        return;
      }
      if (req.method !== 'POST' || !['/start-game-server', '/kill-game-server', '/evict-player'].includes(route)) {
        req.resume();
        throw new HttpError(404, 'Not found');
      }
      const body = await readJson(req);
      if (route === '/start-game-server') send(200, await start(body));
      else if (route === '/kill-game-server') send(200, await stop(jobId(body.jobId)));
      else {
        const id = jobId(body.gameId);
        const userId = integer(body.userId, 'userId', 1);
        integer(body.messageVersionId, 'messageVersionId');
        const entry = jobs.get(id);
        if (!entry) throw new HttpError(404, 'Unknown gameId');
        if (entry.stopping) throw new HttpError(409, 'This job is stopping');
        await entry.ready;
        await evictPlayer(config, entry, userId);
        send(200, { gameId: id, userId });
      }
    } catch (error) {
      if (!error.status) logger.error(error.message);
      send(error.status || 500, { error: error.status ? error.message : 'Internal server error' });
    }
  });
  server.requestTimeout = 15000;
  server.headersTimeout = 10000;

  return {
    server,
    listen: () => new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(config.port, config.host, () => { server.removeListener('error', reject); resolve(server.address()); });
    }),
    close: () => {
      if (!closePromise) {
        closing = true;
        closePromise = (async () => {
          const closed = new Promise(resolve => server.close(resolve));
          server.closeIdleConnections();
          const results = await Promise.allSettled([...jobs.keys()].map(stop));
          await closed;
          for (const result of results) if (result.status === 'rejected') throw result.reason;
        })();
      }
      return closePromise;
    },
  };
}

if (require.main === module) {
  (async () => {
    const filename = path.resolve(process.argv[2] || path.join(__dirname, 'config.json'));
    if (!fs.existsSync(filename)) throw new Error('config.json is missing; run ./scripts/setup.sh or copy config.example.json first');
    const config = JSON.parse(fs.readFileSync(filename, 'utf8'));
    config.rccCwd = path.resolve(path.dirname(filename), config.rccCwd || '../RCCService');
    if (process.env.ARBITER_HOST) config.host = process.env.ARBITER_HOST;
    if (process.env.ARBITER_PORT) config.port = Number(process.env.ARBITER_PORT);
    const arbiter = createArbiter(config);
    const address = await arbiter.listen();
    console.log(`Game arbiter listening on ${address.address}:${address.port}`);
    for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => {
      arbiter.close().catch(error => { console.error(error.message); process.exitCode = 1; });
    });
  })().catch(error => { console.error(error.message); process.exitCode = 1; });
}

module.exports = { createArbiter, expandCommand };
