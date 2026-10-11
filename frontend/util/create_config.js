const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const configPath = path.join(__dirname, '../config.json');

const main = () => {
  if (fs.existsSync(configPath)) {
    console.error('config.json already exists at this path:', configPath, '\n\nIf you want to create a fresh config, manually delete or move this file first, then run this script again.');
    return
  }
  fs.writeFileSync(configPath, JSON.stringify({
    "serverRuntimeConfig": {
      backend: {
        csrfKey: crypto.randomBytes(64).toString('base64'),
      }
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
        "baseUrl": "http://localhost:5000",
        "apiFormat": "http://localhost:5000/apisite/{0}{1}"
      }
    },
  }));
  console.log('config.json created at this path:', configPath);
  return 0
}

main();