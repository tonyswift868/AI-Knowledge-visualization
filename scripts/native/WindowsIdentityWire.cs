// Shared native boundary; FILE_ID_128 is byte order, not Guid formatting.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

internal static class WindowsIdentityWire {
    internal const string Schema = "mentorflow.win32-file-id.v1";
    internal static Dictionary<string, object> Encode(ulong volume, byte[] fileId) {
        if (fileId == null || fileId.Length != 16) throw new ArgumentException("FileId length");
        return new Dictionary<string, object> {
            {"schema", Schema}, {"volume_hex", volume.ToString("x16", CultureInfo.InvariantCulture)},
            {"file_id_le_hex", BitConverter.ToString(fileId).Replace("-", "").ToLowerInvariant()}
        };
    }
    internal static void Decode(Dictionary<string, object> doc, out ulong volume, out byte[] fileId) {
        if (doc == null || doc.Count != 3 || !doc.ContainsKey("schema") ||
            !doc.ContainsKey("volume_hex") || !doc.ContainsKey("file_id_le_hex") ||
            !(doc["schema"] is string) || (string)doc["schema"] != Schema ||
            !(doc["volume_hex"] is string) || !(doc["file_id_le_hex"] is string) ||
            !Regex.IsMatch((string)doc["volume_hex"], @"\A[0-9a-f]{16}\z") ||
            !Regex.IsMatch((string)doc["file_id_le_hex"], @"\A[0-9a-f]{32}\z"))
            throw new ArgumentException("invalid canonical Win32 identity");
        volume = UInt64.Parse((string)doc["volume_hex"], NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture);
        string bytes = (string)doc["file_id_le_hex"];
        fileId = new byte[16];
        for (int i = 0; i != 16; ++i)
            fileId[i] = Byte.Parse(bytes.Substring(i * 2, 2), NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture);
    }
}
