import pytest

from personal_mcp.operations import OperationJournal


def test_completed_operation_repeats_result_without_replaying(tmp_path):
    journal = OperationJournal(tmp_path / "operations.sqlite3")
    calls = []

    def execute():
        calls.append(1)
        return {"content": [{"type": "text", "text": "saved"}], "isError": False}

    first = journal.run("owner", "save-1", "save", {"value": "hello"}, execute)
    assert journal.run("owner", "save-1", "save", {"value": "hello"}, execute) == first
    assert calls == [1]
    with pytest.raises(ValueError, match="CONFLICT"):
        journal.run("owner", "save-1", "save", {"value": "changed"}, execute)


def test_dispatched_exception_and_restart_never_replay(tmp_path):
    path = tmp_path / "operations.sqlite3"
    journal = OperationJournal(path)
    calls = []

    def disconnected():
        calls.append(1)
        raise TimeoutError("reply lost after saving")

    with pytest.raises(TimeoutError):
        journal.run("owner", "save", "save", {}, disconnected)
    restarted = OperationJournal(path)
    with pytest.raises(TimeoutError, match="UNKNOWN"):
        restarted.run("owner", "save", "save", {}, disconnected)
    assert calls == [1]
    assert restarted.run("other", "save", "save", {}, lambda: {"ok": True}) == {"ok": True}
