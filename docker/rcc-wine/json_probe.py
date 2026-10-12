"""Execute one controlled JSON-dispatched Lua job in the bundled RCC 2020."""

import json
from pathlib import Path
import uuid
import xml.etree.ElementTree as ET

from lua_probe import call


def probe(runtime=Path('/home/rcc/runtime')):
    # The mounted original RCC directory is never modified. Use a new script
    # basename so the test cannot overwrite an existing internal script.
    identifier = uuid.uuid4().hex
    script_type = 'WineJsonSmoke' + identifier[:8]
    source = Path(__file__).with_suffix('.lua').read_text()
    directory = Path(runtime) / 'internalscripts/scripts'
    directory.mkdir(parents=True, exist_ok=True)
    filename = directory / (script_type + '.lua')
    with filename.open('x') as stream:
        stream.write(source)
    token = 'rcc2020-json-' + identifier
    expected = token + ':42'
    job_id = str(uuid.uuid4())
    job = ET.Element('job')
    for name, value in {'id': job_id, 'category': 0, 'cores': 1, 'expirationInSeconds': 60}.items():
        ET.SubElement(job, name).text = str(value)
    script = ET.Element('script')
    ET.SubElement(script, 'name').text = 'WineJsonSmoke'
    ET.SubElement(script, 'script').text = json.dumps({
        'Mode': 'ExecuteScript',
        'Settings': {'Type': script_type, 'Arguments': [token, 19, 23]},
        'Arguments': {},
    })
    opened = False
    try:
        reply = call('OpenJobEx', [job, script])
        opened = True
        if not any(element.text == expected for element in reply.iter()):
            raise RuntimeError('JSON ExecuteScript did not return its unique Lua result')
    finally:
        try:
            if opened:
                close_id = ET.Element('jobID')
                close_id.text = job_id
                call('CloseJob', [close_id])
        finally:
            filename.unlink()
    print('JSON ExecuteScript returned the unique Lua result, used a DataModel and closed its job')


if __name__ == '__main__':
    probe()
