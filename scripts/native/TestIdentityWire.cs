// Ordinary-permission test executable, never shipped as an update helper.
using System;
using System.Collections.Generic;
using System.IO;
using System.Web.Script.Serialization;
internal class TestIdentityWire {
    static int Main() {
        var json = new JavaScriptSerializer();
        var documents = json.Deserialize<object[]>(Console.In.ReadToEnd());
        var result = new List<Dictionary<string, object>>();
        foreach (Dictionary<string, object> doc in documents) {
            ulong volume; byte[] bytes;
            WindowsIdentityWire.Decode(doc, out volume, out bytes);
            result.Add(WindowsIdentityWire.Encode(volume, bytes));
        }
        Console.Write(json.Serialize(result));
        return 0;
    }
}
