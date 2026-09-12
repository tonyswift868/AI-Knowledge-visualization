package lab;

import jakarta.ws.rs.*;
import jakarta.ws.rs.core.MediaType;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;
import org.keycloak.Config;
import org.keycloak.common.util.Time;
import org.keycloak.models.*;
import org.keycloak.services.resource.*;
import org.keycloak.sessions.*;
import org.keycloak.authentication.actiontoken.DefaultActionToken;
import org.keycloak.authentication.actiontoken.verifyemail.VerifyEmailActionToken;
import org.keycloak.authentication.actiontoken.resetcred.ResetCredentialsActionToken;

/** Disposable lab only: native signing, never a replacement redemption handler.
 * No SMTP provider or message transport is installed. Never deploy this SPI.
 */
public final class NativeTokenFixture implements RealmResourceProviderFactory {
    public String getId() { return "mf-e109-sec04-fixture"; }
    public void init(Config.Scope config) {}
    public void postInit(KeycloakSessionFactory factory) {}
    public void close() {}
    public RealmResourceProvider create(KeycloakSession session) {
        return new RealmResourceProvider() {
            public Object getResource() { return new Resource(session); }
            public void close() {}
        };
    }
    @Path("")
    public static final class Resource {
        private final KeycloakSession session;
        Resource(KeycloakSession session) { this.session = session; }
        @POST @Path("mint") @Produces(MediaType.APPLICATION_JSON)
        public Map<String,String> mint(@HeaderParam("X-Lab-Lease") String lease,
                @QueryParam("user") String username, @QueryParam("purpose") String purpose,
                @DefaultValue("600") @QueryParam("ttl") int ttl,
                @DefaultValue("false") @QueryParam("wrongEmail") boolean wrongEmail) {
            String expected = System.getenv("E109_NATIVE_LAB_LEASE");
            RealmModel realm = session.getContext().getRealm();
            if (expected == null || lease == null || !MessageDigest.isEqual(
                    expected.getBytes(StandardCharsets.UTF_8), lease.getBytes(StandardCharsets.UTF_8))
                    || !realm.getName().startsWith("mf-e109-sec04-")
                    || !"127.0.0.1".equals(session.getContext().getConnection().getRemoteAddr()))
                throw new NotFoundException();
            if (!(purpose.equals("verification") || purpose.equals("reset")) || ttl < -60 || ttl > 600)
                throw new BadRequestException();
            UserModel user = session.users().getUserByUsername(realm, username);
            if (user == null) throw new BadRequestException();
            ClientModel client = realm.getClientByClientId("mf-e109-sec04-client");
            RootAuthenticationSessionModel root = session.authenticationSessions().createRootAuthenticationSession(realm);
            AuthenticationSessionModel auth = root.createAuthenticationSession(client);
            auth.setAuthenticatedUser(user);
            auth.setProtocol("openid-connect");
            auth.setAction("AUTHENTICATE");
            auth.setRedirectUri("http://127.0.0.1/unused-lab-callback");
            auth.setClientNote("response_type", "code");
            auth.setClientNote("redirect_uri", "http://127.0.0.1/unused-lab-callback");
            auth.setAuthNote("END_AFTER_REQUIRED_ACTIONS", "true");
            String compound = AuthenticationSessionCompoundId.fromAuthSession(auth).getEncodedId();
            String email = wrongEmail ? "wrong@invalid.example" : user.getEmail();
            DefaultActionToken token = purpose.equals("verification")
                ? new VerifyEmailActionToken(user.getId(), Time.currentTime() + ttl, compound, email, client.getClientId())
                : new ResetCredentialsActionToken(user.getId(), email, Time.currentTime() + ttl, compound, client.getClientId());
            return Map.of("token", token.serialize(session, realm, session.getContext().getUri()),
                "root", root.getId(), "tab", auth.getTabId());
        }
    }
}
