"""Bounded, isolated compatibility experiment for the bundled Windows RCC builds."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
BUILDS = {'2016': 'RCCService', '2017': 'RCCService/RCCService2017',
          '2018': 'RCCService/RCCService2018', '2020': 'RCCService/RCCService2020'}


def command(*args, timeout=15):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def annotation(level, title, message):
    print(title + ': ' + message)
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        text = message.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
        print(f'::{level} title={title}::{text}')


def run_build(year, folder, image):
    name = 'rcc-wine-test-' + uuid.uuid4().hex[:12]
    namespace = 'http://silrev.biz/' if year == '2020' else 'http://projex.zip/'
    result = {'year': year, 'namespace': namespace, 'soap': False, 'lua': None if year in ('2018', '2020') else False, 'stopped': False}
    try:
        launched = command(
            'docker', 'run', '--detach', '--name', name, '--network', 'none',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--read-only',
            '--memory', '4g', '--cpus', '2', '--pids-limit', '512',
            '--tmpfs', '/home/rcc:rw,nosuid,nodev,uid=1000,gid=1000,size=2g',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m',
            '--mount', f'type=bind,src={ROOT / folder},dst=/opt/rcc,readonly',
            '--env', 'RCC_BASE_URL=http://127.0.0.1:5000',
            '--env', 'RCC_SOAP_NAMESPACE=' + namespace, image,
            timeout=30,
        )
        if launched.returncode:
            raise RuntimeError(launched.stderr.strip())
        deadline = time.monotonic() + 150
        last_error = ''
        while time.monotonic() < deadline:
            state = command('docker', 'inspect', '--format', '{{.State.Running}} {{.State.ExitCode}} {{.State.OOMKilled}}', name)
            if not state.stdout.startswith('true '):
                raise RuntimeError('RCC container exited before SOAP was ready: ' + state.stdout.strip())
            health = command('docker', 'exec', name, 'python3', '/usr/local/bin/healthcheck.py')
            if health.returncode == 0:
                result.update(soap=True, version=health.stdout.strip())
                break
            last_error = health.stderr.strip()
            time.sleep(1)
        if not result['soap']:
            raise RuntimeError('No valid SOAP GetVersion reply within 150 seconds: ' + last_error)
        if year in ('2016', '2017'):
            lua = command('docker', 'exec', name, 'python3', '/usr/local/bin/lua_probe.py', timeout=70)
            if lua.returncode:
                raise RuntimeError('SOAP works, but Lua did not: ' + lua.stderr.strip())
            result['lua'] = True
        else:
            # These real RCC builds rejected raw Lua as invalid JSON. Do not
            # mistake a wrong job contract for a Wine failure or fake a Lua pass.
            result['jobContract'] = 'JSON dispatch; execution not tested'
        before = time.monotonic()
        stopped = command('docker', 'stop', '--time', '15', name, timeout=25)
        result['stopSeconds'] = round(time.monotonic() - before, 2)
        state = command('docker', 'inspect', '--format', '{{.State.Running}} {{.State.ExitCode}}', name)
        result['stopped'] = stopped.returncode == 0 and state.stdout.startswith('false ') and not state.stdout.strip().endswith(' 137')
        if not result['stopped']:
            raise RuntimeError('The container needed a forceful kill or did not stop: ' + state.stdout.strip())
        scope = 'Lua OpenJobEx/CloseJob passed' if result['lua'] else 'JSON job execution NOT tested'
        annotation('notice', f'RCC Wine {year}', f"SOAP {result['version']}, {scope}, signal shutdown passed. No place or client join was tested.")
    except Exception as error:
        result['error'] = str(error)
        logs = command('docker', 'logs', '--tail', '45', name)
        result['logs'] = (logs.stdout + logs.stderr)[-14000:]
        annotation('warning', f'RCC Wine {year} compatibility not established', result['error'] + '\n' + result['logs'])
    finally:
        command('docker', 'rm', '--force', name, timeout=25)
    return result


def main():
    image = os.environ.get('RCC_WINE_IMAGE', 'rcc-wine-test')
    selected = os.environ.get('RCC_WINE_YEARS', '2016,2017,2018,2020').split(',')
    if any(year not in BUILDS for year in selected):
        raise SystemExit('RCC_WINE_YEARS must select 2016,2017,2018,2020')
    results = [run_build(year, BUILDS[year], image) for year in selected]
    destination = os.environ.get('RCC_WINE_RESULTS')
    if destination:
        Path(destination).write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write('## Actual Windows RCC under Wine\n\n| Build | SOAP | Lua job | Shutdown |\n| --- | --- | --- | --- |\n')
            for result in results:
                summary.write('| ' + result['year'] + ' | ' + ' | '.join('not tested (JSON jobs)' if result[key] is None else 'passed' if result[key] else 'not verified' for key in ('soap', 'lua', 'stopped')) + ' |\n')
            summary.write('\nThese scoped smoke checks are not a real place launch or a client join. Failed builds remain unsupported.\n')
    return 0 if all(r['soap'] and r['lua'] is not False and r['stopped'] for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
