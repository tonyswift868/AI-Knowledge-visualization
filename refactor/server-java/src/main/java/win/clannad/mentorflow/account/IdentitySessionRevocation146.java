package win.clannad.mentorflow.account;

/** Hook for process-owned OIDC sessions. Java currently owns no OIDC cookie store. */
public interface IdentitySessionRevocation146 {
    boolean revokeBySubject(String issuer, String subject);
}
