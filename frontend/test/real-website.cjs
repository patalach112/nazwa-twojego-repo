// End-to-end against the actual Docker website, Postgres and Redis. No fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {randomBytes} = require('node:crypto');
const {execFileSync} = require('node:child_process');
const {chromium, request} = require('playwright-core');
const root = path.resolve(__dirname, '../..');
const directory = path.join(root, 'docker/local');
const output = path.resolve(__dirname, '../.visual-results/integration');
const manifest = JSON.parse(fs.readFileSync(path.join(directory, 'manifest.json')));
const website = JSON.parse(fs.readFileSync(path.join(directory, 'website.json')));
assert(process.env.REAL_WEBSITE_TEST === '1' && manifest.test && manifest.database.endsWith('_tests'),
  'Use REAL_WEBSITE_TEST=1 and an explicitly generated disposable *_tests Docker database');
assert(website.Postgres.includes('Database=' + manifest.database + ';'), 'The website must use the disposable database');
const frontend = manifest.publicUrl;
const backendUrl = new URL(frontend);
assert(['localhost', '127.0.0.1', '[::1]'].includes(backendUrl.hostname), 'Only local test targets are permitted');
backendUrl.port = String(manifest.backendPort);
const backend = backendUrl.origin;
const accountPath = '/apisite/users/v1/users/authenticated';
const proxyUrl = pathname => '/api/proxy?url=' + encodeURIComponent(frontend + pathname);
const username = 'LocalTest' + randomBytes(5).toString('hex');
const password = 'real-local-test-' + randomBytes(16).toString('hex');
const errors = [];
const serverFailures = [];
const externalRequests = new Set();
const passed = [];
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

function observe(page) {
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (response.status() >= 500 || response.status() === 404) {
      serverFailures.push(response.status() + ' ' + new URL(response.url()).pathname);
    }
  });
  page.on('request', req => {
    const url = new URL(req.url());
    if (['http:', 'https:'].includes(url.protocol) && ![frontend, backend].includes(url.origin))
      externalRequests.add(url.origin);
  });
}
function checkBrowser() {
  assert.deepEqual(errors, [], 'Browser JavaScript errors against the real website');
  assert.deepEqual(serverFailures, [], 'Unexpected server/asset failures against the real website');
  assert.deepEqual([...externalRequests], [], 'Local account pages must not depend on external services');
}
async function snapshot(page, name) {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForFunction(() => [...document.images].every(image => image.complete));
  assert.deepEqual(await page.evaluate(() => [...document.images].filter(image => image.src && !image.naturalWidth).map(image => new URL(image.src).pathname)), []);
  assert.equal(await page.getByText(/sample data only|PreviewBuilder/).count(), 0);
  await page.screenshot({path: path.join(output, name + '.png'), fullPage: true});
  checkBrowser();
}
async function waitReady() {
  const api = await request.newContext();
  try {
    const deadline = Date.now() + 120000;
    while (Date.now() < deadline) {
      try {
        const probe = await api.get(backend + accountPath, {timeout: 3000, maxRedirects: 0});
        const page = await api.get(frontend + '/', {timeout: 5000});
        if (probe.status() === 401 && page.ok()) return;
      } catch (_) {}
      await delay(300);
    }
    throw new Error('The real frontend/backend did not become reachable within two minutes');
  } finally {await api.dispose();}
}

async function main() {
  fs.rmSync(output, {recursive: true, force: true});
  fs.mkdirSync(output, {recursive: true});
  await waitReady();
  const browser = await chromium.launch({headless: true,
    executablePath: process.env.BROWSER_EXECUTABLE_PATH || undefined,
    args: ['--no-sandbox', '--disable-dev-shm-usage']});
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
    const page = await context.newPage();
    observe(page);
    for (const [label, origin] of [['frontend', frontend], ['backend', backend]]) {
      const response = await page.goto(origin + '/', {waitUntil: 'domcontentloaded'});
      assert.equal(response.status(), 200, label + ' root is not reachable');
      await page.getByRole('heading', {name: 'Welcome to Marine', exact: true}).waitFor();
      await page.locator('nav').getByRole('link', {name: 'Login', exact: true}).waitFor();
      await snapshot(page, label + '-anonymous');
    }
    passed.push('Anonymous real frontend and backend roots on both ports');
    await page.goto(frontend + '/auth/signup', {waitUntil: 'domcontentloaded'});
    await page.getByPlaceholder('Username', {exact: true}).waitFor();
    await snapshot(page, 'signup');
    // Missing antiforgery must remain rejected despite removal of global CSRF.
    const invalidForm = await context.request.post(frontend + '/auth/signup', {
      form: {username, password}, maxRedirects: 0,
    });
    assert.equal(invalidForm.status(), 400);
    await page.getByPlaceholder('Username', {exact: true}).fill(username);
    await page.getByPlaceholder('Password', {exact: true}).fill(password);
    await page.getByRole('button', {name: 'Sign Up', exact: true}).click();
    await page.getByText('Hello, ' + username + '!', {exact: true}).waitFor({timeout: 30000});
    const sessionCookie = (await context.cookies()).find(cookie => cookie.name === '.PEKOSECURITY');
    assert(sessionCookie && sessionCookie.httpOnly && !sessionCookie.secure);
    const account = await context.request.get(frontend + proxyUrl(accountPath));
    assert.equal(account.status(), 200);
    const user = await account.json();
    assert.equal(user.name, username);
    assert(user.id > 0);
    passed.push('Real Razor registration, antiforgery and HttpOnly HTTP session');
    for (const [label, origin] of [['frontend', frontend], ['backend', backend]]) {
      for (const width of [1440, 390]) {
        await page.setViewportSize({width, height: width === 1440 ? 1000 : 844});
        await page.goto(origin + '/home', {waitUntil: 'domcontentloaded'});
        await page.getByText('Hello, ' + username + '!', {exact: true}).waitFor();
        await snapshot(page, label + '-home-' + width);
      }
    }
    await page.setViewportSize({width: 1440, height: 1000});
    for (const route of ['/games', '/catalog']) {
      await page.goto(frontend + route, {waitUntil: 'domcontentloaded'});
      await page.locator('nav').getByRole('link', {name: username, exact: true}).waitFor();
      await snapshot(page, route.slice(1));
    }
    passed.push('Real member dashboard on both ports and widths, games and catalog');
    const staff = await context.request.get(frontend + proxyUrl('/admin-api/api/applications/pending-num'), {maxRedirects: 0});
    assert([401, 403].includes(staff.status()), 'A registered ordinary account must not become staff');
    passed.push('Ordinary account retains staff authorization boundaries');
    const logout = await context.request.post(frontend + proxyUrl('/apisite/auth/v2/logout'));
    assert([200, 204].includes(logout.status()));
    assert.equal((await context.request.get(frontend + proxyUrl(accountPath), {maxRedirects: 0})).status(), 401);
    const replay = await browser.newContext();
    await replay.addCookies([sessionCookie]);
    assert.equal((await replay.request.get(frontend + proxyUrl(accountPath), {maxRedirects: 0})).status(), 401);
    await replay.close();
    passed.push('Real logout revokes the session, including cookie replay');
    await page.setViewportSize({width: 1440, height: 1000});
    await page.goto(frontend + '/auth/accountlogin', {waitUntil: 'domcontentloaded'});
    await page.getByPlaceholder('Username', {exact: true}).fill(username);
    await page.getByPlaceholder('Password', {exact: true}).fill('wrong-real-test-password');
    await page.getByRole('button', {name: 'Login', exact: true}).click();
    await page.getByText('Incorrect username or password. Please try again', {exact: true}).waitFor();
    assert.equal((await context.request.get(frontend + proxyUrl(accountPath), {maxRedirects: 0})).status(), 401);
    await snapshot(page, 'login');
    // Respect the real five-second per-IP login cooldown; do not disable it.
    await delay(5200);
    await page.getByPlaceholder('Password', {exact: true}).fill(password);
    await page.getByRole('button', {name: 'Login', exact: true}).click();
    await page.getByText('Hello, ' + username + '!', {exact: true}).waitFor();
    assert.equal((await (await context.request.get(frontend + proxyUrl(accountPath))).json()).id, user.id);
    passed.push('Incorrect password rejected and correct password authenticates the same account');
    const before = fs.readFileSync(path.join(directory, 'website.json'));
    execFileSync('bash', ['scripts/local-stack.sh', 'restart', 'website', 'frontend'], {cwd: root, stdio: 'inherit', timeout: 90000});
    await waitReady();
    assert.deepEqual(fs.readFileSync(path.join(directory, 'website.json')), before);
    await page.goto(frontend + '/home', {waitUntil: 'domcontentloaded'});
    await page.getByText('Hello, ' + username + '!', {exact: true}).waitFor();
    assert.equal((await (await context.request.get(frontend + proxyUrl(accountPath))).json()).id, user.id);
    await snapshot(page, 'home-after-restart');
    passed.push('Account and login cookie survive frontend/backend restart without key rotation');
    await context.close();
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify({scope:'Real local Docker website, PostgreSQL and Redis; no game join', passed}, null, 2));
    for (const result of passed) console.log('Real website: ' + result);
    console.log('Real frontend/backend account integration passed; RCC games and rendering remain separate tests.');
  } catch (error) {
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify({passed, error:error.message, errors, serverFailures, externalRequests:[...externalRequests]}, null, 2));
    if (process.env.GITHUB_ACTIONS === 'true') console.error('::error title=Real website regression::' + error.message.replaceAll('%','%25').replaceAll('\r','%0D').replaceAll('\n','%0A'));
    throw error;
  } finally {await browser.close();}
}
main().catch(error => {console.error(error); process.exitCode=1;});
