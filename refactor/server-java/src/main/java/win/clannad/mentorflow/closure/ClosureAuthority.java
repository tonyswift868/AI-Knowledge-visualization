package win.clannad.mentorflow.closure;

/** Narrow adapter to the already configured native identity/session services. */
public interface ClosureAuthority {
    /** The exact account lifecycle authority must still be active. */
    default boolean active() { return false; }

    /** Shared native login throttle; implementations without the outward seam fail closed. */
    default void throttle(String subject, String peer) { throw new ClosureFailure("CLOSURE_UNAVAILABLE"); }

    /** Reauthenticate and require the returned issuer/subject to equal the request principal. */
    void verifyCurrentPassword(String issuer, String subject, String password);

    /** Attempt native and OIDC revocation independently; return {native, oidc}. */
    Revocation revokeLocal(String subject);

    record Revocation(boolean nativeSessions, boolean oidcSessions) {
        public boolean complete() { return nativeSessions && oidcSessions; }
    }
}
