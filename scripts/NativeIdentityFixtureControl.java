package win.clannad.mentorflow.lab;

import jakarta.ws.rs.*;
import java.security.MessageDigest;
import java.util.*;
import org.keycloak.Config;
import org.keycloak.models.*;
import org.keycloak.services.resource.*;
import org.keycloak.common.util.Time;
import org.keycloak.authentication.actiontoken.*;
import org.keycloak.authentication.actiontoken.verifyemail.*;
import org.keycloak.authentication.actiontoken.resetcred.*;
import org.keycloak.events.*;
import org.keycloak.sessions.*;

/** Disposable fixture control only. Never packaged in the deployable verifier JAR. */
public final class NativeIdentityFixtureControl implements RealmResourceProviderFactory {
    public String getId() { return "mf-native-fixture-control"; }
    public void init(Config.Scope scope) { }
    public void postInit(KeycloakSessionFactory factory) { }
    public void close() { }
    public RealmResourceProvider create(KeycloakSession session) {
        return new RealmResourceProvider() {
            public Object getResource() { return new Resource(session); }
            public void close() { }
        };
    }
    @Path("")
    public static final class Resource {
        private final KeycloakSession session;
        Resource(KeycloakSession session) { this.session = session; }
        private RealmModel guard() throws Exception {
            var realm = session.getContext().getRealm();
            var chain = session.getContext().getHttpRequest().getClientCertificateChain();
            if (!"1".equals(System.getenv("MF_NATIVE_FIXTURE_CONTROL"))
                    || !realm.getName().startsWith("mf-native-")
                    || !"127.0.0.1".equals(session.getContext().getConnection().getRemoteAddr())
                    || chain == null || chain.length == 0
                    || !MessageDigest.isEqual(MessageDigest.getInstance("SHA-256").digest(chain[0].getEncoded()),
                        HexFormat.of().parseHex(System.getenv("MF_NATIVE_VERIFIER_BACKEND_CERT_SHA256")))) throw new NotFoundException();
            return realm;
        }
        private Object flow(RealmModel realm, String id) {
            return realm.getAuthenticationExecutionsStream(id).map(e -> {
                Map<String, Object> value = new LinkedHashMap<>();
                value.put("type", e.isAuthenticatorFlow() ? "flow" : e.getAuthenticator());
                value.put("requirement", e.getRequirement().name());
                if (e.isAuthenticatorFlow()) value.put("children", flow(realm, e.getFlowId()));
                if (e.getAuthenticatorConfig() != null) value.put("config", realm.getAuthenticatorConfigById(e.getAuthenticatorConfig()).getConfig());
                return value;
            }).toList();
        }
        @POST @Path("registration") @Consumes("application/json") @Produces("application/json")
        public Map<String,Object> registration(Map<String,String> body) throws Exception {
            var realm = guard();
            String name = body.get("username");
            if (name == null || !name.matches("action-[a-f0-9]{16}")) throw new BadRequestException();
            var u = session.users().getUserByUsername(realm, name);
            if (u == null) throw new BadRequestException();
            switch (body.get("operation")) {
                case "cooldown-elapsed" -> {
                    String digest = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(
                        u.getEmail().toLowerCase(Locale.ROOT).getBytes(java.nio.charset.StandardCharsets.UTF_8)));
                    session.singleUseObjects().remove("mf-register-v1:"+realm.getId()+":send:"+digest+":cooldown");
                }
                case "force-password" -> u.addRequiredAction(UserModel.RequiredAction.UPDATE_PASSWORD);
                case "state" -> { }
                default -> throw new BadRequestException();
            }
            return Map.of("subject",u.getId(),"verified",u.isEmailVerified(),"actions",u.getRequiredActionsStream().toList(),
                "marker",Objects.toString(u.getFirstAttribute("mf.registration.v1"),""));
        }
        @POST @Path("mail-health") @Consumes("application/json") @Produces("application/json")
        public Map<String,Boolean> mailHealth(Map<String,String> body) throws Exception {
            var realm=guard();var config=new HashMap<>(realm.getSmtpConfig());
            if(!Set.of("127.0.0.1","localhost").contains(config.get("host"))
                    || !body.keySet().equals(Set.of("mode")))throw new BadRequestException();
            switch(body.get("mode")) {
                case "normal" -> {config.put("host","127.0.0.1");config.put("starttls","true");}
                case "wrong-hostname" -> config.put("host","localhost");
                case "insecure" -> config.put("starttls","false");
                default -> throw new BadRequestException();
            }
            realm.setSmtpConfig(config);
            return Map.of("configured",true);
        }
        @GET @Path("policy") @Produces("application/json")
        public Map<String, Object> policy() throws Exception {
            var realm = guard();
            var user = session.users().getUserByUsername(realm, "valid");
            return Map.of("flow", flow(realm, realm.getBrowserFlow().getId()),
                "required_credentials", realm.getRequiredCredentialsStream().map(RequiredCredentialModel::getType).toList(),
                "required_actions", realm.getRequiredActionProvidersStream().filter(RequiredActionProviderModel::isEnabled)
                    .map(m -> Map.of("id", m.getProviderId(), "default", m.isDefaultAction())).toList(),
                "user_required_actions", user.getRequiredActionsStream().toList());
        }
        @POST @Path("mutate") @Consumes("application/json") @Produces("application/json")
        public Map<String, Boolean> mutate(Map<String, String> body) throws Exception {
            var realm = guard();
            var name = body.get("username");
            if (!Set.of("revalidate", "policy-expired", "configured-mfa").contains(name)) throw new BadRequestException();
            var user = session.users().getUserByUsername(realm, name);
            if (user == null) throw new BadRequestException();
            switch (body.get("operation")) {
                case "default-actions", "restore-default-actions" -> {
                    for (String alias : Set.of("VERIFY_EMAIL", "UPDATE_PASSWORD")) {
                        var action = realm.getRequiredActionProviderByAlias(alias);
                        action.setDefaultAction("default-actions".equals(body.get("operation")));
                        realm.updateRequiredActionProvider(action);
                    }
                }
                case "password" -> user.credentialManager().updateCredential(UserCredentialModel.password(body.get("password")));
                case "disable" -> user.setEnabled(false);
                case "otp" -> user.credentialManager().createStoredCredential(
                    org.keycloak.models.credential.OTPCredentialModel.createFromPolicy(realm, UUID.randomUUID().toString()));
                case "require-mfa", "restore-flow" -> {
                    var forms = realm.getAuthenticationExecutionsStream(realm.getBrowserFlow().getId())
                        .filter(AuthenticationExecutionModel::isAuthenticatorFlow)
                        .filter(e -> realm.getAuthenticationExecutionsStream(e.getFlowId())
                            .anyMatch(c -> "auth-username-password-form".equals(c.getAuthenticator()))).findFirst().orElseThrow();
                    var conditional = realm.getAuthenticationExecutionsStream(forms.getFlowId())
                        .filter(AuthenticationExecutionModel::isAuthenticatorFlow).findFirst().orElseThrow();
                    conditional.setRequirement("require-mfa".equals(body.get("operation"))
                        ? AuthenticationExecutionModel.Requirement.REQUIRED : AuthenticationExecutionModel.Requirement.CONDITIONAL);
                    realm.updateAuthenticatorExecution(conditional);
                }
                case "expire" -> {
                    var credential = user.credentialManager().getStoredCredentialsByTypeStream("password").findFirst().orElseThrow();
                    credential.setCreatedDate(System.currentTimeMillis() - 172800000L);
                    user.credentialManager().updateStoredCredential(credential);
                }
                default -> throw new BadRequestException();
            }
            body.clear();
            return Map.of("changed", true);
        }

        /** Native signing and event/state oracles only; redemption uses production handlers. */
        @POST @Path("action") @Consumes("application/json") @Produces("application/json")
        public Map<String, Object> action(Map<String, String> body) throws Exception {
            var realm = guard();
            String name = body.get("username"), purpose = body.get("purpose");
            if (name == null || !name.matches("action-[a-f0-9]{16}")
                    || !Set.of("verification", "reset").contains(purpose)) throw new BadRequestException();
            var user = session.users().getUserByUsername(realm, name);
            switch (body.get("operation")) {
                case "create" -> {
                    if (user != null) throw new BadRequestException();
                    user = session.users().addUser(realm, name);
                    user.setEnabled(true);
                    user.setEmail(name + "@fixture.invalid");
                    if (!"true".equals(body.get("incomplete_profile"))) {
                        user.setFirstName("Action"); user.setLastName("Fixture");
                    }
                    user.setEmailVerified("reset".equals(purpose));
                    user.credentialManager().updateCredential(UserCredentialModel.password(body.get("password")));
                    if ("verification".equals(purpose)) user.addRequiredAction(UserModel.RequiredAction.VERIFY_EMAIL);
                    realm.setResetPasswordAllowed(true);
                    realm.setEventsEnabled(true);
                    realm.setEnabledEventTypes(Set.of("VERIFY_EMAIL", "VERIFY_EMAIL_ERROR", "UPDATE_PASSWORD", "UPDATE_PASSWORD_ERROR"));
                    return Map.of("created", true);
                }
                case "rearm" -> {
                    if (user == null) throw new BadRequestException();
                    user.addRequiredAction("verification".equals(purpose) ? UserModel.RequiredAction.VERIFY_EMAIL : UserModel.RequiredAction.UPDATE_PASSWORD);
                    return Map.of("rearmed", true);
                }
                case "rollback", "pause-commit", "fail-commit" -> {
                    if (user == null) throw new BadRequestException();
                    user.setSingleAttribute("mf-fixture-" + body.get("operation"), "true");
                    return Map.of("armed", true);
                }
                case "clock-offset" -> {
                    int offset = Integer.parseInt(body.get("seconds"));
                    if (offset != 0 && offset != 901) throw new BadRequestException();
                    Time.setOffset(offset);
                    return Map.of("clock_adjusted", true);
                }
                case "release-commit" -> {
                    if (user == null) throw new BadRequestException();
                    var latch = ResetEventFixtureObserver.paused.get(user.getId());
                    if (latch == null) throw new BadRequestException();
                    latch.countDown();
                    return Map.of("released", true);
                }
                case "check-password" -> {
                    if (user == null) throw new BadRequestException();
                    return Map.of("valid", user.credentialManager().isValid(UserCredentialModel.password(body.get("password"))));
                }
                case "state" -> {
                    if (user == null) throw new BadRequestException();
                    var events = session.getProvider(EventStoreProvider.class);
                    long verified = events.createQuery().realm(realm.getId()).user(user.getId())
                        .type(EventType.VERIFY_EMAIL).getResultStream().filter(e -> e.getError() == null).count();
                    long reset = events.createQuery().realm(realm.getId()).user(user.getId())
                        .type(EventType.UPDATE_PASSWORD).getResultStream().filter(e -> e.getError() == null).count();
                    String uid = user.getId();
                    return Map.of("observations", ResetEventFixtureObserver.observations.stream().filter(e -> uid.equals(e.get("user_id"))).toList(),
                        "verified", user.isEmailVerified(), "verify_events", verified, "reset_events", reset,
                        "verify_handler", session.getProvider(ActionTokenHandler.class, "verify-email").getClass().getName(),
                        "reset_handler", session.getProvider(ActionTokenHandler.class, "reset-credentials").getClass().getName(),
                        "password_action", session.getProvider(org.keycloak.authentication.RequiredActionProvider.class, "UPDATE_PASSWORD").getClass().getName(),
                        "required_actions", user.getRequiredActionsStream().toList());
                }
                case "mint" -> {
                    if (user == null) throw new BadRequestException();
                    int ttl = Integer.parseInt(body.getOrDefault("ttl", "600"));
                    if (ttl < -60 || ttl > 600) throw new BadRequestException();
                    var client = realm.getClientByClientId("mentorflow-desktop");
                    var root = session.authenticationSessions().createRootAuthenticationSession(realm);
                    var auth = root.createAuthenticationSession(client);
                    auth.setAuthenticatedUser(user); auth.setProtocol("openid-connect"); auth.setAction("AUTHENTICATE");
                    auth.setRedirectUri("http://127.0.0.1/unused-fixture-callback");
                    auth.setClientNote("response_type", "code");
                    if (body.get("reset_state") != null) auth.setClientNote("state", body.get("reset_state"));
                    auth.setClientNote("redirect_uri", "http://127.0.0.1/unused-fixture-callback");
                    auth.setAuthNote("END_AFTER_REQUIRED_ACTIONS", "true");
                    String compound = AuthenticationSessionCompoundId.fromAuthSession(auth).getEncodedId();
                    String email = "true".equals(body.get("wrong_email")) ? "wrong@fixture.invalid" : user.getEmail();
                    DefaultActionToken token = "verification".equals(purpose)
                        ? new VerifyEmailActionToken(user.getId(), Time.currentTime()+ttl, compound, email, client.getClientId())
                        : new ResetCredentialsActionToken(user.getId(), email, Time.currentTime()+ttl, compound, client.getClientId());
                    return Map.of("token", token.serialize(session, realm, session.getContext().getUri()), "tab", auth.getTabId());
                }
                default -> throw new BadRequestException();
            }
        }
    }
}
