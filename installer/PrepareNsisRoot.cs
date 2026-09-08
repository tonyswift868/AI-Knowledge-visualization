// Small windowed NSIS helper; inherits installer elevation, never elevates itself.
using System;
namespace MentorFlow.Installation
{
    internal static class PrepareNsisRoot
    {
        private static int Main(string[] args)
        {
            if (args.Length != 0) return 2;
            try { ProtectedStage.PrepareUatRoot(); return 0; }
            catch { return 3; } // NSIS owns visible error handling; no console.
        }
    }
}
