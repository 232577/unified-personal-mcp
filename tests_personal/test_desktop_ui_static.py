"""离线界面的交付边界；不替代真实宿主与桌面验收。"""

from html.parser import HTMLParser
from pathlib import Path
import re


UI = Path(__file__).resolve().parents[1] / "personal_mcp" / "ui"


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_desktop_ui_is_self_contained_and_has_accessible_literal_input():
    markup = (UI / "desktop.html").read_text(encoding="utf-8")
    parsed = Elements()
    parsed.feed(markup)
    assert not re.search(r'(?:src|href)=["\']https?://', markup)
    assert any(tag == "canvas" and attrs.get("tabindex") == "0"
               and attrs.get("aria-label") for tag, attrs in parsed.tags)
    assert any(tag == "textarea" and attrs.get("id") == "literal-text"
               for tag, attrs in parsed.tags)
    assert all("novalidate" in attrs for tag, attrs in parsed.tags if tag == "form")
    assert (UI / "desktop.css").is_file() and (UI / "desktop.js").is_file()


def test_desktop_bundle_has_no_sdk_raw_message_console_logging():
    bundle = (UI / "desktop.js").read_text(encoding="utf-8")
    assert not re.search(r'console\.(?:debug|log|warn|error)\(', bundle)
    assert "unified/ui_session" in bundle
    assert "https://cdn" not in bundle
    assert (UI / "desktop.source.js").is_file()
    assert (UI / "THIRD_PARTY_NOTICES.txt").is_file()
