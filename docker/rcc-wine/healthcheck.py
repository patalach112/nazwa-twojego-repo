"""Check an actual SOAP reply, not merely an open TCP port or a Wine process."""

import os
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

SOAP = 'http://schemas.xmlsoap.org/soap/envelope/'


def get_version(port, namespace='http://projex.zip/'):
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
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=3)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read()
        root = ET.fromstring(raw)
    fault = root.find(f'.//{{{SOAP}}}Fault')
    if fault is not None:
        raise RuntimeError('RCC SOAP fault: ' + ' '.join(fault.itertext()))
    result = next((e for e in root.iter() if e.tag.rsplit('}', 1)[-1] == 'GetVersionResult'), None)
    if result is None or not result.text or not result.text.strip():
        raise RuntimeError('RCC did not return a nonempty GetVersionResult')
    return result.text.strip()


if __name__ == '__main__':
    try:
        print(get_version(int(os.environ.get('RCC_PORT', '64989')), os.environ.get('RCC_SOAP_NAMESPACE', 'http://projex.zip/')))
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
