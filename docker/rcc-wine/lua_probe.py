"""Exercise the Lua VM after the SOAP-only health check; this is not a game join."""

import os
import urllib.request
import urllib.error
import uuid
import xml.etree.ElementTree as ET

SOAP = 'http://schemas.xmlsoap.org/soap/envelope/'
NAMESPACE = os.environ.get('RCC_SOAP_NAMESPACE', 'http://projex.zip/')


def call(method, children):
    envelope = ET.Element(f'{{{SOAP}}}Envelope')
    body = ET.SubElement(envelope, f'{{{SOAP}}}Body')
    operation = ET.SubElement(body, f'{{{NAMESPACE}}}{method}')
    for child in children:
        # The original SOAP contract uses a default namespace for both the
        # operation and its argument elements, not just for the method name.
        for element in child.iter():
            element.tag = f'{{{NAMESPACE}}}{element.tag}'
        operation.append(child)
    request = urllib.request.Request(
        f"http://127.0.0.1:{int(os.environ.get('RCC_PORT', '64989'))}/",
        data=ET.tostring(envelope, encoding='utf-8', xml_declaration=True),
        headers={'Content-Type': 'text/xml; charset=utf-8', 'SOAPAction': NAMESPACE + method},
        method='POST',
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        root = ET.fromstring(response.read())
    fault = root.find(f'.//{{{SOAP}}}Fault')
    if fault is not None:
        raise RuntimeError('RCC SOAP fault: ' + ''.join(fault.itertext()))
    return root


def probe():
    job_id = str(uuid.uuid4())
    job = ET.Element('job')
    for key, value in {'id': job_id, 'category': 0, 'cores': 1, 'expirationInSeconds': 60}.items():
        ET.SubElement(job, key).text = str(value)
    script = ET.Element('script')
    ET.SubElement(script, 'name').text = 'WineSmoke'
    ET.SubElement(script, 'script').text = 'return "rcc-wine-smoke"'
    opened = False
    try:
        reply = call('OpenJobEx', [job, script])
        opened = True
        if not any(element.text == 'rcc-wine-smoke' for element in reply.iter()):
            raise RuntimeError('OpenJobEx did not return the Lua smoke token')
    finally:
        if opened:
            identifier = ET.Element('jobID')
            identifier.text = job_id
            call('CloseJob', [identifier])
    print('OpenJobEx returned the Lua smoke token and CloseJob completed')


if __name__ == '__main__':
    probe()
