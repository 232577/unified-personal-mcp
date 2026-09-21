"""Prepare the pinned search executable and Playwright browser cache."""
import hashlib
import os
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "https://github.com/BurntSushi/ripgrep/releases/download/15.1.0/ripgrep-15.1.0-x86_64-pc-windows-msvc.zip"
SHA256 = "124510b94b6baa3380d051fdf4650eaa80a302c876d611e9dba0b2e18d87493a"
cache = ROOT / "verification"
cache.mkdir(exist_ok=True)
archive = cache / "ripgrep-15.1.0-windows.zip"
if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
    urllib.request.urlretrieve(URL, archive)
if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
    raise ValueError("RIPGREP_ARCHIVE_HASH_MISMATCH")
target = ROOT / "resources/bin"
target.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(archive) as source:
    for member in source.namelist():
        name = Path(member).name
        if name in {"rg.exe", "COPYING", "LICENSE-MIT", "UNLICENSE"}:
            (target / name).write_bytes(source.read(member))
env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(ROOT / "resources/browsers")}
subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], env=env, check=True)
print("Pinned search and browser resources are ready. Configure the official OpenAI tunnel-client separately.")

