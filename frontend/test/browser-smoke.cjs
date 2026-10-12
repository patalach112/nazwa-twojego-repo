// Real Chromium rendering and interactions, with explicit sample API fixtures.
// This is not the .NET integration test and cannot establish login or game joins.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright-core');
const {createFixtureServer} = require('./visual-fixtures.cjs');

const root = path.resolve(__dirname, '..');
const mode = process.env.BROWSER_MODE || 'development';
assert(['development', 'production'].includes(mode), 'BROWSER_MODE must be development or production');
const output = path.join(root, '.visual-results', mode);
const logs = [];
const results = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '0.0.0.0', () => resolve(server.address().port));
  });
}
function run(args, env) {
  const child = spawn(process.execPath, [require.resolve('next/dist/bin/next'), ...args], {
    cwd: root, env: {...process.env, ...env, NEXT_TELEMETRY_DISABLED: '1'},
    detached: process.platform !== 'win32', stdio: ['ignore', 'pipe', 'pipe'],
  });
  for (const stream of [child.stdout, child.stderr]) stream.on('data', data => {
    logs.push(data.toString());
    if (logs.length > 400) logs.shift();
  });
  return child;
}
function exited(child) {
  return new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('exit', code => code === 0 ? resolve() : reject(new Error('Next exited with code ' + code)));
  });
}
async function stop(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const signal = value => process.platform === 'win32' ? child.kill(value) : process.kill(-child.pid, value);
  signal('SIGTERM');
  await Promise.race([new Promise(resolve => child.once('exit', resolve)), delay(5000)]);
  if (child.exitCode === null && child.signalCode === null) signal('SIGKILL');
}

async function main() {
  fs.mkdirSync(output, {recursive: true});
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'frontend-browser-'));
  const fixture = createFixtureServer();
  let server, browser;
  try {
    const fixturePort = await listen(fixture);
    const reservation = net.createServer();
    const port = await listen(reservation);
    await new Promise(resolve => reservation.close(resolve));
    const origin = 'http://127.0.0.1:' + port;
    const config = JSON.parse(fs.readFileSync(path.join(root, 'config.example.json'), 'utf8'));
    config.publicRuntimeConfig.backend = {...config.publicRuntimeConfig.backend,
      proxyEnabled: true, baseUrl: 'http://127.0.0.1:' + fixturePort,
      apiFormat: 'http://127.0.0.1:' + fixturePort + '/apisite/{0}{1}'};
    const configPath = path.join(temporary, 'visual-config.json');
    fs.writeFileSync(configPath, JSON.stringify(config), {mode: 0o600});
    const env = {FRONTEND_CONFIG_PATH: configPath};
    if (mode === 'production') await exited(run(['build'], env));
    server = run([mode === 'production' ? 'start' : 'dev', '--hostname', '0.0.0.0', '--port', String(port)], env);
    const deadline = Date.now() + 90000;
    while (true) {
      if (server.exitCode !== null || server.signalCode !== null) throw new Error('Next exited before becoming ready');
      try { if ((await fetch(origin, {signal: AbortSignal.timeout(3000)})).ok) break; } catch (_) {}
      if (Date.now() > deadline) throw new Error('Next did not become ready within 90 seconds');
      await delay(150);
    }
    browser = await chromium.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE_PATH || undefined,
      args: ['--no-sandbox', '--disable-dev-shm-usage']});
    for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}, {width: 320, height: 760}]) {
      const context = await browser.newContext({viewport, extraHTTPHeaders: {'x-visual-origin': origin}});
      const page = await context.newPage();
      const errors = [];
      const failedRequests = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('response', response => {
        if (response.status() >= 400 && response.url().startsWith(origin)) {
          const decoded = decodeURIComponent(response.url());
          if (!(response.status() === 401 && decoded.includes('/users/authenticated'))) {
            failedRequests.push(response.status() + ' ' + response.url());
          }
        }
      });
      for (const route of ['/', '/games', '/catalog', '/home']) {
        await context.clearCookies();
        if (route === '/home') await context.addCookies([{name: 'visual_preview', value: 'member', url: origin}]);
        const response = await page.goto(origin + route, {waitUntil: 'domcontentloaded', timeout: 90000});
        assert.equal(response.status(), 200);
        if (route === '/home') await page.getByText('Hello, PreviewBuilder!', {exact: true}).waitFor();
        else await page.locator('nav').getByRole('link', {name: 'Login', exact: true}).waitFor();
        if (route === '/' || route === '/games') await page.getByText('Classic Crossroads', {exact: true}).first().waitFor();
        if (route === '/catalog') await page.getByText('Classic Cap', {exact: true}).first().waitFor();
        await page.locator('nav').getByPlaceholder('Search', {exact: true}).fill('classic');
        await page.getByText('Search "classic" in Games', {exact: true}).waitFor();
        await page.locator('nav').getByPlaceholder('Search', {exact: true}).fill('');
        if (route === '/home' && viewport.width < 992) {
          const navigation = page.getByRole('button', {name: 'Open navigation', exact: true});
          await navigation.click();
          await page.getByRole('link', {name: 'Home', exact: true}).waitFor();
          assert.equal(await navigation.getAttribute('aria-expanded'), 'true');
          await navigation.click();
          await page.getByRole('link', {name: 'Home', exact: true}).waitFor({state: 'hidden'});
        }
        await page.waitForFunction(() => [...document.images].every(image => image.complete));
        await page.evaluate(() => document.fonts.ready);
        const layout = await page.evaluate(() => ({
          width: window.innerWidth, scrollWidth: document.documentElement.scrollWidth,
          visible: getComputedStyle(document.body).display !== 'none',
          brokenImages: [...document.images].filter(image => image.src && !image.naturalWidth).map(image => image.src),
          navOverflow: [...document.querySelectorAll('nav a, nav input')].filter(element => {
            const box = element.getBoundingClientRect();
            return box.width > 0 && (box.left < -1 || box.right > window.innerWidth + 1);
          }).map(element => element.textContent || element.getAttribute('placeholder')),
          fontLoaded: document.fonts.check('14px "HCo Gotham SSm"'),
        }));
        const slug = (route === '/' ? 'index' : route.slice(1)) + '-' + viewport.width;
        await page.screenshot({path: path.join(output, slug + '.png'), fullPage: true});
        results.push({route, viewport, ...layout});
        assert(layout.visible, 'Next left the document hidden');
        assert(layout.scrollWidth <= layout.width + 1, slug + ' causes page-wide horizontal overflow');
        assert.deepEqual(layout.navOverflow, [], slug + ' has clipped navigation');
        assert.deepEqual(layout.brokenImages, [], slug + ' has broken images');
        assert(layout.fontLoaded, 'The bundled Gotham font did not load');
        assert.deepEqual(errors, [], 'Browser JavaScript errors');
        assert.deepEqual(failedRequests, [], 'Unexpected local HTTP failures');
        console.log(mode + ': ' + slug + ' rendered, hydrated and passed layout/image/search checks');
      }
      // Check navigation to the real backend login without pretending the fixture
      // message is the Razor login page or submitting any real credentials.
      await context.clearCookies();
      await page.goto(origin + '/');
      assert.equal(await page.locator('nav').getByRole('link', {name: 'Login', exact: true}).getAttribute('href'), '/auth/accountlogin');
      const redirect = await context.request.get(origin + '/login', {maxRedirects: 0});
      assert.equal(redirect.status(), 307);
      assert.equal(redirect.headers().location, '/auth/accountlogin');
      await page.evaluate(() => localStorage.setItem('rbx_logo_style_v1', 'Roblox2016'));
      await page.reload({waitUntil: 'domcontentloaded'});
      await page.waitForFunction(() => [...document.querySelectorAll('nav a[aria-label="Marine home"]')]
        .filter(element => element.getBoundingClientRect().width > 0)
        .some(element => getComputedStyle(element).backgroundImage.includes('roblox2016')));
      assert.deepEqual(errors, [], 'Browser errors after changing logo preferences');
      assert.deepEqual(failedRequests, [], 'Unexpected local HTTP failures after changing logo preferences');
      await context.close();
    }
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify({mode, scope: 'Real browser, sample API fixtures only', results}, null, 2));
    console.log('Browser smoke passed; .NET integration and real game joins are separate tests.');
  } catch (error) {
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify({mode, results, error: error.message}, null, 2));
    fs.writeFileSync(path.join(output, 'next.log'), logs.join(''));
    if (process.env.GITHUB_ACTIONS === 'true') {
      const message = error.message.replaceAll('%', '%25').replaceAll('\r', '%0D').replaceAll('\n', '%0A');
      console.error('::error title=Frontend browser regression::' + message);
    }
    throw error;
  } finally {
    if (browser) await browser.close();
    await stop(server);
    await new Promise(resolve => fixture.close(resolve));
    fs.rmSync(temporary, {recursive: true, force: true});
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
