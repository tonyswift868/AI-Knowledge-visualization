using System;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Security.Principal;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace MentorFlow.Installation
{
    internal sealed class BootstrapWindow : Form
    {
        private readonly TextBox installRoot = new TextBox { Name = "InstallRoot", Width = 490 };
        private readonly TextBox shortcut = new TextBox { Name = "ShortcutPath", Width = 490 };
        private readonly Label status = new Label { Name = "Status", Width = 540, Height = 65 };
        private readonly Button install = new Button { Name = "Install", Text = "安装", Width = 110 };
        private readonly Button close = new Button { Name = "Close", Text = "关闭", Width = 110 };
        private readonly ProgressBar progress = new ProgressBar { Width = 540, Height = 18, Style = ProgressBarStyle.Marquee, Visible = false };
        private bool running;
        internal int ResultCode { get; private set; }

        internal BootstrapWindow()
        {
            ResultCode = 1;
            Text = "MentorFlow 安装 — " + ReleaseIdentity.Version;
            ClientSize = new Size(580, 365);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            Font = new Font("Microsoft YaHei UI", 9);
            FlowLayoutPanel layout = new FlowLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(18), FlowDirection = FlowDirection.TopDown, WrapContents = false };
            Controls.Add(layout);
            layout.Controls.Add(new Label { Text = "首次安装需要管理员权限；日常启动不需要。", Width = 540, Height = 30 });
            layout.Controls.Add(new Label { Text = "新的程序安装目录（已有目录不会覆盖）", AutoSize = true });
            layout.Controls.Add(installRoot);
            layout.Controls.Add(new Label { Text = "新的快捷方式路径（已有快捷方式不会覆盖）", AutoSize = true });
            layout.Controls.Add(shortcut);
            layout.Controls.Add(status);
            layout.Controls.Add(progress);
            FlowLayoutPanel buttons = new FlowLayoutPanel { Width = 540, Height = 40 };
            buttons.Controls.Add(install);
            buttons.Controls.Add(close);
            layout.Controls.Add(buttons);
            string volume = FindSafeVolume();
            installRoot.Text = volume == null ? "" : Path.Combine(volume, "MentorFlow-" + ReleaseIdentity.Version + "-" + ReleaseIdentity.SourceSha.Substring(0, 8));
            string desktop = Environment.GetFolderPath(Environment.SpecialFolder.CommonDesktopDirectory);
            string link = Path.Combine(desktop, "MentorFlow.lnk");
            if (File.Exists(link) || Directory.Exists(link))
                link = Path.Combine(desktop, "MentorFlow-" + ReleaseIdentity.Version + "-" + ReleaseIdentity.SourceSha.Substring(0, 8) + ".lnk");
            shortcut.Text = link;
            status.Text = "仅安装程序，不迁移或修改学习数据。完成后请通过快捷方式正常启动。";
            install.Click += async (sender, args) => await Install();
            close.Click += (sender, args) => Close();
            FormClosing += (sender, args) =>
            {
                if (running)
                {
                    args.Cancel = true;
                    status.Text = "安装正在执行，请等待完成；不会强制终止正在验证或写入的进程。";
                }
            };
        }

        private static string FindSafeVolume()
        {
            foreach (DriveInfo drive in DriveInfo.GetDrives().OrderBy(d => String.Equals(d.Name, @"E:\", StringComparison.OrdinalIgnoreCase) ? 0 : 1))
            {
                try
                {
                    if (drive.DriveType != DriveType.Fixed || !drive.IsReady) continue;
                    ProtectedStage.AssertSafeParents(drive.RootDirectory.FullName);
                    return drive.RootDirectory.FullName;
                }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
                catch (InvalidOperationException) { }
            }
            return null;
        }

        private async Task Install()
        {
            if (running) return;
            running = true;
            install.Enabled = installRoot.Enabled = shortcut.Enabled = false;
            progress.Visible = true;
            string root = installRoot.Text;
            string link = shortcut.Text;
            try
            {
                await Task.Run(() => InstallFromEmbedded.Run(root, link, Path.GetDirectoryName(Path.GetFullPath(root).TrimEnd('\\')), message =>
                    BeginInvoke(new Action(() => status.Text = message))));
                ResultCode = 0;
                status.Text = "安装完成。请关闭安装器，再通过新快捷方式启动 MentorFlow。";
                close.Text = "完成";
            }
            catch (Exception error)
            {
                ResultCode = 3;
                status.Text = "安装未完成：" + error.Message;
                install.Enabled = installRoot.Enabled = shortcut.Enabled = true;
            }
            finally
            {
                progress.Visible = false;
                running = false;
            }
        }

        [STAThread]
        private static int Main()
        {
            if (!new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator))
            {
                MessageBox.Show("此安装器需要管理员权限。", "MentorFlow 安装");
                return 3;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            using (BootstrapWindow window = new BootstrapWindow())
            {
                Application.Run(window);
                return window.ResultCode;
            }
        }
    }
}
