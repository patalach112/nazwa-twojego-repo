import { useState } from "react";
import { logger } from "../../../lib/logger";
import { getUrlWithProxy } from "../../../lib/request";
import { getBaseUrl } from "../../../lib/request";

// needs to be COMPLETELY redone once shika finishes api for feed

const css = `
a.list-header {
  display: inline-block;
}
img.header-thumb, img.avatar-card-image {
  height: 85px;
  width: auto;
  display: inline-block;
}
ul.vlist.feeds {
  padding: 0;
  list-style: none;
}
div.list-body {
  display: inline-block;
  margin-left: 10px;
  max-width: calc(100% - 100px);
}
html,body {
  font-family: HCo Gotham SSm,Helvetica Neue,Helvetica,Arial,Lucida Grande,sans-serif;
}
p {
  padding: 0;
  margin: 0;
}
p.feedtext {
  padding: 10px 0;
}
span.xsmall {
  color: #777;
  font-size: 12px;
}
a {
  color: var(--primary-color);
  text-decoration: none;
}
a:visited {
  color: var(--primary-color);
}
li.list-item {
  padding-bottom: 20px;
  border-bottom: 1px solid var(--text-color-quinary);
  padding-top: 10px;
}
`;

const MyFeed = props => {
  const [height, setHeight] = useState('auto');
  // TODO: would a flag be better? I guess automatically guessing is more convenient...
  // Hostnames alone are not origins: local frontend/backend ports differ too.
  const shouldProxyRequest = new URL(getBaseUrl(), window.location.href).origin !== window.location.origin;
  const feedFrameUrl = shouldProxyRequest ? (getUrlWithProxy(getBaseUrl() + '/Feeds/GetUserFeed')) : getBaseUrl() + '/Feeds/GetUserFeed';

  return <div>
    <iframe id='homepage-iframe-feed' height='100%' scrolling='no' style={{ width: '100%', height: height, overflow: 'hidden' }} src={feedFrameUrl} onLoad={() => {
      logger.info('feed', 'feed iframe loaded');
      // @ts-ignore
      const current = document.getElementById('homepage-iframe-feed');
      // @ts-ignore
      const doc = current.contentWindow.document;
      const body = doc.body;
      const font = doc.createElement('link');
      font.setAttribute('href', '/fonts/source-sans-pro.css');
      font.setAttribute('rel', 'stylesheet');
      doc.body.appendChild(font);
      const styles = doc.createElement('style');
      styles.innerText = css;
      // doc.appendElement(styles)
      doc.body.appendChild(styles)
      const height = body.scrollHeight + 10;
      setHeight(height + 'px');
    }}></iframe>
  </div>
}

export default MyFeed;