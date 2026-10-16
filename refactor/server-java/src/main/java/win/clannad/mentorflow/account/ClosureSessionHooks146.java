package win.clannad.mentorflow.account;

import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Component;
import win.clannad.mentorflow.closure.ClosureFailure;
import win.clannad.mentorflow.closure.ClosureSessionHooks;
import win.clannad.mentorflow.identity.IdentitySettings;

/** Production adapter joining account-owned limits and session revocation to closure. */
@Component
public final class ClosureSessionHooks146 implements ClosureSessionHooks {
    private final ObjectProvider<NativeSessionService> sessions;
    private final ObjectProvider<IdentitySessionRevocation146> oidcRevocation;
    private final IdentitySettings settings;

    public ClosureSessionHooks146(ObjectProvider<NativeSessionService> sessions,
            ObjectProvider<IdentitySessionRevocation146> oidcRevocation,IdentitySettings settings) {
        this.sessions=sessions;
        this.oidcRevocation=oidcRevocation;
        this.settings=settings;
    }

    @Override public void throttle(String subject,String peer) {
        AccountRateLimit146.throttleClosure(sessions.getIfAvailable(),subject,peer);
    }

    @Override public void revokeOidcSubject(String subject) {
        IdentitySessionRevocation146 revocation=oidcRevocation.getIfAvailable();
        // The account-core profile has no OIDC cookie store. When one is
        // configured, require its subject-wide revocation to confirm success.
        if(revocation==null)return;
        if(!revocation.revokeBySubject(settings.canonicalIssuer,subject))
            throw new ClosureFailure("CLOSURE_SESSION_REVOCATION_PENDING");
    }
}
