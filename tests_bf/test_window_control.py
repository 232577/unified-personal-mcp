import pytest

from bf_automation.task_store import TaskStore
from bf_automation.window_control import (
    BackgroundControlUnavailable,
    NativeWinApi,
    WindowController,
)


class FakeWinApi:
    def __init__(self):
        self.windows = [
            {
                "hwnd": 100,
                "pid": 10,
                "title": "Fixture",
                "visible": True,
                "minimized": False,
                "rect": [10, 20, 410, 320],
            }
        ]

    def inventory(self, *, include_hidden=False, title_filter=None, process_id=None):
        return list(self.windows)

    def validate_window(self, hwnd, pid):
        return hwnd == 100 and pid == 10

    def control(self, hwnd, *, action, element, text, clear, loc, button, clicks):
        return {"mode": "uia", "verified": True, "hwnd": hwnd, "action": action}

    def snapshot(self, hwnd, *, max_elements=120):
        return {
            "elements": [
                {
                    "element_id": "bfe_button",
                    "name": "Run",
                    "control_type": "ButtonControl",
                }
            ],
            "element_count": 1,
        }

    def foreground_control(self, hwnd, **kwargs):
        return {
            "mode": "foreground_fallback",
            "verified": True,
            "foreground_preserved": True,
            "foreground_restored": True,
        }


class FakeCapture:
    def capture(self, hwnd, *, allow_staging=True):
        return {"backend": "fake", "width": 400, "height": 300, "staging_used": False}


class BytesCapture:
    def capture(self, hwnd, *, allow_staging=True):
        return {
            "backend": "fake",
            "width": 1,
            "height": 1,
            "staging_used": False,
            "png_bytes": b"not-a-real-png-but-private",
        }


class BackgroundFailWinApi(FakeWinApi):
    def control(self, hwnd, **kwargs):
        raise BackgroundControlUnavailable("background path unavailable")


class ShortcutWinApi(FakeWinApi):
    def control(self, hwnd, **kwargs):
        if kwargs.get("action") == "shortcut":
            raise BackgroundControlUnavailable("shortcut requires foreground")
        return super().control(hwnd, **kwargs)

    def foreground_control(self, hwnd, **kwargs):
        return {
            "mode": "foreground_fallback",
            "verified": False,
            "foreground_preserved": False,
            "foreground_restored": True,
            "shortcut": kwargs.get("shortcut"),
        }


def make_controller(tmp_path):
    allowed = tmp_path / "run"
    project = allowed / "project"
    project.mkdir(parents=True)
    store = TaskStore(tmp_path / "state", allowed_root=allowed)
    task = store.begin(project)["bf_task_id"]
    return WindowController(store, win_api=FakeWinApi(), capture_backend=FakeCapture()), task


def test_inventory_returns_task_scoped_opaque_window_id(tmp_path):
    controller, task = make_controller(tmp_path)

    result = controller.inventory(task, include_hidden=True)

    assert result["count"] == 1
    assert result["windows"][0]["window_id"].startswith("bfw_")
    assert "100" not in result["windows"][0]["window_id"]


def test_background_control_refuses_foreground_fallback_by_default(tmp_path):
    controller, task = make_controller(tmp_path)
    window_id = controller.inventory(task)["windows"][0]["window_id"]

    result = controller.control(task, window_id, action="invoke")

    assert result["foreground_fallback_allowed"] is False
    assert result["mode"] == "uia"


def test_foreign_task_cannot_control_window(tmp_path):
    controller, task_a = make_controller(tmp_path)
    window_id = controller.inventory(task_a)["windows"][0]["window_id"]
    project = controller.store.allowed_root / "project"
    task_b = controller.store.begin(project)["bf_task_id"]

    with pytest.raises(PermissionError, match="window_id"):
        controller.control(task_b, window_id, action="invoke")


def test_two_tasks_cannot_write_same_window_using_their_own_ids(tmp_path):
    controller, task_a = make_controller(tmp_path)
    window_a = controller.inventory(task_a)["windows"][0]["window_id"]
    task_b = controller.store.begin(controller.store.allowed_root / "project")["bf_task_id"]
    window_b = controller.inventory(task_b)["windows"][0]["window_id"]
    controller.control(task_a, window_a, action="invoke")
    with pytest.raises(PermissionError, match="another task"):
        controller.control(task_b, window_b, action="invoke")
    controller.store.end(task_a)
    assert controller.control(task_b, window_b, action="invoke")["mode"] == "uia"


def test_snapshot_returns_bounded_elements_without_foreground(tmp_path):
    controller, task = make_controller(tmp_path)
    window_id = controller.inventory(task)["windows"][0]["window_id"]

    result = controller.snapshot(task, window_id, include_image=False, max_elements=25)

    snapshot = result["window_snapshot"]
    assert snapshot["window_id"] == window_id
    assert snapshot["element_count"] == 1
    assert snapshot["elements"][0]["element_id"] == "bfe_button"


def test_screenshot_bytes_are_written_inside_task_directory(tmp_path):
    controller, task = make_controller(tmp_path)
    controller.capture_backend = BytesCapture()
    window_id = controller.inventory(task)["windows"][0]["window_id"]

    result = controller.screenshot(task, window_id)

    capture = result["window_capture"]
    assert "png_bytes" not in capture
    assert capture["path"].startswith(controller.store.status(task)["task_directory"])
    assert capture["sha256"]


def test_background_failure_refuses_foreground_fallback_by_default(tmp_path):
    controller, task = make_controller(tmp_path)
    controller.win_api = BackgroundFailWinApi()
    window_id = controller.inventory(task)["windows"][0]["window_id"]

    result = controller.control(task, window_id, action="invoke")

    assert result["accepted"] is False
    assert result["mode"] == "background_refused"
    assert result["foreground_fallback_allowed"] is False


def test_explicit_foreground_fallback_is_restored(tmp_path):
    controller, task = make_controller(tmp_path)
    controller.win_api = BackgroundFailWinApi()
    window_id = controller.inventory(task)["windows"][0]["window_id"]

    result = controller.control(
        task,
        window_id,
        action="invoke",
        allow_foreground_fallback=True,
    )

    assert result["mode"] == "foreground_fallback"
    assert result["foreground_restored"] is True
    assert result["foreground_fallback_allowed"] is True


def test_shortcut_is_refused_in_background_and_requires_explicit_fallback(tmp_path):
    controller, task = make_controller(tmp_path)
    controller.win_api = ShortcutWinApi()
    window_id = controller.inventory(task)["windows"][0]["window_id"]

    refused = controller.control(task, window_id, action="shortcut", shortcut="ctrl+s")

    assert refused["accepted"] is False
    assert refused["mode"] == "background_refused"

    accepted = controller.control(
        task,
        window_id,
        action="shortcut",
        shortcut="ctrl+s",
        allow_foreground_fallback=True,
    )

    assert accepted["mode"] == "foreground_fallback"
    assert accepted["foreground_restored"] is True
    assert accepted["shortcut"] == "ctrl+s"


class EmptyUia:
    class _Init:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    @staticmethod
    def UIAutomationInitializerInThread():
        return EmptyUia._Init()

    @staticmethod
    def ControlFromHandle(_hwnd):
        return object()

    @staticmethod
    def WalkControl(_root, **_kwargs):
        return []


class DialogWin32:
    def __init__(self):
        self.sent = []
        self.classes = {500: "#32770", 601: "Button", 602: "Edit"}
        self.text = {500: "另存为", 601: "保存(&S)", 602: "u4a.txt"}
        self.ids = {601: 1, 602: 1001}

    def IsWindow(self, hwnd):
        return hwnd in self.classes

    def IsChild(self, parent, child):
        return parent == 500 and child in {601, 602}

    def GetClassName(self, hwnd):
        return self.classes[hwnd]

    def GetWindowText(self, hwnd):
        return self.text.get(hwnd, "")

    def EnumChildWindows(self, hwnd, callback, extra):
        assert hwnd == 500
        for child in (601, 602):
            callback(child, extra)

    def GetDlgCtrlID(self, hwnd):
        return self.ids.get(hwnd, 0)

    def IsWindowEnabled(self, _hwnd):
        return True

    def IsWindowVisible(self, _hwnd):
        return True

    def GetWindowRect(self, hwnd):
        return {601: (10, 10, 90, 40), 602: (100, 10, 300, 40)}[hwnd]

    def GetWindowLong(self, _hwnd, _index):
        return 0

    def GetParent(self, hwnd):
        assert hwnd in {601, 602}
        return 500

    def SendMessageTimeout(self, *args):
        self.sent.append(args)
        return 1


class DialogCon:
    GWL_STYLE = -16
    BN_CLICKED = 0
    WM_COMMAND = 0x0111
    WM_SETTEXT = 0x000C
    SMTO_ABORTIFHUNG = 0x0002


def test_common_dialog_native_children_fill_uia_gap_and_remain_semantic():
    api = object.__new__(NativeWinApi)
    api.win32gui = DialogWin32()
    api.win32con = DialogCon()
    api.uia = EmptyUia()
    api._elements = {}

    snapshot = api.snapshot(500, max_elements=20)

    save = next(item for item in snapshot["elements"] if item["name"] == "保存(&S)")
    assert save["class_name"] == "Button"
    assert save["control_type"] == "ButtonControl"
    assert save["native_handle"] == 601
    assert save["automation_id"] == "native:1"

    result = api.control(
        500,
        action="invoke",
        element=save["element_id"],
        text=None,
        clear=False,
        loc=None,
        button="left",
        clicks=1,
    )

    assert result["mode"] == "win32_command"
    assert result["accepted"] is True
    assert api.win32gui.sent
