const {test} = require('node:test');
const assert = require('node:assert/strict');
const {isSafe, targetUrl, backendOrigin} = require('../lib/backendProxy.cjs');
const config = {publicRuntimeConfig: {backend: {baseUrl:'http://localhost:3000'}},
  serverRuntimeConfig: {backend: {baseUrl:'http://website:5000'}}};
test('public browser URLs are mapped to the internal Docker backend', () => {
  assert.equal(targetUrl('http://localhost:3000/apisite/users/v1/users/authenticated?x=1', config),
    'http://website:5000/apisite/users/v1/users/authenticated?x=1');
  assert.equal(backendOrigin(config), 'http://website:5000');
});
test('legacy configs without a private origin keep their configured backend', () => {
  assert.equal(targetUrl('http://localhost:3000/auth/signup', {publicRuntimeConfig:config.publicRuntimeConfig}),
    'http://localhost:3000/auth/signup');
});
test('URL validation includes scheme, host and port rather than just a parent domain', () => {
  for (const value of ['http://localhost:5000/apisite/users', 'https://localhost:3000/apisite/users',
    'http://website:5000/apisite/users', 'http://user:pass@localhost:3000/apisite/users',
    'http://localhost:3000/api/proxy', 'http://localhost:3000/foo#fragment', 'not a URL', ['http://localhost:3000/foo']]) {
    assert.equal(isSafe(value, config.publicRuntimeConfig.backend.baseUrl), false);
  }
  assert.equal(isSafe('HTTP://LOCALHOST:3000/apisite/users', config.publicRuntimeConfig.backend.baseUrl), true);
  assert.equal(isSafe('http://service:5000/apisite/users', 'http://service:5000'), true);
  assert.equal(isSafe('https://api.example.test/foo', 'https://www.example.test'), false);
});
test('invalid public targets cannot be remapped into private services', () => {
  assert.throws(() => targetUrl('http://attacker.test/apisite/users', config), /Invalid proxy URL/);
});
