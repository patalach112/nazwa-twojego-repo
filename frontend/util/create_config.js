const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const configPath = path.join(__dirname, '../config.json');

const main = () => {
  if (fs.existsSync(configPath)) {
    console.error('config.json already exists at this path:', configPath, '\n\nIf you want to create a fresh config, manually delete or move this file first, then run this script again.');
    return
  }
  let baseUrl = 'http://localhost:5000';
  const backend = { csrfKey: crypto.randomBytes(64).toString('base64') };
  if (process.env.WEBSITE_CONFIG_PATH) {
    const website = JSON.parse(fs.readFileSync(process.env.WEBSITE_CONFIG_PATH, 'utf8'));
    const setting = name => Object.entries(website).find(([key]) => key.toLowerCase() === name.toLowerCase())?.[1];
    baseUrl = String(setting('BaseUrl')).replace(/\/+$/, '');
    const parsed = new URL(baseUrl);
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password)
      throw new Error('The website BaseUrl must be an HTTP(S) URL without embedded credentials');
    const authorization = setting('Authorization');
    if (typeof authorization !== 'string' || !authorization)
      throw new Error('The website needs a nonempty Authorization before frontend config can be generated');
    // Next exposes only publicRuntimeConfig to browsers. Keep the credential here.
    backend.authorization = authorization;
  }
  fs.writeFileSync(configPath, JSON.stringify({
    "serverRuntimeConfig": {
      backend
    },
    "publicRuntimeConfig": {
      "backend": {
        proxyEnabled: true,
        flags: {
          myAccountPage2016Enabled: true,
          catalogGenreFilterSupported: false,
          catalogPageLimit: 28,
          catalogSaleCountVisibleFromDetailsEndpoint: false,
          catalogDetailsPageResellerLimit: 10,
          avatarPageInventoryLimit: 10,
          friendsPageLimit: 25,
          settingsPageThemeSelectorEnabled: true,
          tradeWindowInventoryCollectibleLimit: 10,
          moneyPagePromotionTabVisible: false,
          gameGenreFilterMethod: 'keyword',
          gameGenreFilterSupported: true,
          avatarPageOutfitCreatedAtAvailable: false,
          catalogDetailsPageOwnersTabEnabled: false,
        },
        // points at the local .NET website by default. the proxy route only forwards
        // to urls on the same domain as baseUrl, so keep these two in sync when you
        // put this on a real domain (e.g. https://pekora.zip + https://pekora.zip/apisite/{0}{1})
        "baseUrl": baseUrl,
        "apiFormat": baseUrl + "/apisite/{0}{1}"
      }
    },
  }));
  console.log('config.json created at this path:', configPath);
  return 0
}

main();