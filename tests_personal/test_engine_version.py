import io

import pytest


@pytest.mark.parametrize("value,expected", [("Edg/153.0.3672.91", "153.0.3672.91"),
    ("Chrome/153.0.3672.91", "153.0.3672.91"), ("untrusted/1", None)])
def test_debug_version_normalizes_only_known_engine_prefix(monkeypatch, value, expected):
    import json
    import urllib.request
    from types import SimpleNamespace
    from bf_automation.hybrid.attach import ExistingWebViewResolver
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: SimpleNamespace(
        open=lambda *args, **kwargs: io.BytesIO(json.dumps({"Browser": value}).encode())))
    if expected:
        assert ExistingWebViewResolver._engine_version(12345) == expected
    else:
        with pytest.raises(ValueError, match="DEBUG_VERSION_UNAVAILABLE"):
            ExistingWebViewResolver._engine_version(12345)

