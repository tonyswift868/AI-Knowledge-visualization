package win.clannad.mentorflow;

import jakarta.ws.rs.core.Response;
import org.keycloak.authentication.actiontoken.ActionTokenContext;
import org.keycloak.authentication.actiontoken.execactions.ExecuteActionsActionToken;
import org.keycloak.authentication.actiontoken.execactions.ExecuteActionsActionTokenHandler;

/** Observe the already verified official action; preserve all upstream handling. */
public final class ObservedExecuteActions extends ExecuteActionsActionTokenHandler {
    @Override public int order() { return 100; }
    @Override public Response handleToken(ExecuteActionsActionToken token, ActionTokenContext<ExecuteActionsActionToken> context) {
        if (!context.isAuthenticationSessionFresh())
            ResetCompletions.bindExecute(context.getSession(), context.getAuthenticationSession(), token);
        return super.handleToken(token, context);
    }
}
