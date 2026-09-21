import os

from bf_automation.runtime import application_environment


def test_windows_folder_recovery_and_secret_filtering():
    result = application_environment({**os.environ, "UPM_TEST_SECRET": "must-not-inherit"})
    assert "UPM_TEST_SECRET" not in result
    assert result["SystemDrive"] and result["ProgramData"]
    assert "%" not in result["ProgramData"]
