package win.clannad.mentorflow;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.ws.rs.*;
import jakarta.ws.rs.core.Response;
import java.io.InputStream;
import java.lang.reflect.Proxy;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.*;
import org.keycloak.Config;
import org.keycloak.authentication.AuthenticatorUtil;
import org.keycloak.authentication.RequiredActionContext;
import org.keycloak.authentication.RequiredActionProvider;
import org.keycloak.authentication.authenticators.util.AuthenticatorUtils;
import org.keycloak.credential.hash.PasswordHashProvider;
import org.keycloak.models.*;
import org.keycloak.models.utils.KeycloakModelUtils;
import org.keycloak.services.managers.BruteForceProtector;
import org.keycloak.services.resource.RealmResourceProvider;
import org.keycloak.services.resource.RealmResourceProviderFactory;
import org.keycloak.sessions.AuthenticationSessionModel;

/** Identity-owned, certificate-pinned credential validation. No OAuth token endpoint. */
public final class NativeIdentityVerifier implements RealmResourceProviderFactory {
    private static final ObjectMapper JSON = new ObjectMapper()
        .enable(com.fasterxml.jackson.core.JsonParser.Feature.STRICT_DUPLICATE_DETECTION);
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final Map<String, long[]> attempts = new HashMap<>();
    public String getId() { return "mentorflow-native-identity"; }
    public void init(Config.Scope config) { }
    public void postInit(KeycloakSessionFactory factory) { }
    public void close() { attempts.clear(); }
    public RealmResourceProvider create(KeycloakSession session) {
        return new RealmResourceProvider() {
            public Object getResource() { return new Resource(session); }
            public void close() { }
        };
    }
    private static String env(String name) { return System.getenv("MF_NATIVE_VERIFIER_" + name); }
    private static String digest(String value) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
    }
    private static synchronized boolean permitted(String user, boolean login) throws Exception {
        long now = System.currentTimeMillis();
        attempts.values().removeIf(row -> row[1] <= now);
        if (attempts.size() > 4096) return false;
        String key = digest(user.toLowerCase(Locale.ROOT));
        String[] keys = login ? new String[]{"global", key} : new String[]{"refresh"};
        int[] limits = login ? new int[]{100, 5} : new int[]{200};
        for (int i = 0; i < keys.length; i++) {
            long[] row = attempts.computeIfAbsent(keys[i], k -> new long[]{0, now + 60000});
            if (row[0] >= limits[i]) return false;
        }
        for (String k : keys) attempts.get(k)[0]++;
        return true;
    }
    @Path("")
    public static final class Resource {
        private final KeycloakSession session;
        Resource(KeycloakSession session) { this.session = session; }
        private Response result(int status, Map<String, String> value) {
            return Response.status(status).type("application/json").header("Cache-Control", "no-store")
                .header("Pragma", "no-cache").entity(value).build();
        }
        private Response denied() { return result(401, Map.of("outcome", "invalid")); }
        private Response challenge() { return result(200, Map.of("schema", "mentorflow.native-identity.v1", "outcome", "challenge_required")); }
        private boolean caller() throws Exception {
            RealmModel realm = session.getContext().getRealm();
            if (!"26.7.3".equals(org.keycloak.common.Version.VERSION)
                    || !"1".equals(env("ENABLED")) || !realm.getName().equals(env("REALM"))
                    || env("ISSUER") == null || !env("ISSUER").startsWith("https://")
                    || env("BACKEND_CERT_SHA256") == null) return false;
            // Raw TLS peer certificates only. Never use forwarded certificate headers.
            var chain = session.getContext().getHttpRequest().getClientCertificateChain();
            if (chain == null || chain.length == 0) return false;
            chain[0].checkValidity();
            byte[] actual = MessageDigest.getInstance("SHA-256").digest(chain[0].getEncoded());
            return MessageDigest.isEqual(actual, HexFormat.of().parseHex(env("BACKEND_CERT_SHA256")));
        }
        private boolean locked(RealmModel realm, UserModel user) {
            BruteForceProtector protector = session.getProvider(BruteForceProtector.class);
            return !user.isEnabled() || (realm.isBruteForceProtected() &&
                (protector == null || AuthenticatorUtils.getDisabledByBruteForceEventError(protector, session, realm, user) != null));
        }
        private void dummy(RealmModel realm) {
            var policy = realm.getPasswordPolicy();
            var provider = session.getProvider(PasswordHashProvider.class, policy.getHashAlgorithm());
            if (provider == null) throw new IllegalStateException();
            provider.encodedCredential("MentorFlow-non-account-dummy-input", policy.getHashIterations());
        }
        private List<AuthenticationExecutionModel> active(RealmModel realm, String flow) {
            return realm.getAuthenticationExecutionsStream(flow).filter(AuthenticationExecutionModel::isEnabled).toList();
        }
        private boolean step(AuthenticationExecutionModel e, String name, String requirement) {
            return !e.isAuthenticatorFlow() && name.equals(e.getAuthenticator())
                && requirement.equals(e.getRequirement().name()) && e.getAuthenticatorConfig() == null;
        }
        private boolean subflow(RealmModel realm, AuthenticationExecutionModel e, String requirement) {
            return e.isAuthenticatorFlow() && requirement.equals(e.getRequirement().name())
                && e.getAuthenticator() == null && e.getAuthenticatorConfig() == null
                && "basic-flow".equals(realm.getAuthenticationFlowById(e.getFlowId()).getProviderId());
        }
        /** Only the stock password + conditional 2FA policy is supported. Custom flows stop. */
        private boolean supportedFlow(RealmModel realm, ClientModel client) {
            if (realm.isOrganizationsEnabled() || client.isConsentRequired()
                    || !client.getAuthenticationFlowBindingOverrides().isEmpty() || realm.getBrowserFlow() == null
                    || !"basic-flow".equals(realm.getBrowserFlow().getProviderId())) return false;
            var root = active(realm, realm.getBrowserFlow().getId());
            if ((root.size() != 3 && root.size() != 4) || !step(root.get(0), "auth-cookie", "ALTERNATIVE")
                    || !step(root.get(1), "identity-provider-redirector", "ALTERNATIVE")) return false;
            if (root.size() == 4) {
                // Stock organization branch is inactive only when organizations are disabled.
                var org = root.get(2);
                if (!subflow(realm, org, "ALTERNATIVE")) return false;
                var orgBody = active(realm, org.getFlowId());
                if (orgBody.size() != 1 || !subflow(realm, orgBody.get(0), "CONDITIONAL")) return false;
                var orgCondition = active(realm, orgBody.get(0).getFlowId());
                if (orgCondition.size() != 2 || !step(orgCondition.get(0), "conditional-user-configured", "REQUIRED")
                        || !step(orgCondition.get(1), "organization", "ALTERNATIVE")) return false;
            }
            var forms = root.get(root.size()-1);
            if (!subflow(realm, forms, "ALTERNATIVE")) return false;
            var body = active(realm, forms.getFlowId());
            if (body.size() != 2 || !step(body.get(0), "auth-username-password-form", "REQUIRED")) return false;
            var conditional = body.get(1);
            if (!subflow(realm, conditional, "CONDITIONAL")) return false;
            var factors = active(realm, conditional.getFlowId());
            if (factors.size() < 2 || factors.size() > 3
                    || !step(factors.get(0), "conditional-user-configured", "REQUIRED")
                    || !step(factors.get(factors.size()-1), "auth-otp-form", "ALTERNATIVE")) return false;
            if (factors.size() == 3) {
                var condition = factors.get(1);
                if (!"conditional-credential".equals(condition.getAuthenticator()) || !condition.isRequired()
                        || condition.isAuthenticatorFlow() || condition.getAuthenticatorConfig() == null) return false;
                var config = realm.getAuthenticatorConfigById(condition.getAuthenticatorConfig());
                if (config == null || !Map.of("credentials", "webauthn-passwordless").equals(config.getConfig())) return false;
            }
            return true;
        }
        private boolean additional(RealmModel realm, UserModel user) {
            ClientModel client = realm.getClientByClientId("mentorflow-desktop");
            if (client == null || !client.isEnabled() || user.getFederationLink() != null || !supportedFlow(realm, client)
                    || user.getRequiredActionsStream().findAny().isPresent()
                    || (realm.isVerifyEmail() && !user.isEmailVerified())
                    || realm.getRequiredCredentialsStream().anyMatch(c -> !"password".equals(c.getType()))
                    || user.credentialManager().getStoredCredentialsStream().anyMatch(c -> !Set.of("password", "password-history").contains(c.getType()))) return true;
            // Evaluate identity-owned trigger implementations, including password age and profile policy.
            var root = session.authenticationSessions().createRootAuthenticationSession(realm);
            AuthenticationSessionModel auth = root.createAuthenticationSession(client);
            auth.setAuthenticatedUser(user);
            auth.setAuthNote("PASSWORD_VALIDATED", "true");
            AuthenticatorUtil.addAuthCredential(auth, "password");
            try {
                for (var model : realm.getRequiredActionProvidersStream().filter(RequiredActionProviderModel::isEnabled).toList()) {
                    // Default actions are assigned when an account is created. Their
                    // realm flag is not an outstanding action for every existing user.
                    // Check the user's actions above and evaluate native triggers below.
                    var config = new RequiredActionConfigModel();
                    config.setConfig(model.getConfig()); config.setProviderId(model.getProviderId()); config.setAlias(model.getAlias());
                    RequiredActionContext context = (RequiredActionContext) Proxy.newProxyInstance(
                        RequiredActionContext.class.getClassLoader(), new Class[]{RequiredActionContext.class}, (proxy, method, args) -> {
                            return switch (method.getName()) {
                                case "getUser" -> user; case "getRealm" -> realm; case "getSession" -> session;
                                case "getAuthenticationSession" -> auth; case "getConfig" -> config;
                                case "getConnection" -> session.getContext().getConnection();
                                case "getUriInfo" -> session.getContext().getUri();
                                case "getHttpRequest" -> session.getContext().getHttpRequest();
                                case "getAction" -> model.getAlias();
                                default -> throw new IllegalStateException("Unsupported required action context");
                            };
                        });
                    RequiredActionProvider provider = session.getProvider(RequiredActionProvider.class, model.getProviderId());
                    if (provider == null) return true;
                    provider.evaluateTriggers(context);
                }
                return user.getRequiredActionsStream().findAny().isPresent() || !auth.getRequiredActions().isEmpty();
            } catch (RuntimeException unavailablePolicy) { return true; }
            finally { session.authenticationSessions().removeRootAuthenticationSession(realm, root); }
        }
        private String binding(UserModel user) throws Exception {
            // Remains inside Keycloak; only an unrelated random handle leaves the authority.
            var passwords = user.credentialManager().getStoredCredentialsByTypeStream("password").toList();
            if (passwords.size() != 1) throw new IllegalStateException();
            var c = passwords.get(0);
            return digest(c.getId() + "\n" + c.getCreatedDate() + "\n" + c.getCredentialData() + "\n" + c.getSecretData());
        }
        @POST @Path("v1") @Consumes("application/json") @Produces("application/json")
        public Response verify(InputStream input) {
            byte[] bytes = null;
            Map<String, String> body = null;
            try {
                if (!caller()) return result(404, Map.of("outcome", "unavailable"));
                bytes = input.readNBytes(4097);
                if (bytes.length > 4096) return result(400, Map.of("outcome", "invalid_request"));
                var node = JSON.readTree(bytes);
                if (node == null || !node.isObject() || node.size() > 4) return result(400, Map.of("outcome", "invalid_request"));
                body = new HashMap<>();
                var fields = node.fields();
                while (fields.hasNext()) {
                    var field = fields.next();
                    if (!field.getValue().isTextual()) return result(400, Map.of("outcome", "invalid_request"));
                    body.put(field.getKey(), field.getValue().textValue());
                }
                RealmModel realm = session.getContext().getRealm();
                if (body.keySet().equals(Set.of("operation", "state")) && "reset-watch".equals(body.get("operation"))) {
                    if (!permitted("reset-watch", false)) return result(429, Map.of("outcome", "unavailable"));
                    return result(200, ResetCompletions.watch(session, body.get("state")));
                }
                if (body.keySet().equals(Set.of("operation", "transaction_id")) && "reset-status".equals(body.get("operation"))) {
                    if (!permitted("reset-status", false)) return result(429, Map.of("outcome", "unavailable"));
                    return result(200, ResetCompletions.status(session, body.get("transaction_id")));
                }
                UserModel user;
                String handle;
                if (body.keySet().equals(Set.of("operation", "username", "password")) && "authenticate".equals(body.get("operation"))) {
                    String username = body.get("username"), password = body.get("password");
                    if (username == null || username.isBlank() || username.length() > 320 || password == null || password.isEmpty() || password.length() > 1024) return denied();
                    if (!permitted(username, true)) return result(429, Map.of("outcome", "unavailable"));
                    user = KeycloakModelUtils.findUserByNameOrEmail(session, realm, username);
                    if (user == null || locked(realm, user)) { dummy(realm); return denied(); }
                    if (!user.credentialManager().isValid(UserCredentialModel.password(password))) {
                        if (realm.isBruteForceProtected()) session.getProvider(BruteForceProtector.class).failedLogin(
                            realm, user, session.getContext().getConnection(), session.getContext().getUri(), Set.of("password"));
                        return denied();
                    }
                    body.remove("password"); password = null;
                    if (additional(realm, user)) return challenge();
                    if (realm.isBruteForceProtected()) session.getProvider(BruteForceProtector.class).successfulLogin(
                        realm, user, session.getContext().getConnection(), session.getContext().getUri(), Set.of("password"));
                    byte[] entropy = new byte[32]; RANDOM.nextBytes(entropy);
                    handle = Base64.getUrlEncoder().withoutPadding().encodeToString(entropy);
                    long lease = 28800;
                    if (realm.getSsoSessionMaxLifespan() > 0) lease = Math.min(lease, realm.getSsoSessionMaxLifespan());
                    if (realm.getSsoSessionIdleTimeout() > 0) lease = Math.min(lease, realm.getSsoSessionIdleTimeout());
                    session.singleUseObjects().put("mf-native:" + digest(handle), lease,
                        Map.of("realm", realm.getId(), "subject", user.getId(), "binding", binding(user),
                            "issued", Long.toString(System.currentTimeMillis()/1000)));
                } else if (body.keySet().equals(Set.of("operation", "issuer", "subject", "verification_handle")) && "revalidate".equals(body.get("operation"))) {
                    if (!permitted("", false)) return result(429, Map.of("outcome", "unavailable"));
                    handle = body.get("verification_handle");
                    if (handle == null || !handle.matches("[A-Za-z0-9_-]{43}") || !env("ISSUER").equals(body.get("issuer"))) return denied();
                    var row = session.singleUseObjects().get("mf-native:" + digest(handle));
                    if (row == null || !realm.getId().equals(row.get("realm")) || !Objects.equals(body.get("subject"), row.get("subject"))) return denied();
                    user = session.users().getUserById(realm, row.get("subject"));
                    if (user == null || locked(realm, user) || !binding(user).equals(row.get("binding"))
                            || Long.parseLong(row.get("issued")) < realm.getNotBefore()
                            || Long.parseLong(row.get("issued")) < session.users().getNotBeforeOfUser(realm, user)) return denied();
                    if (additional(realm, user)) return challenge();
                } else return result(400, Map.of("outcome", "invalid_request"));
                String display = Objects.toString(user.getFirstName(), "").trim();
                return result(200, Map.of("schema", "mentorflow.native-identity.v1", "outcome", "authenticated",
                    "issuer", env("ISSUER"), "subject", user.getId(), "display_name", display,
                    "authentication_strength", "password_verified", "verification_handle", handle));
            } catch (Exception failure) {
                // Never log credential requests or expose provider exceptions.
                return result(503, Map.of("outcome", "unavailable"));
            } finally {
                if (bytes != null) Arrays.fill(bytes, (byte)0);
                if (body != null) body.clear();
            }
        }
    }
}
