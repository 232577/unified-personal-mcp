using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

internal static class Launcher {
    private static string Quote(string value) {
        StringBuilder result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char ch in value) {
            if (ch == '\\') { slashes++; continue; }
            if (ch == '"') { result.Append('\\', slashes * 2 + 1); result.Append(ch); slashes = 0; continue; }
            result.Append('\\', slashes); slashes = 0; result.Append(ch);
        }
        result.Append('\\', slashes * 2); result.Append('"');
        return result.ToString();
    }

    [STAThread]
    private static int Main(string[] args) {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(root, "resources", "python", "pythonw.exe");
        try {
            if (!File.Exists(python)) throw new FileNotFoundException("缺少随程序提供的 Python 运行环境。");
            string command = "-B -s " + Quote(Path.Combine(root, "app", "run.py"));
            if (args.Length == 0) command += " gui";
            foreach (string arg in args) command += " " + Quote(arg);
            command += " --assets " + Quote(Path.Combine(root, "resources"));
            var info = new ProcessStartInfo(python, command);
            info.WorkingDirectory = root;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.WindowStyle = ProcessWindowStyle.Hidden;
            using (Process child = Process.Start(info)) {
                child.WaitForExit();
                return child.ExitCode;
            }
        } catch (Exception error) {
            MessageBox.Show("程序无法启动。\n" + error.Message, "Unified Personal MCP",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}

