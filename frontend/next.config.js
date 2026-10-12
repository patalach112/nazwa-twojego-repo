const fs = require('fs');
const path = require('path');
// Browser tests supply an isolated temporary config without touching local secrets.
const configPath = process.env.FRONTEND_CONFIG_PATH
  ? path.resolve(__dirname, process.env.FRONTEND_CONFIG_PATH)
  : path.join(__dirname, 'config.json');
if (!fs.existsSync(configPath)) {
  throw new Error('Configuration could not be found at location: ' + configPath);
}
const config = JSON.parse(fs.readFileSync(configPath).toString('utf-8'));

module.exports = {
  reactStrictMode: true,
  serverRuntimeConfig: config.serverRuntimeConfig,
  publicRuntimeConfig: config.publicRuntimeConfig,
  // Direct frontend previews still use relative URLs for the backend Razor forms.
  async rewrites() {
    return [{
      source: '/auth/:path*',
      destination: config.publicRuntimeConfig.backend.baseUrl.replace(/\/+$/, '') + '/auth/:path*',
    }];
  },
  async redirects() {
    return [
      // The old Next login imports an external cookie or calls a disabled API.
      // Use the existing backend password/2FA form instead.
      {source: '/login', destination: '/auth/accountlogin', permanent: false},
      {
        source: '/catalog.aspx',
        destination: '/catalog',
        permanent: true,
      },
      /*
      {
        source: '/catalog/:id/:name',
        destination: '/redirect-item?id=:id',
        permanent: false,
      },
       */
      {
        source: '/groups/:id/:name',
        destination: '/My/Groups.aspx?gid=:id',
        permanent: false,
      },
    ]
  },
}
