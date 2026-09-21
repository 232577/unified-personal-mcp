from pathlib import Path

import pytest


def test_process_identity_detects_pid_reuse():
    from bf_automation.hybrid.process import ProcessIdentity

    original = ProcessIdentity(1200, 10.0, Path('C:/fixture.exe'))
    same = ProcessIdentity(1200, 10.0, Path('C:/fixture.exe'))
    reused = ProcessIdentity(1200, 11.0, Path('C:/fixture.exe'))
    wrong_exe = ProcessIdentity(1200, 10.0, Path('C:/other.exe'))
    assert original.matches(same)
    assert not original.matches(reused)
    assert not original.matches(wrong_exe)


def test_hybrid_instance_id_is_opaque_and_connection_details_are_private():
    from bf_automation.hybrid.manager import HybridInstance
    from bf_automation.hybrid.process import ProcessIdentity

    instance = HybridInstance(
        hybrid_instance_id='bfh_' + 'a' * 32, owner='b' * 16,
        application_id='u4a-fixture', ownership='managed',
        directory=Path('D:/run/task/hybrid/bfh'),
        user_data_dir=Path('D:/run/task/hybrid/bfh/user-data'),
        ready_file=Path('D:/run/task/hybrid/bfh/ready.json'), debug_port=53123,
        shell=ProcessIdentity(1200, 10.0, Path('D:/run/fixture.exe')),
        shell_hwnd=500, shell_window_id='bfw_' + 'c' * 24,
        webview=ProcessIdentity(1201, 11.0, Path('C:/WebView/msedgewebview2.exe')),
        engine_version='153.0.4234.32', state='active',
    )
    public = instance.public_identity()
    assert public == {
        'hybrid_instance_id': 'bfh_' + 'a' * 32,
        'ownership': 'managed',
        'shell_window_id': 'bfw_' + 'c' * 24,
    }
    assert 'debug_port' not in public
    assert 'user_data_dir' not in public
    assert 'webview' not in public


@pytest.mark.parametrize('value', ['bfh_short', 'bad_' + 'a' * 32, 'bfh_' + 'g' * 32])
def test_hybrid_instance_rejects_invalid_opaque_id(value):
    from bf_automation.hybrid.manager import validate_hybrid_instance_id

    with pytest.raises(ValueError, match='INVALID_HYBRID_INSTANCE_ID'):
        validate_hybrid_instance_id(value)
