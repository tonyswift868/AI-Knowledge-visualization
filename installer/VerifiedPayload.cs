// Outer-installer component. Expected hash must come from the trusted compiled
// installer, never from a sidecar next to a user-writable archive.
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.RegularExpressions;

namespace MentorFlow.Installation
{
    internal static class VerifiedPayload
    {
        internal static ProtectedStage Extract(string resourceName, string expectedHash, string parent)
        {
            // Only this compiled assembly's embedded resource is accepted; no
            // mutable external archive or caller-supplied stream after hashing.
            using (Stream resource = Assembly.GetExecutingAssembly().GetManifestResourceStream(resourceName))
            {
                if (resource == null) throw new InvalidDataException("Embedded payload missing");
                return ExtractResource(resource, expectedHash, parent);
            }
        }

        private static ProtectedStage ExtractResource(Stream embeddedArchive, string expectedHash, string parent)
        {
            const long MaxBytes = 8L * 1024 * 1024 * 1024;
            if (!embeddedArchive.CanSeek || !embeddedArchive.CanRead || embeddedArchive.Length > MaxBytes ||
                expectedHash == null || !Regex.IsMatch(expectedHash, "^[0-9a-f]{64}$"))
                throw new InvalidDataException("Invalid embedded payload identity or stream");
            embeddedArchive.Position = 0;
            string actual;
            using (SHA256 hash = SHA256.Create())
                actual = BitConverter.ToString(hash.ComputeHash(embeddedArchive)).Replace("-", "").ToLowerInvariant();
            if (!String.Equals(actual, expectedHash, StringComparison.Ordinal))
                throw new InvalidDataException("Embedded payload SHA-256 mismatch");
            embeddedArchive.Position = 0;
            using (ZipArchive archive = new ZipArchive(embeddedArchive, ZipArchiveMode.Read, true))
            {
                if (archive.Entries.Count == 0 || archive.Entries.Count > 50000)
                    throw new InvalidDataException("Invalid archive entry count");
                HashSet<string> names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                HashSet<string> files = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                long total = 0;
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    bool directory = entry.FullName.EndsWith("/", StringComparison.Ordinal);
                    string name = directory ? entry.FullName.Substring(0, entry.FullName.Length - 1) : entry.FullName;
                    ProtectedStage.ValidateRelative(name);
                    if (!names.Add(name)) throw new InvalidDataException("Duplicate archive path");
                    int unixType = (entry.ExternalAttributes >> 16) & 0xf000;
                    if ((entry.ExternalAttributes & 0x400) != 0 ||
                        (unixType != 0 && unixType != (directory ? 0x4000 : 0x8000)))
                        throw new InvalidDataException("Archive link or special file rejected");
                    if (directory && entry.Length != 0) throw new InvalidDataException("Directory has payload bytes");
                    total = checked(total + entry.Length);
                    if (entry.Length < 0 || total > MaxBytes) throw new InvalidDataException("Archive size limit exceeded");
                    if (!directory) files.Add(name);
                }
                foreach (string name in names)
                {
                    int slash = name.IndexOf('/');
                    while (slash >= 0)
                    {
                        if (files.Contains(name.Substring(0, slash)))
                            throw new InvalidDataException("Archive file/directory conflict");
                        slash = name.IndexOf('/', slash + 1);
                    }
                }
                ProtectedStage stage = ProtectedStage.Create(parent);
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    if (entry.FullName.EndsWith("/", StringComparison.Ordinal))
                        stage.EnsureSubdirectory(entry.FullName.Substring(0, entry.FullName.Length - 1));
                    else
                        using (Stream incoming = entry.Open())
                            stage.WriteNewFile(entry.FullName, incoming, entry.Length);
                }
                return stage;
            }
        }
    }
}
