"""Build a clean personal Windows installation from explicit source and dependency inputs.

No live settings, credentials, state, browser profiles, or development venv are copied.
The output is for this owner's machine; public source publication is separate.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def run(*command, **kwargs):
    subprocess.run(list(map(str, command)), check=True, **kwargs)


def installation_files(destination):
    """Ship login management with the package rather than referencing the repo."""
    import ast

    module = ast.parse((ROOT / "personal_mcp/__init__.py").read_text(encoding="utf-8"))
    version = next(ast.literal_eval(statement.value) for statement in module.body
        if isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__version__"
                                                   for target in statement.targets))
    scripts = destination / "scripts"
    scripts.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "scripts/install-autostart.ps1", scripts / "install-autostart.ps1")
    (destination / "package-metadata.json").write_text(json.dumps(
        {"name": "unified-personal-mcp", "version": version}, indent=2), encoding="utf-8")


def build(destination, python_home, assets, *, zip_output=False):
    destination = destination.resolve()
    if not destination.is_relative_to(ROOT / "release-private") or destination.exists():
        raise ValueError("Choose a NEW child of release-private")
    python_home, assets = python_home.resolve(strict=True), assets.resolve(strict=True)
    version = subprocess.check_output([str(python_home / "python.exe"), "-I", "-c",
        "import sys,struct; print('.'.join(map(str,sys.version_info[:3]))+':'+str(struct.calcsize('P')*8))"],
        text=True).strip()
    if version != "3.13.14:64":
        raise ValueError("CPython 3.13.14 x64 is required")
    required = {"bin/rg.exe": "decdd4992f3f1b9a5ef9898f1b40ab16886d579d6516b4efd3d5eaa19364e408",
        "tunnel-client/tunnel-client.exe": "fcc85a69ec0ad82518e4f8964f60c45e31787957782a0fc9c1b0c44e82d61b9b"}
    for relative, expected in required.items():
        if digest(assets / relative) != expected:
            raise ValueError("DEPENDENCY_HASH_MISMATCH: " + relative)
    destination.mkdir(parents=True)
    target_python = destination / "resources/python"
    target_python.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "site-packages", "test", "tests", "idle_test")
    for name in ("DLLs", "Lib", "tcl"):
        shutil.copytree(python_home / name, target_python / name, ignore=ignore)
    for name in ("python.exe", "pythonw.exe", "python3.dll", "python313.dll",
                 "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt"):
        shutil.copy2(python_home / name, target_python / name)
    run(sys.executable, "-m", "pip", "install", "--no-deps", "--no-compile", "--only-binary=:all:",
        "--target", target_python / "Lib/site-packages", "-r", ROOT / "requirements-windows.lock",
        env={**os.environ, "PIP_CACHE_DIR": str(ROOT / "verification/pip-cache")})
    app = destination / "app"
    app.mkdir()
    for name in ("personal_mcp", "bf_automation", "coding_tools_mcp"):
        shutil.copytree(ROOT / name, app / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "packaging/run.py", app / "run.py")
    for name in ("browsers", "bin", "tunnel-client"):
        shutil.copytree(assets / name, destination / "resources" / name,
                        ignore=shutil.ignore_patterns(".links"))
    for name in ("LICENSE", "NOTICE", "requirements-windows.lock", "README.md", "verification.md"):
        shutil.copy2(ROOT / name, destination / name)
    shutil.copytree(ROOT / "third_party", destination / "third_party")
    shutil.copytree(ROOT / "docs/personal", destination / "docs/personal")
    shutil.copytree(ROOT / "examples/personal", destination / "examples/personal")
    installation_files(destination)
    csc = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    run(csc, "/nologo", "/target:winexe", "/platform:x64", "/reference:System.Windows.Forms.dll",
        "/out:" + str(destination / "UnifiedPersonalMCP.exe"), ROOT / "packaging/Launcher.cs")
    # Confirm the runtime, modules and worker entry point resolve from this installation.
    run(target_python / "python.exe", "-B", "-s", app / "run.py", "--help", cwd=destination)
    run(target_python / "python.exe", "-B", "-I", "-c",
        "import tkinter,win32api,playwright; from fastmcp import FastMCP; "
        "from windows_mcp.desktop.service import Desktop; print('portable imports OK')",
        cwd=destination)
    manifest = {p.relative_to(destination).as_posix(): digest(p) for p in destination.rglob("*") if p.is_file()}
    (destination / "manifest-sha256.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if zip_output:
        archive = shutil.make_archive(str(destination), "zip", destination.parent, destination.name)
        Path(archive + ".sha256").write_text(digest(Path(archive)) + "  " + Path(archive).name + "\n", encoding="utf-8")
    print(json.dumps({"directory": str(destination), "files": len(manifest), "python": version}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python-home", type=Path, default=Path(sys.base_prefix))
    parser.add_argument("--assets", type=Path, default=ROOT / "resources")
    parser.add_argument("--zip", action="store_true")
    args = parser.parse_args()
    build(args.output, args.python_home, args.assets, zip_output=args.zip)
