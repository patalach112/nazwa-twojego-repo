import axios from 'axios';
import getConfig from 'next/config';
import {isSafe, targetUrl} from '../../lib/backendProxy.cjs';

const UrlUtilities = {
  isSafe: raw => isSafe(raw, getConfig().publicRuntimeConfig.backend.baseUrl),
};
const hopHeaders = new Set(['host', 'connection', 'keep-alive', 'proxy-authenticate',
  'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade', 'content-length', 'accept-encoding']);

const actualHandler = async (req, res) => {
  const fullUrl = req.query.url;
  // Right now we just validate the URL. 
  // A safer approach might be to just send the parts of the URL (query params, path, api site) to this handler, then construct the correct URL here.
  const isUrlSafe = UrlUtilities.isSafe(fullUrl);// typeof fullUrl === 'string' && fullUrl.toLowerCase().startsWith(getBaseUrl())

  if (getConfig().publicRuntimeConfig.backend.proxyEnabled !== true || !isUrlSafe) {
    return res.status(400).json({
      success: false,
    });
  }
  try {
    let requestHeaders = {
      cookie: req.headers['cookie'] || '',
      'x-csrf-token': req.headers['x-csrf-token'] || '',
      'user-agent': req.headers['user-agent'],
    }
    for (const key in req.headers) {
      if (!hopHeaders.has(key.toLowerCase()) && key.toLowerCase() !== 'authorization') {
        requestHeaders[key] = req.headers[key];
      }
    }
    const authHeaderValue = getConfig().serverRuntimeConfig.backend.authorization;
    if (typeof authHeaderValue === 'string')
      requestHeaders[getConfig().serverRuntimeConfig.backend.authorizationHeader || 'authorization'] = authHeaderValue;
    const result = await axios.request({
      method: req.method,
      url: targetUrl(fullUrl, getConfig()),
      data: req.body,
      maxRedirects: 0,
      headers: requestHeaders,
      validateStatus: () => true,
    });
    for (const item of Object.getOwnPropertyNames(result.headers)) {
      if (!hopHeaders.has(item.toLowerCase())) res.setHeader(item, result.headers[item]);
    }
    res.status(result.status);
    res.send(result.data);
    res.end();
  } catch (e) {
    // Axios errors include request cookies and authorization; never log them.
    console.error('Backend proxy request failed:', e.code || 'unknown');
    res.status(502).json({
      success: false
    })
    res.end();
  }
}

export default function handler(req, res) {
  return new Promise((resolve, reject) => {
    let chunks = []
    req.on('data', function (chunk) {
      chunks.push(chunk);
    })
    req.on('end', function () {
      req.body = Buffer.concat(chunks);
      actualHandler(req, res).then(() => resolve()).catch(e => reject(e));
    })
  })

}

export const config = {
  api: {
    bodyParser: false,
  },
}

export {
  UrlUtilities,
}