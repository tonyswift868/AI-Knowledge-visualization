package win.clannad.mentorflow.lab;

import jakarta.ws.rs.*;
import java.security.MessageDigest;
import java.util.*;
import org.keycloak.Config;
import org.keycloak.models.*;
import org.keycloak.services.resource.*;

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
    }
}
