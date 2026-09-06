// Installer-only staging primitive. No payload execution or receipt issuance.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text.RegularExpressions;
using Microsoft.Win32.SafeHandles;

namespace MentorFlow.Installation
{
    internal sealed class ProtectedStage
    {
        private const string Policy = "O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)";
        private readonly HashSet<string> directories = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        internal string Root { get; private set; }

        [StructLayout(LayoutKind.Sequential)]
        private struct Attributes
        {
            internal int Length;
            internal IntPtr Descriptor;
            [MarshalAs(UnmanagedType.Bool)] internal bool Inherit;
        }
        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string text, uint revision, out IntPtr descriptor, IntPtr length);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CreateDirectoryW(string path, ref Attributes attributes);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFileW(string path, uint access, uint share, ref Attributes attributes, uint disposition, uint flags, IntPtr template);
        [DllImport("kernel32.dll")]
        private static extern IntPtr LocalFree(IntPtr memory);

        private sealed class Descriptor : IDisposable
        {
            internal Attributes Value;
            internal Descriptor()
            {
                IntPtr pointer;
                if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(Policy, 1, out pointer, IntPtr.Zero))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                Value = new Attributes { Length = Marshal.SizeOf(typeof(Attributes)), Descriptor = pointer, Inherit = false };
            }
            public void Dispose()
            {
                if (Value.Descriptor != IntPtr.Zero) LocalFree(Value.Descriptor);
                Value.Descriptor = IntPtr.Zero;
            }
        }

        private static bool Privileged(string sid)
        {
            return sid == "S-1-5-18" || sid == "S-1-5-32-544";
        }

        internal static void AssertSafeParents(string parent)
        {
            string current = Path.GetFullPath(parent);
            string volume = Path.GetPathRoot(current);
            if (volume.Length != 3 || new DriveInfo(volume).DriveFormat != "NTFS")
                throw new InvalidOperationException("Local NTFS volume required");
            while (true)
            {
                if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidOperationException("Reparse parent rejected");
                DirectorySecurity security = Directory.GetAccessControl(current, AccessControlSections.Owner | AccessControlSections.Access);
                string owner = security.GetOwner(typeof(SecurityIdentifier)).Value;
                if (!Privileged(owner) && !Regex.IsMatch(owner, @"^S-1-5-21-(\d+-){3}500$"))
                    throw new InvalidOperationException("Ordinary-user-owned parent rejected");
                bool isVolume = String.Equals(current.TrimEnd('\\'), volume.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase);
                uint unsafeRights = 0x100c0040U | (isVolume ? 0U : 0x10000U);
                AuthorizationRuleCollection rules = security.GetAccessRules(true, true, typeof(SecurityIdentifier));
                if (rules.Count == 0) throw new InvalidOperationException("Missing parent ACL rejected");
                foreach (FileSystemAccessRule rule in rules)
                {
                    if (rule.AccessControlType != AccessControlType.Allow)
                        throw new InvalidOperationException("Unsupported parent ACL rejected");
                    bool effective = (rule.PropagationFlags & PropagationFlags.InheritOnly) == 0;
                    uint mask = unchecked((uint)(int)rule.FileSystemRights);
                    if (effective && !Privileged(rule.IdentityReference.Value) && (mask & unsafeRights) != 0)
                        throw new InvalidOperationException("Parent permits ordinary-user replacement");
                }
                if (isVolume) break;
                current = Path.GetDirectoryName(current);
            }
        }

        internal static ProtectedStage Create(string parent)
        {
            if (!new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator))
                throw new InvalidOperationException("Elevated installation required");
            parent = Path.GetFullPath(parent);
            AssertSafeParents(parent);
            ProtectedStage stage = new ProtectedStage();
            stage.Root = Path.Combine(parent, "MentorFlow-stage-" + Guid.NewGuid().ToString("N"));
            stage.CreateDirectory(stage.Root);
            return stage;
        }

        internal static void PrepareUatRoot()
        {
            if (!new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator))
                throw new InvalidOperationException("Elevated installation required");
            const string root = @"E:\MentorFlow";
            AssertSafeParents(@"E:\");
            if (Directory.Exists(root) || File.Exists(root))
                throw new InvalidOperationException("Existing installation preserved; repair requires separate approval");
            ProtectedStage stage = new ProtectedStage();
            stage.Root = root;
            stage.CreateDirectory(root);
            stage.CreateDirectory(Path.Combine(root, "App"));
        }

        private void CreateDirectory(string path)
        {
            using (Descriptor descriptor = new Descriptor())
            {
                // Existing entries are errors, never adopted or repaired.
                if (!CreateDirectoryW(@"\\?\" + path, ref descriptor.Value))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            directories.Add(path);
        }

        internal static string[] ValidateRelative(string relative)
        {
            if (String.IsNullOrEmpty(relative) || relative.IndexOf('\\') >= 0 || relative.IndexOf(':') >= 0)
                throw new InvalidOperationException("Noncanonical staged path");
            string[] parts = relative.Split('/');
            foreach (string part in parts)
            {
                if (part.Length == 0 || part.Length > 255 || part == "." || part == ".." || part.EndsWith(".") || part.EndsWith(" ") ||
                    part.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 ||
                    Regex.IsMatch(part, @"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", RegexOptions.IgnoreCase))
                    throw new InvalidOperationException("Unsafe staged path component");
            }
            return parts;
        }

        internal void EnsureSubdirectory(string relative)
        {
            string parent = Root;
            foreach (string part in ValidateRelative(relative))
            {
                parent = Path.Combine(parent, part);
                if (parent.Length >= 32760) throw new InvalidOperationException("Staged path too long");
                if (!directories.Contains(parent)) CreateDirectory(parent);
            }
        }

        internal string WriteNewFile(string relative, Stream incoming, long expectedLength)
        {
            if (expectedLength < 0 || expectedLength > 8L * 1024 * 1024 * 1024)
                throw new InvalidOperationException("Invalid staged file length");
            string[] parts = ValidateRelative(relative);
            string destination = Path.Combine(Root, String.Join(Path.DirectorySeparatorChar.ToString(), parts));
            // Canonical validated names use extended Win32 paths; no dependence
            // on machine-wide long-path policy or .NET path-based FileStream.
            if (destination.Length >= 32760) throw new InvalidOperationException("Staged path too long");
            string parent = Root;
            for (int index = 0; index < parts.Length - 1; index++)
            {
                parent = Path.Combine(parent, parts[index]);
                if (!directories.Contains(parent)) CreateDirectory(parent);
            }
            using (Descriptor descriptor = new Descriptor())
            using (SafeFileHandle handle = CreateFileW(@"\\?\" + destination, 0xc0000000U, 0, ref descriptor.Value, 1, 0x80, IntPtr.Zero))
            {
                if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
                using (FileStream outgoing = new FileStream(handle, FileAccess.ReadWrite, 65536, false))
                using (SHA256 inputHash = SHA256.Create())
                {
                    byte[] buffer = new byte[65536];
                    long remaining = expectedLength;
                    while (remaining > 0)
                    {
                        int count = incoming.Read(buffer, 0, (int)Math.Min(buffer.Length, remaining));
                        if (count == 0) throw new InvalidDataException("Truncated staged file");
                        inputHash.TransformBlock(buffer, 0, count, buffer, 0);
                        outgoing.Write(buffer, 0, count);
                        remaining -= count;
                    }
                    if (incoming.ReadByte() != -1) throw new InvalidDataException("Staged file exceeds declared length");
                    inputHash.TransformFinalBlock(new byte[0], 0, 0);
                    outgoing.Flush(true);
                    // Verify bytes through the same exclusive handle before
                    // releasing the file or returning it to the bootstrap.
                    outgoing.Position = 0;
                    using (SHA256 storedHash = SHA256.Create())
                    {
                        byte[] stored = storedHash.ComputeHash(outgoing);
                        byte[] expected = inputHash.Hash;
                        for (int index = 0; index < expected.Length; index++)
                            if (stored[index] != expected[index])
                                throw new InvalidDataException("Staged file readback SHA-256 mismatch");
                    }
                }
            }
            return destination;
        }
    }
}
