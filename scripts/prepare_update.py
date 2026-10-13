"""校验源码升级包，把既有完整安装复制到新目录；不切换服务。"""
import argparse
import ast
import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

FORMAT = 'unified-source-update-v1'
BASE_VERSIONS = ('0.1.10', '0.1.11', '0.1.12', '0.1.13')
MODULES = ('personal_mcp', 'bf_automation', 'coding_tools_mcp')
ROOT_FILES = {'LICENSE', 'NOTICE', 'README.md', 'verification.md', 'requirements-windows.lock',
              'package-metadata.json', 'update-metadata.json', 'update.cmd', '使用前先看.txt', '启动诊断.cmd'}
SCRIPTS = {'prepare_update.py', 'switch_managed_release.py', 'install-autostart.ps1', 'smoke_portable.py'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_relative(value):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or value.startswith('/') or any(part in ('', '.', '..') for part in value.split('/'))):
        raise ValueError('MANIFEST_PATH_INVALID')
    return value


def plain_tree(root, *, inspect_children=True):
    # resolve 前检查父目录，避免把 junction/symlink 误当真实安装目录。
    root = Path(root).absolute()
    for path in (root, *root.parents):
        if path.is_symlink() or path.is_junction():
            raise ValueError('LINK_PATH_INVALID')
    if not root.is_dir():
        raise ValueError('DIRECTORY_REQUIRED')
    if inspect_children:
        for path in root.rglob('*'):
            if path.is_symlink() or path.is_junction():
                raise ValueError('LINK_PATH_INVALID')
    return root.resolve(strict=True)


def read_json(path):
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('METADATA_TOO_LARGE')
    def unique(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError('DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)


def validate_manifest(root):
    root = plain_tree(root)
    rows = read_json(root / 'manifest-sha256.json')
    if not isinstance(rows, dict) or not rows or len(rows) > 50000:
        raise ValueError('MANIFEST_INVALID')
    names = set()
    for relative, expected in rows.items():
        safe_relative(relative)
        folded = relative.casefold()
        if folded in names or folded == 'manifest-sha256.json':
            raise ValueError('MANIFEST_PATH_INVALID')
        names.add(folded)
        if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
            raise ValueError('MANIFEST_INVALID')
        path = root / relative
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError('MANIFEST_PATH_INVALID')
        if digest(path) != expected:
            raise ValueError('PACKAGE_HASH_MISMATCH')
    actual = {p.relative_to(root).as_posix().casefold() for p in root.rglob('*') if p.is_file()}
    if actual != names | {'manifest-sha256.json'}:
        raise ValueError('PACKAGE_FILE_SET_MISMATCH')
    return rows


def payload_allowed(relative):
    if relative in ROOT_FILES:
        return True
    parts = relative.split('/')
    if parts[0] == 'app':
        if len(parts) == 4 and parts[1:3] == ['personal_mcp', 'ui']:
            return Path(relative).suffix.lower() in {'.html', '.css', '.js', '.json', '.txt'}
        return (relative in {'app/run.py', 'app/startup_diagnostics.py'} or
                len(parts) >= 3 and parts[1] in MODULES and relative.endswith('.py'))
    if parts[0] == 'scripts':
        return len(parts) == 2 and parts[1] in SCRIPTS
    return parts[0] in {'docs', 'third_party', 'examples'} and (
        Path(relative).suffix.lower() in {'.md', '.txt', '.json', '.yml', '.yaml'} or
        Path(relative).name in {'LICENSE', 'NOTICE', 'COPYING', 'UNLICENSE'})


def package_version(root):
    metadata = read_json(root / 'package-metadata.json')
    if metadata.get('name') != 'unified-personal-mcp':
        raise ValueError('PACKAGE_NAME_INVALID')
    parsed = ast.parse((root / 'app/personal_mcp/__init__.py').read_text(encoding='utf-8'))
    values = [ast.literal_eval(node.value) for node in parsed.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == '__version__' for target in node.targets)]
    if values != [metadata.get('version')]:
        raise ValueError('PACKAGE_VERSION_MISMATCH')
    return values[0]


def write_manifest(root):
    rows = {p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob('*'))
            if p.is_file() and p != root / 'manifest-sha256.json'}
    (root / 'manifest-sha256.json').write_text(json.dumps(rows, indent=2) + '\n', encoding='utf-8')
    return rows


def prepare(base, update, destination=None):
    base, update = plain_tree(base), plain_tree(update)
    if base.is_relative_to(update) or update.is_relative_to(base):
        raise ValueError('SOURCE_OVERLAP')
    update_rows = validate_manifest(update)
    if any(not payload_allowed(name) for name in update_rows):
        raise ValueError('UPDATE_PAYLOAD_INVALID')
    required_update = {'app/run.py', 'scripts/prepare_update.py', 'scripts/install-autostart.ps1',
                       'package-metadata.json', 'requirements-windows.lock', 'LICENSE', 'NOTICE'}
    required_update.update(f'app/{module}/__init__.py' for module in MODULES)
    if not required_update <= update_rows.keys():
        raise ValueError('UPDATE_INCOMPLETE')
    spec = read_json(update / 'update-metadata.json')
    if (spec.get('format') != FORMAT or spec.get('base_versions') != list(BASE_VERSIONS)
            or not re.fullmatch(r'\d+\.\d+\.\d+', str(spec.get('version', '')))):
        raise ValueError('UPDATE_METADATA_INVALID')
    version = spec['version']
    if version == '0.1.14' and not {
            'app/personal_mcp/ui/desktop.html', 'app/personal_mcp/ui/desktop.js',
            'app/personal_mcp/ui/desktop.css', 'app/personal_mcp/ui/THIRD_PARTY_NOTICES.txt'} <= update_rows.keys():
        raise ValueError('UPDATE_UI_INCOMPLETE')
    destination = Path(destination) if destination else base.parent / f'UnifiedPersonalMCP-{version}'
    destination = destination.absolute()
    for parent in (destination, *destination.parents):
        if parent.is_symlink() or parent.is_junction():
            raise ValueError('LINK_PATH_INVALID')
    destination = destination.resolve()
    if destination.exists():
        raise ValueError('DESTINATION_EXISTS')
    if destination.parent != base.parent:
        raise ValueError('DESTINATION_MUST_BE_SIBLING')
    if any(destination.is_relative_to(root) or root.is_relative_to(destination) for root in (base, update)):
        raise ValueError('DESTINATION_OVERLAP')
    validate_manifest(base)
    old_version = read_json(base / 'package-metadata.json').get('version')
    if old_version not in BASE_VERSIONS:
        raise ValueError('BASE_VERSION_UNSUPPORTED')
    if package_version(base) != old_version or package_version(update) != version:
        raise ValueError('PACKAGE_VERSION_MISMATCH')
    required = ('UnifiedPersonalMCP.exe', 'resources/python/python.exe', 'resources/python/pythonw.exe',
                'app/run.py', 'scripts/install-autostart.ps1')
    if any(not (base / name).is_file() for name in required):
        raise ValueError('BASE_PACKAGE_INCOMPLETE')
    expected_lock = spec.get('lock_sha256')
    if digest(base / 'requirements-windows.lock') != expected_lock or digest(update / 'requirements-windows.lock') != expected_lock:
        raise ValueError('DEPENDENCY_LOCK_MISMATCH')
    resources = spec.get('resources')
    if not isinstance(resources, dict) or not resources:
        raise ValueError('RESOURCE_METADATA_INVALID')
    for relative, expected in resources.items():
        safe_relative(relative)
        if relative.split('/')[0] not in {'bin', 'browsers', 'tunnel-client'}:
            raise ValueError('RESOURCE_PATH_INVALID')
        path = base / 'resources' / relative
        if not path.is_file() or digest(path) != expected:
            raise ValueError('RESOURCE_COMPATIBILITY_MISMATCH')
    # copytree 拒绝覆盖，所有输入均已在写入新目录前完成检查。
    shutil.copytree(base, destination)
    shutil.rmtree(destination / 'app')
    for relative in update_rows:
        if relative in {'update-metadata.json', 'update.cmd'}:
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(update / relative, target)
    write_manifest(destination)
    validate_manifest(destination)
    if package_version(destination) != version:
        raise ValueError('PACKAGE_VERSION_MISMATCH')
    return {'status': 'PREPARED', 'bundle': str(destination), 'version': version,
            'base_bundle': str(base), 'base_version': old_version,
            'service_changed': False, 'next_step': 'Validate isolated startup before switching the managed service.'}


def switch_target(task, prepared):
    if (not task.get('exists') or not task.get('managed') or not task.get('bundle')
            or not task.get('config')):
        raise ValueError('MANAGED_TASK_REQUIRED')
    if Path(task['bundle']).resolve() != Path(prepared['base_bundle']).resolve():
        raise ValueError('TASK_BASE_MISMATCH')
    return Path(task['config']).resolve(strict=True)


def apply_prepared(prepared):
    """仅在用户确认后执行；切换子进程重新验证当前任务和服务。"""
    candidate = Path(prepared['bundle'])
    command = [str(candidate / 'resources/python/python.exe'), '-B', '-I',
               str(candidate / 'scripts/prepare_update.py'), '--base', prepared['base_bundle'],
               '--output', prepared['bundle'], '--switch-only']
    completed = subprocess.run(command, check=False)
    return completed.returncode


def switch_prepared(base, candidate):
    candidate = plain_tree(candidate)
    # sys.path只指向已校验候选app，使用base配置，不复制或打印私有内容。
    validate_manifest(candidate)
    sys.path.insert(0, str(candidate / 'app'))
    from personal_mcp.autostart import TASK_NAME, WindowsTaskScheduler
    from personal_mcp.config import load_config
    metadata = read_json(candidate / 'package-metadata.json')
    version = package_version(candidate)
    base_version = package_version(plain_tree(base))
    prepared = {'bundle': str(candidate), 'base_bundle': str(base)}
    config_path = switch_target(WindowsTaskScheduler().status(TASK_NAME), prepared)
    if metadata.get('tool_count') != 63:
        raise ValueError('UPDATE_TOOL_RECEIPT_INVALID')
    config = load_config(config_path)
    output = config.data_root / ('update-' + uuid.uuid4().hex)
    command = [str(candidate / 'resources/python/python.exe'), '-B', '-s',
               str(candidate / 'scripts/switch_managed_release.py'), '--run', '--config', str(config_path),
               '--candidate', str(candidate), '--old-bundle', str(base), '--output', str(output),
               '--version', version, '--old-version', base_version, '--tools', '63']
    completed = subprocess.run(command, check=False, cwd=candidate)
    receipt = output / 'result.json'
    if receipt.is_file():
        result = read_json(receipt)
        print(json.dumps({'switch_status': result.get('status'), 'result_file': str(receipt)}, ensure_ascii=False))
    if completed.returncode:
        print('Switch did not complete. Inspect the result above. Start the old local console if its service is offline, then retry after all workflows end.')
    return completed.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--update', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--apply', action='store_true', help='Offer an explicit managed-service switch after preparation.')
    parser.add_argument('--switch-only', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.switch_only:
            if args.output is None:
                raise ValueError('CANDIDATE_REQUIRED')
            return switch_prepared(args.base, args.output)
        result = prepare(args.base, args.update, args.output)
        print(json.dumps(result, ensure_ascii=False))
        if args.apply and input('Switch to the prepared release now? (y/N): ').strip().lower() == 'y':
            return apply_prepared(result)
        return 0
    except (ValueError, OSError, SyntaxError, RuntimeError, EOFError, json.JSONDecodeError) as exc:
        print(json.dumps({'status': 'NOT_PREPARED', 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
