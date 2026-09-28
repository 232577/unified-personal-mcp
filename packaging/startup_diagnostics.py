"""Standard-library startup checks, without reading settings or connecting a tunnel."""

import argparse
import hashlib
import json
import os
import platform
import re
import struct
import subprocess
import sys
import time
from pathlib import Path


def check_package(bundle):
    try:
        manifest_path = bundle / 'manifest-sha256.json'
        if manifest_path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('manifest too large')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if not isinstance(manifest, dict) or not 1 <= len(manifest) <= 50000:
            raise ValueError('invalid manifest')
        missing, changed, names = [], [], set()
        for relative, expected in manifest.items():
            if (not isinstance(relative, str) or not relative or ':' in relative or '\\' in relative
                    or relative.startswith('/') or any(p in {'', '.', '..'} for p in relative.split('/'))
                    or relative.casefold() in names or relative.casefold() == 'manifest-sha256.json'):
                return {'ok': False, 'code': 'PACKAGE_MANIFEST_PATH_INVALID'}
            names.add(relative.casefold())
            if not isinstance(expected, str) or re.fullmatch('[0-9a-f]{64}', expected) is None:
                raise ValueError('invalid hash')
            path = bundle / relative
            if not path.resolve().is_relative_to(bundle.resolve()):
                return {'ok': False, 'code': 'PACKAGE_MANIFEST_PATH_INVALID'}
            if not path.is_file():
                missing.append(relative)
                continue
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    changed.append(relative)
        actual = {p.relative_to(bundle).as_posix().casefold() for p in bundle.rglob('*') if p.is_file()}
        unexpected = sorted(actual - names - {'manifest-sha256.json'})
        return {'ok': not missing and not changed and not unexpected, 'files': len(manifest),
                'missing': missing[:10], 'changed': changed[:10],
                'unexpected': unexpected[:10], 'unexpected_count': len(unexpected),
                'missing_count': len(missing), 'changed_count': len(changed)}
    except (OSError, ValueError, TypeError):
        return {'ok': False, 'code': 'PACKAGE_MANIFEST_UNREADABLE'}


def run_probe(python, app, name, code, *, timeout=25):
    env = dict(os.environ)
    for key in ('PYTHONHOME', 'PYTHONPATH', 'TCL_LIBRARY', 'TK_LIBRARY'):
        env.pop(key, None)
    prelude = ('import sys,os; sys.path.insert(0,' + repr(str(app)) + '); '
               "import ctypes; ctypes.windll.kernel32.SetErrorMode(0x8003) if os.name=='nt' else None; ")
    try:
        result = subprocess.run([str(python), '-I', '-B', '-X', 'utf8', '-c', prelude + code],
            env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        detail = (result.stdout + result.stderr).decode('utf-8', errors='replace')[-12000:]
        return {'name': name, 'ok': result.returncode == 0, 'exit_code': result.returncode,
                'exit_hex': f'0x{result.returncode & 0xffffffff:08X}', 'detail': detail}
    except subprocess.TimeoutExpired:
        return {'name': name, 'ok': False, 'code': 'PROBE_TIMEOUT'}
    except OSError as exc:
        return {'name': name, 'ok': False, 'code': 'PROCESS_START_FAILED',
                'winerror': getattr(exc, 'winerror', None)}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Unified portable startup diagnostics')
    parser.add_argument('--assets', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    bundle = Path(__file__).resolve().parents[1]
    output = args.output or (Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) /
        'UnifiedPersonalMCP/diagnostics' / ('startup-' + time.strftime('%Y%m%d-%H%M%S') + '.json'))
    info = {'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'system': platform.platform(),
            'machine': platform.machine(), 'python': sys.version, 'python_bits': struct.calcsize('P') * 8,
            'bundle': str(bundle), 'checks': []}
    if os.name == 'nt':
        version = sys.getwindowsversion()
        info['windows'] = {'major': version.major, 'minor': version.minor, 'build': version.build}
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                    r'SOFTWARE\Microsoft\Windows NT\CurrentVersion') as key:
                info['windows']['display_version'] = winreg.QueryValueEx(key, 'DisplayVersion')[0]
        except OSError:
            pass
    print('Unified Personal MCP startup diagnostics', flush=True)
    print('This check does not read tunnel keys or start the service.', flush=True)
    checks = info['checks']
    checks.append({'name': 'package integrity', **check_package(bundle)})
    checks.append({'name': 'Windows x64', 'ok': os.name == 'nt' and struct.calcsize('P') == 8})
    if args.assets is not None:
        checks.append({'name': 'asset path', 'ok': args.assets.resolve() == bundle / 'resources'})
    python, app = bundle / 'resources/python/python.exe', bundle / 'app'
    probes = [
        ('Python standard library', 'import ssl,sqlite3,ctypes; print("OK")'),
        ('Tk window runtime', 'import tkinter as tk; r=tk.Tk(); r.withdraw(); r.update(); r.destroy(); print("OK")'),
        ('Windows bindings', 'import win32api,win32security,win32crypt,pythoncom; print("OK")'),
        ('Setup window imports', 'import personal_mcp.gui, personal_mcp.quick_setup; print("OK")'),
    ]
    for name, code in probes:
        print('Checking: ' + name, flush=True)
        result = run_probe(python, app, name, code)
        checks.append(result)
        print(('PASS: ' if result['ok'] else 'FAIL: ') + name, flush=True)
        if not result['ok']:
            print(result.get('detail') or result.get('code') or result.get('exit_hex'), flush=True)
    info['ok'] = all(item['ok'] for item in checks)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Report: ' + str(output), flush=True)
    print('PASS' if info['ok'] else 'Some startup checks failed; share the report for diagnosis.', flush=True)
    return 0 if info['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
