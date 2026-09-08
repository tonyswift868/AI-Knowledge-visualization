// Synthetic test host only. Production must compile its hash, not read argv.
using System;
using MentorFlow.Installation;
internal static class VerifiedPayloadChecks
{
    private static int Main(string[] args)
    {
        try
        {
            Console.WriteLine(VerifiedPayload.Extract("MentorFlow.Payload", args[0], args[1]).Root);
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.Message);
            return 3;
        }
    }
}
