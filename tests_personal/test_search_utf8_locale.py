"""ripgrep JSON is UTF-8, independently of Windows' legacy text locale."""

import subprocess
from pathlib import Path

from coding_tools_mcp import server as upstream
from tests_personal.test_host import call, host as host, workflow


def test_real_rg_search_decodes_chinese_paths_and_content_under_gbk_default(host, monkeypatch):
    rg = Path(__file__).resolve().parents[1] / 'resources/bin/rg.exe'
    assert rg.is_file(), 'pinned ripgrep fixture is required'
    original = upstream.cached_which
    monkeypatch.setattr(upstream, 'cached_which', lambda name: str(rg) if name == 'rg' else original(name))
    # Force the Windows legacy default even when the test interpreter uses -X utf8.
    # Explicit subprocess encodings are not affected by this fixture.
    monkeypatch.setattr(subprocess, '_text_encoding', lambda: 'gbk')
    project = host.config.workspace_root / 'app'
    target = project / '中文目录/记录.txt'
    target.parent.mkdir()
    text = '编码检查：健康分62 🛠️'
    target.write_text(text + '\n', encoding='utf-8')
    token = workflow(host)
    result = call(host, 'search_text', workflow_id=token, query='健康分', path='中文目录')
    assert not result['isError'], result
    data = result['structuredContent']
    assert data['engine'] == 'rg'
    assert data['total_matches'] == 1
    assert data['matches'][0]['path'] == '中文目录/记录.txt'
    assert data['matches'][0]['preview'] == text
