// Keep the browser-facing origin separate from an internal Docker service URL.
function origin(raw) {
  const value = new URL(raw);
  if (!['http:', 'https:'].includes(value.protocol) || value.username || value.password
      || value.pathname !== '/' || value.search || value.hash) {
    throw new Error('Backend base URL must be an HTTP(S) origin without credentials or a path');
  }
  return value.origin;
}
function isSafe(raw, publicBaseUrl) {
  if (typeof raw !== 'string') return false;
  try {
    const value = new URL(raw);
    return !value.username && !value.password && !value.hash
      && value.origin === origin(publicBaseUrl) && value.pathname !== '/api/proxy';
  } catch (_) { return false; }
}
function backendOrigin(config) {
  return origin(config.serverRuntimeConfig?.backend?.baseUrl || config.publicRuntimeConfig.backend.baseUrl);
}
function targetUrl(raw, config) {
  if (!isSafe(raw, config.publicRuntimeConfig.backend.baseUrl)) throw new Error('Invalid proxy URL');
  const value = new URL(raw);
  return backendOrigin(config) + value.pathname + value.search;
}
module.exports = {origin, isSafe, backendOrigin, targetUrl};
