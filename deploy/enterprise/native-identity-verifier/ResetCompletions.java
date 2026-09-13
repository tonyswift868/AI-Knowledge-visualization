package win.clannad.mentorflow;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.*;
import org.keycloak.authentication.RequiredActionContext;
import org.keycloak.authentication.actiontoken.resetcred.ResetCredentialsActionToken;
import org.keycloak.common.util.Time;
import org.keycloak.models.*;
import org.keycloak.models.utils.KeycloakModelUtils;
import org.keycloak.sessions.AuthenticationSessionModel;
import org.keycloak.sessions.AuthenticationSessionCompoundId;

/** Positive facts are published only by the password authority's after-commit seam.
 * Cache loss fails closed. This is a bounded observation lease, not a login credential.
 */
final class ResetCompletions {
    private static final String NOTE = "MF_RESET_COMPLETION_BINDING";
    private static final int TTL = 900, RETENTION = 3600;
    private static final SecureRandom RANDOM = new SecureRandom();
    static String hash(String value) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))); }
        catch (Exception impossible) { throw new IllegalStateException(); }
    }
    private static String prefix(RealmModel realm) { return "mf-reset-v1:" + realm.getId() + ":"; }
    static Map<String,String> watch(KeycloakSession session, String state) {
        if (state == null || !state.matches("[A-Za-z0-9_-]{43,128}")) throw new IllegalArgumentException();
        var store = session.singleUseObjects();
        String p = prefix(session.getContext().getRealm()), correlation = hash(state);
        // One registration per OIDC transaction. Reusing state never creates a new observer.
        if (!store.putIfAbsent(p + "reserved:" + correlation, TTL + RETENTION)) throw new IllegalArgumentException();
        byte[] bytes = new byte[32]; RANDOM.nextBytes(bytes);
        String ticket = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
        var row = Map.of("ticket", hash(ticket), "correlation", correlation,
            "issued", Integer.toString(Time.currentTime()), "expires", Integer.toString(Time.currentTime() + TTL));
        store.put(p + "watch:" + correlation, TTL + RETENTION, row);
        store.put(p + "ticket:" + hash(ticket), TTL + RETENTION, row);
        return response(ticket, "PENDING");
    }
    static Map<String,String> response(String ticket, String status) {
        return Map.of("schema", "mentorflow.reset-completion.v1", "transaction_id", ticket, "status", status);
    }
    static Map<String,String> status(KeycloakSession session, String ticket) {
        if (ticket == null || !ticket.matches("[A-Za-z0-9_-]{43}")) throw new IllegalArgumentException();
        String p = prefix(session.getContext().getRealm());
        var row = session.singleUseObjects().get(p + "ticket:" + hash(ticket));
        if (row == null) return response(ticket, "INVALID");
        // A committed fact remains stable for the observation retention, even after action expiry.
        if (session.singleUseObjects().contains(p + "completed:" + hash(ticket))) return response(ticket, "COMPLETED");
        return response(ticket, Integer.parseInt(row.get("expires")) <= Time.currentTime() ? "EXPIRED" : "PENDING");
    }
    static void bind(KeycloakSession session, AuthenticationSessionModel auth, ResetCredentialsActionToken token) {
        // Email links can open in a fresh browser. Read correlation ONLY from the
        // original authentication session referenced by the verified signed token.
        // Never use a state supplied on the redemption URL/new browser session.
        if (token.getCompoundAuthenticationSessionId() == null) return;
        var compound = AuthenticationSessionCompoundId.encoded(token.getCompoundAuthenticationSessionId());
        var realm = session.getContext().getRealm();
        var client = realm.getClientByClientId("mentorflow-desktop");
        if (client == null || !client.getId().equals(compound.getClientUUID())) return;
        var root = session.authenticationSessions().getRootAuthenticationSession(realm, compound.getRootSessionId());
        var original = root == null ? null : root.getAuthenticationSession(client, compound.getTabId());
        if (original == null || original.getAuthenticatedUser() == null
                || !token.getUserId().equals(original.getAuthenticatedUser().getId())) return;
        String state = original.getClientNote("state");
        if (state == null || !"mentorflow-desktop".equals(auth.getClient().getClientId())) return;
        String p = prefix(session.getContext().getRealm()), correlation = hash(state);
        var store = session.singleUseObjects();
        var row = store.get(p + "watch:" + correlation);
        if (row == null || Integer.parseInt(row.get("expires")) <= Time.currentTime()
                || token.getIat() == null || token.getIat() < Long.parseLong(row.get("issued"))) return;
        String key = p + "binding:" + row.get("ticket");
        if (!store.putIfAbsent(key + ":claimed", TTL + RETENTION)) return;
        String binding = hash(token.serializeKey());
        store.put(key, TTL + RETENTION, Map.of("action", binding, "subject", hash(token.getUserId()),
            "expires", Integer.toString(Math.min(token.getExp().intValue(), Integer.parseInt(row.get("expires"))))));
        auth.setAuthNote(NOTE, row.get("ticket"));
    }
    static void afterPasswordSuccess(RequiredActionContext context) {
        var auth = context.getAuthenticationSession();
        String ticketHash = auth.getAuthNote(NOTE), action = auth.getAuthNote(AtomicActionTokens.RESET_BINDING);
        if (ticketHash == null || action == null || context.getStatus() != RequiredActionContext.Status.SUCCESS) return;
        var session = context.getSession();
        String p = prefix(context.getRealm());
        var row = session.singleUseObjects().get(p + "binding:" + ticketHash);
        if (row == null || !hash(action).equals(row.get("action")) || !hash(context.getUser().getId()).equals(row.get("subject"))
                || Integer.parseInt(row.get("expires")) <= Time.currentTime()) return;
        int expires = Integer.parseInt(row.get("expires"));
        var factory = session.getKeycloakSessionFactory();
        // SUCCESS and UPDATE_CREDENTIAL events occur BEFORE the enclosing JPA commit.
        // Do not publish in a listener's onEvent or from a response/redirect handler.
        session.getTransactionManager().enlistAfterCompletion(new AbstractKeycloakTransaction() {
            protected void commitImpl() {
                if (Time.currentTime() >= expires) return;
                KeycloakModelUtils.runJobInTransaction(factory, fresh ->
                    fresh.singleUseObjects().putIfAbsent(p + "completed:" + ticketHash, TTL + RETENTION));
            }
            protected void rollbackImpl() { /* Deliberately no positive fact. */ }
        });
    }
}
