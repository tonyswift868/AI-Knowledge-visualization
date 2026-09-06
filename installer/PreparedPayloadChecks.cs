// Synthetic host: expected archive identity is compiled, not supplied on argv.
using System;
using MentorFlow.Installation;
internal static class PreparedPayloadChecks
{
    private static int Main(string[] args)
    {
        try
        {
            if (args.Length != 1) throw new ArgumentException("Expected isolated parent only");
            Console.WriteLine(VerifiedPayload.Extract("MentorFlow.Payload", ReleaseIdentity.ArchiveSha256, args[0]).Root);
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.Message);
            return 3;
        }
    }
}
