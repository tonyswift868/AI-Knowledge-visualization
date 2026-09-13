package win.clannad.mentorflow.lab;

import java.util.*;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.*;
import org.keycloak.Config;
import org.keycloak.events.*;
import org.keycloak.events.admin.AdminEvent;
import org.keycloak.models.*;

/** Fixture-only observation. Records allowlisted metadata, never event detail values. */
public final class ResetEventFixtureObserver implements EventListenerProviderFactory {
    static final List<Map<String,Object>> observations = new CopyOnWriteArrayList<>();
    static final Map<String,CountDownLatch> paused = new ConcurrentHashMap<>();
    public String getId() { return "mf-fixture-reset-observer"; }
    public void init(Config.Scope config) { }
    public void postInit(KeycloakSessionFactory factory) { }
    public void close() { observations.clear(); }
    public EventListenerProvider create(KeycloakSession session) {
        return new EventListenerProvider() {
            public void close() { }
            public void onEvent(AdminEvent event, boolean includeRepresentation) { }
            public void onEvent(Event event) {
                // Phase-1 discovery observed both native password event families.
                // Do not load users on unrelated loser/error events: that changes
                // the connection demand of the concurrency fixture itself.
                if (!Set.of("UPDATE_PASSWORD", "UPDATE_PASSWORD_ERROR", "UPDATE_CREDENTIAL", "UPDATE_CREDENTIAL_ERROR").contains(event.getType().name())) return;
                var realm = session.getContext().getRealm();
                if (realm == null || !realm.getName().startsWith("mf-native-") || event.getUserId() == null) return;
                var user = session.users().getUserById(realm, event.getUserId());
                if (user == null || !user.getUsername().startsWith("action-")) return;
                Map<String,Object> row = new LinkedHashMap<>();
                row.put("type", event.getType().name());
                row.put("user_id", event.getUserId());
                row.put("client_id", Objects.toString(event.getClientId(), ""));
                row.put("session_present", event.getSessionId() != null);
                row.put("error", Objects.toString(event.getError(), ""));
                row.put("detail_keys", event.getDetails() == null ? List.of() : new ArrayList<>(event.getDetails().keySet()));
                row.put("credential_type", event.getDetails() == null ? "" : Objects.toString(event.getDetails().get("credential_type"), ""));
                row.put("required_actions_at_event", user.getRequiredActionsStream().toList());
                var auth = session.getContext().getAuthenticationSession();
                row.put("authentication_required_actions_at_event", auth == null ? List.of() : new ArrayList<>(auth.getRequiredActions()));
                row.put("transaction_active_at_event", session.getTransactionManager().isActive());
                row.put("phase", "EVENT_BEFORE_COMMIT");
                observations.add(row);
                if ("UPDATE_CREDENTIAL".equals(event.getType().name()) && event.getError() == null) {
                    if ("true".equals(user.getFirstAttribute("mf-fixture-rollback"))) session.getTransactionManager().setRollbackOnly();
                    if ("true".equals(user.getFirstAttribute("mf-fixture-fail-commit"))) {
                        session.getTransactionManager().enlistPrepare(new AbstractKeycloakTransaction() {
                            protected void commitImpl() { throw new IllegalStateException("Fixture forced prepare failure"); }
                            protected void rollbackImpl() { }
                        });
                    }
                    if ("true".equals(user.getFirstAttribute("mf-fixture-pause-commit"))) {
                        var latch = new CountDownLatch(1); paused.put(user.getId(), latch);
                        session.getTransactionManager().enlistPrepare(new AbstractKeycloakTransaction() {
                            protected void commitImpl() {
                                try { if (!latch.await(20, TimeUnit.SECONDS)) throw new IllegalStateException("Fixture commit gate timed out"); }
                                catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); throw new IllegalStateException(); }
                            }
                            protected void rollbackImpl() { }
                        });
                    }
                }
                session.getTransactionManager().enlistAfterCompletion(new AbstractKeycloakTransaction() {
                    protected void commitImpl() {
                        var copy = new LinkedHashMap<>(row); copy.put("phase", "AFTER_COMMIT");
                        copy.put("authentication_required_actions_after_completion", auth == null ? List.of() : new ArrayList<>(auth.getRequiredActions()));
                        observations.add(copy);
                    }
                    protected void rollbackImpl() { var copy = new LinkedHashMap<>(row); copy.put("phase", "AFTER_ROLLBACK"); observations.add(copy); }
                });
            }
        };
    }
}
