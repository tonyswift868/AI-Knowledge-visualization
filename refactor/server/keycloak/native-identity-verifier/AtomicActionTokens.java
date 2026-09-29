package win.clannad.mentorflow;

import jakarta.ws.rs.core.Response;
import org.keycloak.authentication.actiontoken.ActionTokenContext;
import org.keycloak.authentication.actiontoken.DefaultActionToken;
import org.keycloak.common.util.Time;
import org.keycloak.events.Errors;
import org.keycloak.models.KeycloakSession;
import org.keycloak.services.ErrorPage;
import org.keycloak.services.messages.Messages;

/** Keycloak 26.7.3 action-token authority; no application token store or lock. */
final class AtomicActionTokens {
    static final String RESET_BINDING = "MF_ATOMIC_RESET_TOKEN";
    private AtomicActionTokens() { }

    private static String key(DefaultActionToken token, ActionTokenContext<?> context) {
        // Native serializeKey includes subject, signed action, expiry and verification nonce.
        // It survives the verify-email handler's confirmation-link re-serialization.
        return "mf-action-v1:" + context.getRealm().getId() + ":" + token.serializeKey();
    }

    static boolean consumed(DefaultActionToken token, ActionTokenContext<?> context) {
        String key = key(token, context);
        return context.getSession().singleUseObjects().contains(key)
            || context.getSession().revokedTokens().contains(key);
    }

    static boolean claim(DefaultActionToken token, ActionTokenContext<?> context) {
        return claim(context.getSession(), key(token, context), token.getExp());
    }

    static boolean claim(KeycloakSession session, String key, long expiration) {
        long remaining = expiration - Time.currentTime();
        if (remaining <= 0) return false;
        // Revoked-token persistence survives cache restart. This read is NOT the
        // concurrency guard: every contender must win the atomic insertion below.
        if (session.revokedTokens().contains(key)) return false;
        // Retain through expiry plus Keycloak's native action-token retention margin.
        if (!session.singleUseObjects().putIfAbsent(key, remaining + 60)) return false;
        // Only the atomic winner may write the native persistent revocation record.
        // In 26.7.3 even a losing revokedTokens.put can enlist a persistence write.
        // Never remove/release the claim on failure or re-arm.
        return session.revokedTokens().put(key, remaining + 60);
    }

    static Response denied(ActionTokenContext<?> context) {
        // Do not throw through LoginActionsService's session-removal catch: parallel
        // verification requests can share the winner's authentication session.
        context.getEvent().error(Errors.EXPIRED_CODE);
        return ErrorPage.error(context.getSession(), null, Response.Status.BAD_REQUEST, Messages.EXPIRED_ACTION);
    }
}
