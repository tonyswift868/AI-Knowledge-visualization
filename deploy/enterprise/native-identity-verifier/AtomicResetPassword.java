package win.clannad.mentorflow;

import jakarta.ws.rs.core.Response;
import org.keycloak.authentication.RequiredActionContext;
import org.keycloak.authentication.RequiredActionProvider;
import org.keycloak.authentication.requiredactions.UpdatePassword;
import org.keycloak.common.util.Time;
import org.keycloak.events.Errors;
import org.keycloak.events.EventType;
import org.keycloak.models.DefaultActionTokenKey;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.UserModel;
import org.keycloak.policy.PasswordPolicyManagerProvider;
import org.keycloak.services.ErrorPage;
import org.keycloak.services.messages.Messages;
import org.keycloak.services.validation.Validation;

/** The native UPDATE_PASSWORD action, guarded only for an atomically claimed reset link. */
public final class AtomicResetPassword extends UpdatePassword {
    @Override public int order() { return 100; }
    @Override public RequiredActionProvider create(KeycloakSession session) { return new AtomicResetPassword(); }

    private String key(RequiredActionContext context) {
        String binding = context.getAuthenticationSession().getAuthNote(AtomicActionTokens.RESET_BINDING);
        return binding == null ? null : "mf-action-complete-v1:" + context.getRealm().getId() + ":" + binding;
    }

    private void denied(RequiredActionContext context) {
        context.getEvent().error(Errors.EXPIRED_CODE);
        context.challenge(ErrorPage.error(context.getSession(), null, Response.Status.BAD_REQUEST, Messages.EXPIRED_ACTION));
    }

    private boolean consumed(RequiredActionContext context, String key) {
        return context.getSession().singleUseObjects().contains(key) || context.getSession().revokedTokens().contains(key);
    }

    @Override
    public void requiredActionChallenge(RequiredActionContext context) {
        String key = key(context);
        if (key != null && consumed(context, key)) { denied(context); return; }
        super.requiredActionChallenge(context);
    }

    @Override
    public void processAction(RequiredActionContext context) {
        String key = key(context);
        if (key == null) { super.processAction(context); return; }
        var token = DefaultActionTokenKey.from(context.getAuthenticationSession().getAuthNote(AtomicActionTokens.RESET_BINDING));
        if (token == null || !"reset-credentials".equals(token.getActionId())
                || !context.getUser().getId().equals(token.getUserId()) || token.getExp() <= Time.currentTime()
                || consumed(context, key)) { denied(context); return; }
        var form = context.getHttpRequest().getDecodedFormParameters();
        String password = form.getFirst("password-new");
        // Native validation failures have no credential write. Preserve ordinary
        // missing/mismatch/policy feedback and a corrected submission on this form.
        if (Validation.isBlank(password) || !password.equals(form.getFirst("password-confirm"))) {
            super.processAction(context);
            return;
        }
        var policyError = context.getSession().getProvider(PasswordPolicyManagerProvider.class)
            .validate(context.getRealm(), context.getUser(), password);
        if (policyError != null) {
            // Do not call the mutation path to render a policy error: a concurrent
            // policy change must never turn that second validation into an unclaimed write.
            context.getEvent().clone().event(EventType.UPDATE_PASSWORD_ERROR).error(Errors.PASSWORD_REJECTED);
            context.challenge(context.form().setAttribute("username", context.getUser().getUsername())
                .setError(policyError.getMessage(), policyError.getParameters()).createResponse(UserModel.RequiredAction.UPDATE_PASSWORD));
            return;
        }
        if (!AtomicActionTokens.claim(context.getSession(), key, token.getExp())) { denied(context); return; }
        super.processAction(context);
        // An unexpected provider failure must not re-arm a claimed credential write.
        if (context.getStatus() != RequiredActionContext.Status.SUCCESS) denied(context);
    }
}
