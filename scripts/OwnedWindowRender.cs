// Diagnostic: ask only the explicitly owned window to render into cleared memory.
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;

internal static class OwnedWindowRender
{
    [StructLayout(LayoutKind.Sequential)]
    private struct Rect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
    [DllImport("user32.dll")] private static extern bool PrintWindow(IntPtr hwnd, IntPtr dc, uint flags);

    private static int Main(string[] args)
    {
        if (args.Length != 3 || File.Exists(args[2])) return 2;
        IntPtr window = new IntPtr(long.Parse(args[0]));
        uint pid;
        GetWindowThreadProcessId(window, out pid);
        if (pid != uint.Parse(args[1])) return 3;
        Rect area;
        if (!GetWindowRect(window, out area)) return 4;
        int width = area.Right - area.Left, height = area.Bottom - area.Top;
        if (width <= 0 || height <= 0 || width > 4096 || height > 4096) return 4;
        using (var bitmap = new Bitmap(width, height, PixelFormat.Format32bppArgb))
        {
            using (var drawing = Graphics.FromImage(bitmap))
            {
                drawing.Clear(Color.Black);
                IntPtr dc = drawing.GetHdc();
                bool rendered;
                try { rendered = PrintWindow(window, dc, 0); }
                finally { drawing.ReleaseHdc(dc); }
                if (!rendered) return 5;
            }
            using (var output = new FileStream(args[2], FileMode.CreateNew))
                bitmap.Save(output, ImageFormat.Png);
        }
        return 0;
    }
}
