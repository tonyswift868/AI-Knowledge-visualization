package win.clannad.mentorflow.admin;

public final class AdminAccountMask388 {
    public static void main(String[] args) {
        check("ab***@g***.com", AdminOverviewService.maskEmail("abigail@gmail.com"));
        check("a***@e***.uk", AdminOverviewService.maskEmail("a@example.co.uk"));
        check("lo***@s***.org", AdminOverviewService.maskEmail("long.name@sub.domain.org"));
        check("q***@z***.io", AdminOverviewService.maskEmail("q@z.io"));
        check("éc***@例***.测试", AdminOverviewService.maskEmail("éclair@例子.测试"));
        check("😀b***@e***.com", AdminOverviewService.maskEmail("😀b@example.com"));
        check("取不到", AdminOverviewService.maskEmail("not-an-email"));
        check("取不到", AdminOverviewService.maskEmail("@example.com"));
        check("subject-", AdminOverviewService.accountLabel(null, "subject-1234"));
        check("subject-", AdminOverviewService.accountLabel("", "subject-1234"));
        check("id", AdminOverviewService.accountLabel(" ", "id"));
        check("", AdminOverviewService.readerConfigPath("", "lifecycle.json", false, true));
        check("", AdminOverviewService.readerConfigPath("", "lifecycle.json", true, false));
        check("lifecycle.json", AdminOverviewService.readerConfigPath("", "lifecycle.json", true, true));
        check("admin.json", AdminOverviewService.readerConfigPath("admin.json", "lifecycle.json", false, false));
        var lifecycleFields = java.util.Set.of("origin", "realm", "client_id", "secret_file", "ca_file",
                "candidate_sha", "expires_at", "client_cert_file", "client_key_file");
        if (!AdminOverviewService.validReaderConfigFields(lifecycleFields, true))
            throw new AssertionError("existing lifecycle config fields rejected");
        if (AdminOverviewService.validReaderConfigFields(lifecycleFields, false)
                || AdminOverviewService.validReaderConfigFields(java.util.Set.of("origin", "realm", "client_id",
                    "secret_file", "ca_file", "unknown"), true))
            throw new AssertionError("unknown config field accepted");
        var internalAdminOrigin = java.net.URI.create("https://keycloak-admin.internal/");
        var publicIssuer = java.net.URI.create("https://auth.example");
        var publicIssuerRealm = java.net.URI.create("https://auth.example/realms/mentorflow");
        if (!AdminOverviewService.validReaderOrigin(internalAdminOrigin, "mentorflow", "reader",
                publicIssuerRealm, true)
                || AdminOverviewService.validReaderOrigin(internalAdminOrigin, "mentorflow", "reader",
                    publicIssuerRealm, false)
                || !AdminOverviewService.validReaderOrigin(publicIssuer, "mentorflow", "reader",
                    publicIssuerRealm, false)
                || AdminOverviewService.validReaderOrigin(java.net.URI.create("http://keycloak-admin.internal/"),
                    "mentorflow", "reader", publicIssuerRealm, true))
            throw new AssertionError("lifecycle HTTPS origin binding mismatch");
        var now = java.time.Instant.parse("2026-11-12T00:00:00Z");
        String candidate = "a".repeat(40);
        if (!AdminOverviewService.validLifecycleAuthority(candidate, candidate, true,
                "2026-11-18T00:00:00Z", now)
                || AdminOverviewService.validLifecycleAuthority(candidate, "b".repeat(40), false, null, now)
                || AdminOverviewService.validLifecycleAuthority("invalid", "invalid", false, null, now)
                || AdminOverviewService.validLifecycleAuthority("", "", true, null, now)
                || AdminOverviewService.validLifecycleAuthority("", "", false,
                    "2026-11-21T00:00:00Z", now)
                || AdminOverviewService.validLifecycleAuthority("", "", false,
                    "2026-11-11T00:00:00Z", now))
            throw new AssertionError("lifecycle candidate or expiry validation mismatch");
    }

    private static void check(String expected, String actual) {
        if (!expected.equals(actual)) throw new AssertionError("email mask mismatch");
    }
}
