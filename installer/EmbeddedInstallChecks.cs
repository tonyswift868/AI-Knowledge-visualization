// Synthetic integration host, not the production installer UI.
using System;
using MentorFlow.Installation;
internal static class EmbeddedInstallChecks
{
    private static int Main(string[] args)
    {
        try
        {
            if (args.Length != 3) throw new ArgumentException("Expected isolated root, shortcut and staging parent");
            InstallFromEmbedded.Run(args[0], args[1], args[2], message => Console.WriteLine(message));
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.Message);
            return 3;
        }
    }
}
