// Installer-only orchestration. The caller owns visible status/error UI.
using System;
using System.Diagnostics;
using System.IO;
using System.Text;

namespace MentorFlow.Installation
{
    internal static class InstallFromEmbedded
    {
        private static string Quote(string value)
        {
            StringBuilder text = new StringBuilder("\"");
            int slashes = 0;
            foreach (char ch in value)
            {
                if (ch == '\\') { slashes++; continue; }
                text.Append('\\', ch == '"' ? slashes * 2 + 1 : slashes);
                text.Append(ch);
                slashes = 0;
            }
            text.Append('\\', slashes * 2);
            return text.Append('"').ToString();
        }

        internal static void Run(string installRoot, string shortcut, string stageParent, Action<string> status)
        {
            if (status == null) throw new ArgumentNullException("status");
            installRoot = Path.GetFullPath(installRoot);
            shortcut = Path.GetFullPath(shortcut);
            if (String.Equals(installRoot.TrimEnd('\\'), Path.GetPathRoot(installRoot).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase) ||
                Directory.Exists(installRoot) || File.Exists(installRoot))
                throw new InvalidOperationException("New dedicated installation directory required");
            if (!String.Equals(Path.GetExtension(shortcut), ".lnk", StringComparison.OrdinalIgnoreCase) ||
                File.Exists(shortcut) || Directory.Exists(shortcut))
                throw new InvalidOperationException("New shortcut path required");
            installRoot = installRoot.TrimEnd('\\');
            ProtectedStage.AssertSafeParents(Path.GetDirectoryName(installRoot));
            ProtectedStage.AssertSafeParents(Path.GetDirectoryName(shortcut));
            status("Verifying embedded payload and creating protected staging");
            ProtectedStage stage = VerifiedPayload.Extract("MentorFlow.Payload", ReleaseIdentity.ArchiveSha256, stageParent);
            string gate = Path.Combine(stage.Root, "MentorFlowInstallGate.exe");
            if (!File.Exists(gate)) throw new InvalidDataException("Verified native gate missing");
            // Keep child scratch separate from the manifest-governed payload.
            ProtectedStage scratch = ProtectedStage.Create(stageParent);
            ProcessStartInfo start = new ProcessStartInfo(gate);
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.WorkingDirectory = stage.Root;
            start.Arguments = "install --quiet-install --payload-source " + Quote(stage.Root) +
                " --install-root " + Quote(installRoot) + " --shortcut-path " + Quote(shortcut) +
                " --manifest-sha256 " + ReleaseIdentity.ManifestSha256 +
                " --source-sha " + ReleaseIdentity.SourceSha + " --tree-sha " + ReleaseIdentity.TreeSha;
            // Never inherit account, database, module/plugin path or secret hints.
            start.EnvironmentVariables.Clear();
            string system = Environment.SystemDirectory;
            string windows = Directory.GetParent(system).FullName;
            start.EnvironmentVariables["SystemRoot"] = windows;
            start.EnvironmentVariables["WINDIR"] = windows;
            start.EnvironmentVariables["PATH"] = system;
            start.EnvironmentVariables["COMSPEC"] = Path.Combine(system, "cmd.exe");
            start.EnvironmentVariables["TEMP"] = scratch.Root;
            start.EnvironmentVariables["TMP"] = scratch.Root;
            status("Installing, fully verifying and publishing protected shortcut");
            using (Process process = Process.Start(start))
            {
                if (process == null) throw new InvalidOperationException("Native installer did not start");
                process.WaitForExit();
                if (process.ExitCode != 0)
                    throw new InvalidOperationException("Native installation failed: " + process.ExitCode + "; protected partial output retained");
            }
            if (!File.Exists(Path.Combine(installRoot, "installation-receipt.json")) || !File.Exists(shortcut))
                throw new InvalidOperationException("Installation receipt or shortcut missing after child exit");
            // Do not launch the product with the installer's elevated token.
            status("Installation complete; start later through the normal shortcut");
        }
    }
}
