package win.clannad.mentorflow;

import jakarta.ws.rs.core.Response;
import org.keycloak.authentication.actiontoken.ActionTokenContext;
import org.keycloak.authentication.actiontoken.verifyemail.VerifyEmailActionToken;
import org.keycloak.authentication.actiontoken.verifyemail.VerifyEmailActionTokenHandler;

/** Same signed token type and native verifiers; claim before email/required-action writes. */
public final class AtomicVerifyEmailActionTokenHandler extends VerifyEmailActionTokenHandler {
    @Override public int order() { return 100; }

    @Override
    public Response handleToken(VerifyEmailActionToken token, ActionTokenContext<VerifyEmailActionToken> context) {
        if (AtomicActionTokens.consumed(token, context)) return AtomicActionTokens.denied(context);
        // A fresh browser receives a confirmation link, not a successful verification.
        if (!context.isAuthenticationSessionFresh() && !AtomicActionTokens.claim(token, context)) {
            return AtomicActionTokens.denied(context);
        }
        return super.handleToken(token, context);
    }
}
