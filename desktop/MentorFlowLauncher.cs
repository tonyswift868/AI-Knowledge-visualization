using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Windows.Forms;

internal static class MentorFlowLauncher
{
    [STAThread]
    private static int Main()
    {
        bool created;
        using (var singleInstance = new Mutex(true, @"Local\MentorFlow.Native.Development", out created))
        {
            if (!created)
            {
                MessageBox.Show(
                    "MentorFlow 已经由桌面启动器运行。",
                    "MentorFlow",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information
                );
                return 0;
            }

            try
            {
                var releaseDirectory = AppDomain.CurrentDomain.BaseDirectory;
                var projectRoot = Path.GetFullPath(Path.Combine(releaseDirectory, "..", ".."));
                var pythonRuntime = Path.Combine(projectRoot, ".venv", "Scripts", "python.exe");
                if (!File.Exists(pythonRuntime))
                {
                    MessageBox.Show(
                        "未找到 MentorFlow Python 环境。请先运行 scripts\\bootstrap.ps1。",
                        "MentorFlow 无法启动",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error
                    );
                    return 2;
                }

                var start = new ProcessStartInfo
                {
                    FileName = pythonRuntime,
                    Arguments = "-m desktop.main",
                    WorkingDirectory = projectRoot,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                };
                using (var process = Process.Start(start))
                {
                    if (process == null)
                    {
                        throw new InvalidOperationException("Qt 进程未能启动。");
                    }
                    process.WaitForExit();
                    return process.ExitCode;
                }
            }
            catch (Exception error)
            {
                MessageBox.Show(
                    error.Message,
                    "MentorFlow 无法启动",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return 1;
            }
        }
    }
}
