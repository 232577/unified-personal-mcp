import os
import subprocess
import time
from pathlib import Path

from bf_automation.task_store import TaskStore
from bf_automation.window_control import WindowController


def test_winforms_snapshot_reads_current_edit_value_and_masks_password(tmp_path):
    source = tmp_path / "ValueFixture.cs"
    source.write_text('''using System;
using System.Windows.Forms;
class ValueFixture {
 [STAThread] static void Main() {
  Form f = new Form(); f.Text = "UPM value fixture";
  f.Controls.Add(new TextBox { Name="Value", Text="before", Top=20 });
  f.Controls.Add(new TextBox { Name="Password", Text="private-fixture", Top=60, UseSystemPasswordChar=true });
  Application.Run(f);
 }
}
''', encoding="utf-8")
    executable = tmp_path / "ValueFixture.exe"
    csc = Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run([str(csc), "/nologo", "/target:winexe", "/reference:System.Windows.Forms.dll",
                    "/out:" + str(executable), str(source)], check=True, timeout=30,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    store = TaskStore(tmp_path / "state", allowed_root=tmp_path)
    token = store.begin(tmp_path)["bf_task_id"]
    controller = WindowController(store)
    try:
        process = store.spawn_owned(token, [str(executable)], cwd=tmp_path)
        deadline = time.monotonic() + 10
        while not (windows := controller.inventory(token, process_id=process.pid)["windows"]):
            assert time.monotonic() < deadline
            time.sleep(.05)
        window = windows[0]["window_id"]
        before = controller.snapshot(token, window, include_image=False)["window_snapshot"]
        element = next(e for e in before["elements"] if e["automation_id"] == "Value")
        controller.control(token, window, action="set_value", element_id=element["element_id"], text="after")
        after = controller.snapshot(token, window, include_image=False)["window_snapshot"]
        values = {e["automation_id"]: e["native_text"] for e in after["elements"]}
        assert values["Value"] == "after"
        assert values["Password"] == ""
    finally:
        store.end(token)
