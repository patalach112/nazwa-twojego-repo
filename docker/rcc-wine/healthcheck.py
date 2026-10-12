"""Check an actual SOAP reply, not merely an open TCP port or a Wine process."""

import os
import sys
import urllib.request
import xml.etree.ElementTree as ET

SOAP = 'http://schemas.xmlsoap.org/soap/envelope/'


def get_version(port, namespace='http://silrev.biz/'):
    envelope = ET.Element(f'{{{SOAP}}}Envelope')
    body = ET.SubElement(envelope, f'{{{SOAP}}}Body')
    ET.SubElement(body, f'{{{namespace}}}GetVersion')
    request = urllib.request.Request(
        f'http://127.0.0.1:{port}/',
        data=ET.tostring(envelope, encoding='utf-8', xml_declaration=True),
        headers={'Content-Type': 'text/xml; charset=utf-8', 'SOAPAction': namespace + 'GetVersion'},
        method='POST',
    )
    # Ignore proxy variables: SOAP belongs to this container, not an HTTP proxy.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=3) as response:
        root = ET.fromstring(response.read())
    if root.find(f'.//{{{SOAP}}}Fault') is not None:
        raise RuntimeError('RCC returned a SOAP fault')
    result = next((e for e in root.iter() if e.tag.rsplit('}', 1)[-1] == 'GetVersionResult'), None)
    if result is None or not result.text or not result.text.strip():
        raise RuntimeError('RCC did not return a nonempty GetVersionResult')
    return result.text.strip()


if __name__ == '__main__':
    try:
        print(get_version(int(os.environ.get('RCC_PORT', '64989')), os.environ.get('RCC_SOAP_NAMESPACE', 'http://silrev.biz/')))
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
