// Fixtures are only loaded by the opt-in browser test. They are not an API backend.
const http = require('node:http');

const games = ['Classic Crossroads', 'Brickbattle Arena', 'Build a Town', 'Rocket Park',
  'The Old Obby', 'Island Adventure', 'Sword Fight', 'Happy Home', 'Train Station',
  'Space Base', 'Retro Racing', 'Castle Explorer'].map((name, i) => ({
  placeId: 100 + i, universeId: 200 + i, name, creatorId: 42,
  creatorName: 'PreviewBuilder', creatorType: 'User', playerCount: 8 + i,
  totalUpVotes: 100 + i, totalDownVotes: 3, year: 2016,
}));
const items = ['Classic Cap', 'Builders Helmet', 'Blue Beanie', 'Retro Visor',
  'Explorer Hat', 'Red Scarf', 'Brick Crown', 'Space Helmet'].map((name, i) => ({
  id: 300 + i, itemType: 'Asset', assetType: 8, name, description: 'Visual test item',
  creatorType: 'User', creatorTargetId: 42, creatorName: 'PreviewBuilder',
  price: 25 + i * 10, priceTickets: null, lowestPrice: null, isForSale: true,
  itemRestrictions: [], createdAt: '2020-01-01T00:00:00Z', saleCount: 0,
}));

function createFixtureServer() {
  return http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://fixture.invalid');
    const pathname = url.pathname;
    const member = (req.headers.cookie || '').includes('visual_preview=member');
    // Only sample thumbnails point back to the frontend origin. Real browser API
    // requests use relative /api/proxy URLs, not the fixture server's loopback port.
    const origin = req.headers['x-visual-origin'] || 'http://fixture.invalid';
    const imageUrl = origin + '/img/placeholder/icon_one.png';
    const json = (data, status = 200) => {
      res.writeHead(status, {'Content-Type': 'application/json'});
      res.end(JSON.stringify(data));
    };
    const html = body => {
      res.writeHead(200, {'Content-Type': 'text/html; charset=utf-8'});
      res.end('<!doctype html><html><body>' + body + '</body></html>');
    };
    if (pathname.endsWith('/users/authenticated')) {
      return member ? json({id: 42, name: 'PreviewBuilder', isStaff: false})
        : json({errors: [{code: 0, message: 'Not authenticated'}]}, 401);
    }
    if (pathname.endsWith('/alert-info')) return json({IsVisible: true,
      Text: 'Visual test — sample data only, not a real account or game server.'});
    if (pathname.endsWith('/chat-settings')) return json({chatEnabled: !member, isActiveChatUser: false});
    if (pathname.endsWith('/count')) return json({count: 0});
    if (pathname.endsWith('/currency')) return json({robux: 125, tickets: 320});
    if (pathname.endsWith('/friends')) return json({data: []});
    if (pathname.endsWith('/games/sorts')) return json({sorts: [
      {token: 'Popular', name: 'popular', displayName: 'Popular'},
      {token: 'TopRated', name: 'top-rated', displayName: 'Top Rated'},
    ]});
    if (pathname.endsWith('/games/list')) return json({games: games.slice(0, Number(url.searchParams.get('maxRows')) || 12)});
    if (pathname.endsWith('/search/items')) return json({data: items.map(({id}) => ({id})),
      nextPageCursor: null, previousPageCursor: null, _total: items.length});
    if (pathname.endsWith('/catalog/items/details')) return json({data: items});
    if (pathname.includes('/thumbnails/')) {
      const ids = (url.searchParams.get('userIds') || url.searchParams.get('universeIds')
        || url.searchParams.get('assetIds') || '').split(',').filter(Boolean).map(Number);
      return json({data: ids.map(targetId => ({targetId, state: 'Completed', imageUrl}))});
    }
    if (pathname.toLowerCase().startsWith('/feeds/')) return html('<p>No recent updates in this sample account.</p>');
    if (pathname.startsWith('/user-sponsorship/')) return html('<a href="/games" title="Sample advertisement"><img src="/img/058f09be8a19b8607d6fe50baa6659a1-AdBannerTemplate.png" alt="Sample advertisement"></a>');
    if (pathname === '/auth/accountlogin') return html('<h1>Backend login</h1><p>The visual test does not implement login. The real Razor login is covered by the separate website startup test.</p>');
    json({errors: [{code: 0, message: 'No visual fixture for ' + req.method + ' ' + pathname}]}, 404);
  });
}

module.exports = {createFixtureServer};
