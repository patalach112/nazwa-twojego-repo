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
    result = {'year': year, 'namespace': namespace, 'soap': False, 'lua': None if year in ('2018', '2020') else False, 'jsonJob': False if year == '2020' else None, 'thumbnail': None, 'stopped': False}
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
        elif year == '2020':
            execution = command('docker', 'exec', name, 'python3', '/usr/local/bin/json_probe.py', timeout=70)
            if execution.returncode:
                raise RuntimeError('SOAP works, but the JSON job did not: ' + execution.stderr.strip())
            result['jsonJob'] = True
            result['jobContract'] = 'JSON ExecuteScript with a controlled local Lua script'
            # The render probe is what separates a working JSON dispatch from a
            # working renderer: it must return two different decodable images.
            thumbnail = command('docker', 'exec', name, 'python3', '/usr/local/bin/thumbnail_probe.py',
                                timeout=int(os.environ.get('RCC_WINE_THUMBNAIL_TIMEOUT', '220')))
            if thumbnail.returncode:
                detail = (thumbnail.stderr.strip() or thumbnail.stdout.strip())[-700:]
                raise RuntimeError('JSON execution works, but no thumbnail render was established: ' + detail)
            result['thumbnail'] = json.loads(thumbnail.stdout.strip().splitlines()[-1])
            annotation('notice', 'RCC Wine 2020 thumbnail',
                       'two different DataModels rendered as {width}x{height} {format} through {mode}. No place or client join was tested.'.format(**result['thumbnail']))
        else:
            result['jobContract'] = 'JSON dispatch; execution not tested'
        before = time.monotonic()
        stopped = command('docker', 'stop', '--time', '15', name, timeout=25)
        result['stopSeconds'] = round(time.monotonic() - before, 2)
        state = command('docker', 'inspect', '--format', '{{.State.Running}} {{.State.ExitCode}}', name)
        result['stopped'] = stopped.returncode == 0 and state.stdout.startswith('false ') and not state.stdout.strip().endswith(' 137')
        if not result['stopped']:
            raise RuntimeError('The container needed a forceful kill or did not stop: ' + state.stdout.strip())
        scope = ('Lua OpenJobEx/CloseJob passed' if result['lua'] else
                 'JSON ExecuteScript/CloseJob passed' if result['jsonJob'] else 'JSON job execution NOT tested')
        if result['thumbnail']:
            scope += ', thumbnail render passed'
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
            summary.write('## Actual Windows RCC under Wine\n\n| Build | SOAP | Execution job | Thumbnail render | Shutdown |\n| --- | --- | --- | --- | --- |\n')
            for result in results:
                thumbnail = ('passed' if result['thumbnail'] else 'not verified') if result['year'] == '2020' else 'not applicable'
                cell = lambda value: 'not tested' if value is None else 'passed' if value else 'not verified'
                cells = (cell(result['soap']), cell(result['jsonJob'] if result['year'] == '2020' else result['lua']),
                         thumbnail, cell(result['stopped']))
                summary.write('| ' + result['year'] + ' | ' + ' | '.join(cells) + ' |\n')
            summary.write('\nThese scoped smoke checks are not a real place launch or a client join. Failed builds remain unsupported.\n')
    required = (lambda r: bool(r['soap']) and r['lua'] is not False and r['jsonJob'] is not False and bool(r['stopped'])
                and (bool(r['thumbnail']) if r['year'] == '2020' else True))
    return 0 if all(required(r) for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
