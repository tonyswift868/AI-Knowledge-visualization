package win.clannad.mentorflow;

import jakarta.ws.rs.core.Response;
import org.keycloak.authentication.actiontoken.ActionTokenContext;
import org.keycloak.authentication.actiontoken.resetcred.ResetCredentialsActionToken;
import org.keycloak.authentication.actiontoken.resetcred.ResetCredentialsActionTokenHandler;
import org.keycloak.TokenVerifier.Predicate;
import org.keycloak.services.resources.LoginActionsServiceChecks.IsActionRequired;
import org.keycloak.services.resources.LoginActionsServiceException;
import org.keycloak.sessions.CommonClientSessionModel.Action;

/** Claim before the native reset flow can mutate credentials or required actions. */
public final class AtomicResetCredentialsActionTokenHandler extends ResetCredentialsActionTokenHandler {
    @Override public int order() { return 100; }

    @Override
    public Predicate<? super ResetCredentialsActionToken>[] getVerifiers(ActionTokenContext<ResetCredentialsActionToken> context) {
        var checks = super.getVerifiers(context);
        for (int i = 0; i < checks.length; i++) {
            if (checks[i] instanceof IsActionRequired) {
                // The stock verifier can resume required actions before handleToken.
                // A signed reset link must instead pass the atomic claim below.
                checks[i] = token -> {
                    if (!Action.AUTHENTICATE.name().equals(context.getAuthenticationSession().getAction())) {
                        throw new LoginActionsServiceException(AtomicActionTokens.denied(context));
                    }
                    return true;
                };
            }
        }
        return checks;
    }

    @Override
    @SuppressWarnings("rawtypes")
    public Response handleToken(ResetCredentialsActionToken token, ActionTokenContext context) {
        if (!AtomicActionTokens.claim(token, context)) return AtomicActionTokens.denied(context);
        context.getAuthenticationSession().setAuthNote(AtomicActionTokens.RESET_BINDING, token.serializeKey());
        // Preserve native form/session-code validation, password policy and final expiry
        // and invalidation checks. Only one token redemption can reach this flow.
        return super.handleToken(token, context);
    }
}
