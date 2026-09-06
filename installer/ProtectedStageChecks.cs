// Synthetic test executable only. Not an installer entry or release payload.
using System;
using System.IO;
using MentorFlow.Installation;

internal static class ProtectedStageChecks
{
    private static void Reject(Action action)
    {
        try { action(); }
        catch (Exception) { return; }
        throw new Exception("Expected rejection did not occur");
    }
    private static int Main(string[] args)
    {
        try
        {
            ProtectedStage stage = ProtectedStage.Create(args[0]);
            byte[] value = new byte[] { 1, 2, 3 };
            string good = stage.WriteNewFile("nested/ok.bin", new MemoryStream(value), 3);
            Reject(() => stage.WriteNewFile("nested/ok.bin", new MemoryStream(new byte[] { 9 }), 1));
            if (File.ReadAllBytes(good)[0] != 1) throw new Exception("Existing file changed");
            foreach (string path in new string[] { "../x", "/x", "C:/x", "x\\y", "x:stream", "x//y", "x/./y", "x/../y", "CON.txt", "NUL", "COM1.dll", "LPT².txt", "x.", "x ", "x/", "" })
                Reject(() => stage.WriteNewFile(path, new MemoryStream(value), 3));
            Reject(() => stage.WriteNewFile("short.bin", new MemoryStream(value), 4));
            Reject(() => stage.WriteNewFile("long.bin", new MemoryStream(value), 2));
            stage.WriteNewFile("deep/" + new String('a', 120) + "/" + new String('b', 120) + "/ok.bin", new MemoryStream(value), 3);
            stage.WriteNewFile("zero.bin", new MemoryStream(new byte[0]), 0);
            byte[] blocks = new byte[131089];
            for (int index = 0; index < blocks.Length; index++) blocks[index] = (byte)(index % 251);
            stage.WriteNewFile("blocks.bin", new MemoryStream(blocks), blocks.Length);
            Console.WriteLine(stage.Root);
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.Message);
            return 3;
        }
    }
}
