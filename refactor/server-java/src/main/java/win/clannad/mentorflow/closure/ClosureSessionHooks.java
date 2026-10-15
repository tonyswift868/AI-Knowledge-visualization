package win.clannad.mentorflow.closure;

/** Account runtime integration: use the existing shared counters and cookie store. */
public interface ClosureSessionHooks {
    void throttle(String subject,String peer);
    void revokeOidcSubject(String subject);
}
