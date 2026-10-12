"""Contract and cleanup tests only; actual execution requires the Wine CI job."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

PROBES = Path(__file__).resolve().parents[1] / 'docker/rcc-wine'
sys.path.insert(0, str(PROBES))
import json_probe


class JsonProbeTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='json probe test ')
        self.addCleanup(self.directory.cleanup)
        self.runtime = Path(self.directory.name)

    def scripts(self):
        return list(self.runtime.rglob('*.lua'))

    def test_json_contract_delivers_typed_arguments_and_closes_the_same_job(self):
        import json
        opened_id = None
        def rpc(method, children):
            nonlocal opened_id
            if method == 'OpenJobEx':
                opened_id = children[0].findtext('id')
                payload = json.loads(children[1].findtext('script'))
                self.assertEqual(payload['Mode'], 'ExecuteScript')
                self.assertEqual(payload['Settings']['Arguments'][1:], [19, 23])
                self.assertEqual(payload['Arguments'], {})
                script = self.runtime / 'internalscripts/scripts' / (payload['Settings']['Type'] + '.lua')
                self.assertIn('Instance.new("Part")', script.read_text())
                reply = ET.Element('OpenJobExResponse')
                ET.SubElement(reply, 'value').text = payload['Settings']['Arguments'][0] + ':42'
                return reply
            self.assertEqual(method, 'CloseJob')
            self.assertEqual(children[0].text, opened_id)
            return ET.Element('CloseJobResponse')
        with patch.object(json_probe, 'call', side_effect=rpc) as calls:
            json_probe.probe(self.runtime)
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(self.scripts(), [])

    def test_missing_lua_result_is_not_a_false_execution_pass(self):
        with patch.object(json_probe, 'call', return_value=ET.Element('reply')) as calls:
            with self.assertRaisesRegex(RuntimeError, 'unique Lua result'):
                json_probe.probe(self.runtime)
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(self.scripts(), [])

    def test_a_rejected_open_removes_the_temporary_script(self):
        with patch.object(json_probe, 'call', side_effect=RuntimeError('SOAP rejected')) as calls:
            with self.assertRaisesRegex(RuntimeError, 'SOAP rejected'):
                json_probe.probe(self.runtime)
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(self.scripts(), [])

    def test_a_failed_close_is_not_reported_as_a_pass(self):
        import json
        def rpc(method, children):
            if method == 'CloseJob':
                raise RuntimeError('close failed')
            payload = json.loads(children[1].findtext('script'))
            reply = ET.Element('reply')
            ET.SubElement(reply, 'value').text = payload['Settings']['Arguments'][0] + ':42'
            return reply
        with patch.object(json_probe, 'call', side_effect=rpc):
            with self.assertRaisesRegex(RuntimeError, 'close failed'):
                json_probe.probe(self.runtime)
        self.assertEqual(self.scripts(), [])


if __name__ == '__main__':
    unittest.main()
