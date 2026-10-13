"""构建不含第三方二进制、配置和私有状态的源码升级包。"""
import argparse
import json
import shutil
from pathlib import Path

try:
    from .prepare_update import (BASE_VERSIONS, FORMAT, MODULES, digest, package_version,
                                 payload_allowed, plain_tree, validate_manifest, write_manifest)
except ImportError:
    from prepare_update import (BASE_VERSIONS, FORMAT, MODULES, digest, package_version,
                                payload_allowed, plain_tree, validate_manifest, write_manifest)

ROOT = Path(__file__).resolve().parents[1]


def build(destination, *, root=ROOT, zip_output=False):
    root = plain_tree(root, inspect_children=False)
    destination = Path(destination).absolute()
    if destination.exists():
        raise ValueError('DESTINATION_EXISTS')
    for parent in (destination, *destination.parents):
        if parent.is_symlink() or parent.is_junction():
            raise ValueError('LINK_PATH_INVALID')
    files = {}
    for module in MODULES:
        for source in plain_tree(root / module).rglob('*.py'):
            files[f'app/{module}/{source.relative_to(root / module).as_posix()}'] = source
    files.update({f'app/{name}': root / 'packaging' / name
                  for name in ('run.py', 'startup_diagnostics.py')})
    ui = root / 'personal_mcp/ui'
    if ui.is_dir():
        for source in plain_tree(ui).iterdir():
            relative = 'app/personal_mcp/ui/' + source.name
            if source.is_file() and payload_allowed(relative):
                files[relative] = source
    for name in ('prepare_update.py', 'switch_managed_release.py', 'install-autostart.ps1', 'smoke_portable.py'):
        files[f'scripts/{name}'] = root / 'scripts' / name
    for name in ('LICENSE', 'NOTICE', 'README.md', 'verification.md', 'requirements-windows.lock'):
        files[name] = root / name
    files['update.cmd'] = root / 'packaging/update.cmd'
    files['启动诊断.cmd'] = root / 'packaging/startup-diagnostics.cmd'
    files['使用前先看.txt'] = root / 'docs/personal/换机使用说明.txt'
    for folder in ('third_party', 'docs/personal', 'examples/personal'):
        for source in plain_tree(root / folder).rglob('*'):
            if source.is_file():
                relative = source.relative_to(root).as_posix()
                if payload_allowed(relative):
                    files[relative] = source
    if any(not source.is_file() or not payload_allowed(relative) for relative, source in files.items()):
        raise ValueError('UPDATE_SOURCE_INVALID')
    # 资源仅发布摘要。debug.log 与 Playwright 的设备登记目录不属于依赖输入。
    resources = {}
    for folder in ('bin', 'browsers', 'tunnel-client'):
        for source in plain_tree(root / 'resources' / folder).rglob('*'):
            if source.is_file() and source.name != 'debug.log' and '.links' not in source.parts:
                resources[source.relative_to(root / 'resources').as_posix()] = digest(source)
    import ast
    parsed = ast.parse((root / 'personal_mcp/__init__.py').read_text(encoding='utf-8'))
    version = next(ast.literal_eval(node.value) for node in parsed.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == '__version__' for target in node.targets))
    metadata = {'format': FORMAT, 'version': version, 'base_versions': list(BASE_VERSIONS),
                'lock_sha256': digest(root / 'requirements-windows.lock'), 'resources': resources}
    destination.mkdir(parents=True)
    for relative, source in files.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for name, content in (('update-metadata.json', metadata),
                          ('package-metadata.json', {'name': 'unified-personal-mcp', 'version': version, 'tool_count': 63})):
        (destination / name).write_text(json.dumps(content, indent=2) + '\n', encoding='utf-8')
    rows = write_manifest(destination)
    validate_manifest(destination)
    if package_version(destination) != version:
        raise ValueError('PACKAGE_VERSION_MISMATCH')
    result = {'directory': str(destination), 'version': version, 'files': len(rows),
              'third_party_binaries': False}
    if zip_output:
        archive = shutil.make_archive(str(destination), 'zip', destination.parent, destination.name)
        Path(archive + '.sha256').write_text(digest(archive) + '  ' + Path(archive).name + '\n', encoding='utf-8')
        result['archive'] = archive
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--zip', action='store_true')
    args = parser.parse_args()
    print(json.dumps(build(args.output, zip_output=args.zip), ensure_ascii=False))
