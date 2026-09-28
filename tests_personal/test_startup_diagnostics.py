import importlib.util
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


spec = importlib.util.spec_from_file_location('startup_diagnostics',
    Path(__file__).resolve().parents[1] / 'packaging/startup_diagnostics.py')
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


def test_probe_reports_missing_import_without_starting_service(tmp_path):
    result = diagnostics.run_probe(Path(sys.executable), tmp_path,
        'missing dependency', 'import intentionally_missing_unified_dependency')
    assert not result['ok'] and result['exit_code'] == 1
    assert 'ModuleNotFoundError' in result['detail']


def test_probe_timeout_is_bounded_and_classified(tmp_path):
    result = diagnostics.run_probe(Path(sys.executable), tmp_path,
        'slow import', 'import time; time.sleep(20)', timeout=0.1)
    assert not result['ok'] and result['code'] == 'PROBE_TIMEOUT'


def test_manifest_rejects_escape_and_records_missing_files(tmp_path):
    manifest = tmp_path / 'manifest-sha256.json'
    manifest.write_text(json.dumps({'../private.key': '0' * 64}), encoding='utf-8')
    result = diagnostics.check_package(tmp_path)
    assert not result['ok'] and result['code'] == 'PACKAGE_MANIFEST_PATH_INVALID'
    manifest.write_text(json.dumps({'resources/python/python.exe': '0' * 64}), encoding='utf-8')
    result = diagnostics.check_package(tmp_path)
    assert not result['ok'] and result['missing'] == ['resources/python/python.exe']


def test_probe_does_not_inherit_python_or_tcl_location_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv('PYTHONHOME', str(tmp_path / 'missing python'))
    monkeypatch.setenv('TCL_LIBRARY', str(tmp_path / 'missing tcl'))
    result = diagnostics.run_probe(Path(sys.executable), tmp_path, 'isolated Python',
        "import os; print('clean' if 'TCL_LIBRARY' not in os.environ else 'leaked')")
    assert result['ok'] and 'clean' in result['detail']


def test_bundled_entrypoint_preserves_returned_failure(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'packaging/run.py'
    shutil.copy2(source, tmp_path / 'run.py')
    module = tmp_path / 'personal_mcp'
    module.mkdir()
    (module / '__init__.py').write_text('', encoding='utf-8')
    (module / '__main__.py').write_text('def main():\n    return 7\n', encoding='utf-8')
    result = subprocess.run([sys.executable, '-I', '-B', str(tmp_path / 'run.py')],
                            capture_output=True, timeout=5)
    assert result.returncode == 7


def test_integrity_reports_extra_files_like_the_installer(tmp_path):
    (tmp_path / 'app.py').write_bytes(b'fixture')
    (tmp_path / 'manifest-sha256.json').write_text(json.dumps({
        'app.py': hashlib.sha256(b'fixture').hexdigest()}), encoding='utf-8')
    (tmp_path / 'extra.txt').write_text('fixture', encoding='utf-8')
    result = diagnostics.check_package(tmp_path)
    assert not result['ok']
    assert result['unexpected'] == ['extra.txt']
