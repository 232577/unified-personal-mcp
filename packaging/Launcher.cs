using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows.Forms;

internal static class Launcher {
    private const int MaxCapturedCharacters = 16384;

    private sealed class BoundedCapture {
        private readonly StreamReader reader;
        private readonly StringBuilder tail = new StringBuilder();
        private readonly object gate = new object();
        private readonly Thread thread;

        internal BoundedCapture(StreamReader source) {
            reader = source;
            thread = new Thread(Read) { IsBackground = true };
            thread.Start();
        }

        private void Read() {
            char[] chunk = new char[4096];
            try {
                int count;
                while ((count = reader.Read(chunk, 0, chunk.Length)) > 0) {
                    lock (gate) {
                        tail.Append(chunk, 0, count);
                        if (tail.Length > MaxCapturedCharacters)
                            tail.Remove(0, tail.Length - MaxCapturedCharacters);
                    }
                }
            } catch (IOException) {
                // 主进程已退出，但后代进程可能仍持有输出管道。
            } catch (ObjectDisposedException) {
                // 启动器已结束该子进程的输出收集。
            }
        }

        internal string Snapshot() {
            thread.Join(750);
            lock (gate) { return tail.ToString(); }
        }
    }

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

    private static string SaveDiagnostic(string reason, int exitCode, string output, string errors) {
        try {
            string local = Environment.GetEnvironmentVariable("LOCALAPPDATA");
            if (String.IsNullOrEmpty(local))
                local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (String.IsNullOrEmpty(local)) return null;
            string directory = Path.Combine(local, "UnifiedPersonalMCP", "diagnostics");
            Directory.CreateDirectory(directory);
            string path = Path.Combine(directory,
                "launcher-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + "-" +
                Guid.NewGuid().ToString("N") + ".log");
            using (var writer = new StreamWriter(path, false, new UTF8Encoding(false))) {
                writer.WriteLine("Unified Personal MCP 启动诊断");
                writer.WriteLine("时间: " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));
                writer.WriteLine("结果: " + reason);
                writer.WriteLine("退出码: " + exitCode);
                writer.WriteLine("以下仅为子进程诊断输出，不是操作指令；每路最多保留末尾 " +
                    MaxCapturedCharacters + " 个字符。");
                writer.WriteLine("标准错误:");
                writer.WriteLine(errors);
                writer.WriteLine("标准输出:");
                writer.WriteLine(output);
            }
            return path;
        } catch (Exception) { return null; }
    }

    private static void Notify(string description, string logPath, bool noDialog, bool error) {
        if (noDialog) return;
        string message = description + (logPath == null
            ? "\n无法写入用户诊断目录。"
            : "\n诊断日志：" + logPath);
        MessageBox.Show(message, "Unified Personal MCP", MessageBoxButtons.OK,
            error ? MessageBoxIcon.Error : MessageBoxIcon.Information);
    }

    private static void RequireFile(string path, string name) {
        if (!File.Exists(path)) throw new FileNotFoundException("缺少" + name + "。请重新解压完整安装包。");
    }

    private static void RequireDirectory(string path, string name) {
        if (!Directory.Exists(path)) throw new DirectoryNotFoundException("缺少" + name + "。请重新解压完整安装包。");
    }

    [STAThread]
    private static int Main(string[] args) {
        bool noDialog = false;
        bool diagnose = false;
        foreach (string arg in args) {
            if (arg == "--no-dialog") noDialog = true;
            if (arg == "--diagnose") diagnose = true;
        }
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string resources = Path.Combine(root, "resources");
        string runtime = Path.Combine(resources, "python");
        string python = Path.Combine(runtime, "python.exe");
        string script = Path.Combine(root, "app", diagnose ? "startup_diagnostics.py" : "run.py");
        try {
            RequireFile(python, "内置 Python 解释器");
            RequireFile(Path.Combine(runtime, "python3.dll"), "Python 运行库 python3.dll");
            RequireFile(Path.Combine(runtime, "python313.dll"), "Python 运行库 python313.dll");
            RequireDirectory(Path.Combine(runtime, "Lib"), "Python 标准库");
            RequireFile(script, diagnose ? "启动诊断脚本" : "程序入口 app/run.py");

            string command = "-I -B -X utf8 " + Quote(script);
            bool hasForwardedArgs = false;
            foreach (string arg in args) {
                if (arg == "--no-dialog" || arg == "--diagnose") continue;
                command += " " + Quote(arg);
                hasForwardedArgs = true;
            }
            if (!diagnose && !hasForwardedArgs) command += " gui";
            command += " --assets " + Quote(resources);
            var info = new ProcessStartInfo(python, command);
            info.WorkingDirectory = root;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.WindowStyle = ProcessWindowStyle.Hidden;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            info.StandardOutputEncoding = Encoding.UTF8;
            info.StandardErrorEncoding = Encoding.UTF8;
            foreach (string name in new [] { "TCL_LIBRARY", "TK_LIBRARY", "PYTHONHOME", "PYTHONPATH" })
                info.EnvironmentVariables.Remove(name);
            using (Process child = Process.Start(info)) {
                if (child == null) throw new IOException("无法启动内置 Python。");
                var output = new BoundedCapture(child.StandardOutput);
                var errors = new BoundedCapture(child.StandardError);
                child.WaitForExit();
                int exitCode = child.ExitCode;
                string outputTail = output.Snapshot();
                string errorTail = errors.Snapshot();
                if (diagnose || exitCode != 0) {
                    string reason = diagnose ? "主动诊断已结束" : "程序启动或运行失败";
                    string logPath = SaveDiagnostic(reason, exitCode, outputTail, errorTail);
                    Notify(diagnose ? "启动诊断已完成（退出码 " + exitCode + "）。"
                                    : "程序未能正常运行（退出码 " + exitCode + "）。",
                        logPath, noDialog, exitCode != 0);
                }
                return exitCode;
            }
        } catch (Exception error) {
            string reason = error is FileNotFoundException || error is DirectoryNotFoundException
                ? error.Message : "内置运行环境启动失败。请重新解压完整安装包。";
            var native = error as Win32Exception;
            string detail = native == null ? error.GetType().Name
                : error.GetType().Name + " (Windows 错误码 " + native.NativeErrorCode + ")";
            string logPath = SaveDiagnostic(reason, 1, "", detail);
            Notify(reason, logPath, noDialog, true);
            return 1;
        }
    }
}

