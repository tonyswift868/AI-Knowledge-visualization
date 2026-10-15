package win.clannad.mentorflow.identity;

import java.net.URI;
import java.util.Arrays;
import java.util.Locale;
import java.util.Set;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

@Component
public final class IdentitySettings {
    public final String issuer, canonicalIssuer, jwksUri, audience, clientId, cookieName, mode;
    public final int skew, maxLifetime, timeout, cacheSeconds;
    public IdentitySettings(Environment env) {
        mode=env.getProperty("MENTORFLOW_DEPLOYMENT_MODE","ENTERPRISE_SERVER").strip().toUpperCase(Locale.ROOT);
        if (!"ENTERPRISE_SERVER".equals(mode)) throw new IllegalArgumentException("Java identity runtime supports ENTERPRISE_SERVER mode only");
        boolean local=truthy(env.getProperty("MENTORFLOW_KEYCLOAK_ALLOW_LOOPBACK_HTTP","false"));
        issuer=validatedUrl(env.getRequiredProperty("MENTORFLOW_KEYCLOAK_ISSUER").strip(), "MENTORFLOW_KEYCLOAK_ISSUER", local);
        String identityIssuer=env.getProperty("MENTORFLOW_KEYCLOAK_IDENTITY_ISSUER", "").strip();
        canonicalIssuer=identityIssuer.isEmpty() ? issuer : identityIssuer;
        String transportValue=env.getProperty("MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER", "").strip();
        String transport=validatedUrl(transportValue.isEmpty() ? issuer : transportValue, "MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER", local);
        URI publicUri=URI.create(issuer), transportUri=URI.create(transport);
        if (!publicUri.getScheme().equalsIgnoreCase(transportUri.getScheme())
            || !publicUri.getHost().equalsIgnoreCase(transportUri.getHost())
            || !trimPath(publicUri.getPath()).equals(trimPath(transportUri.getPath())))
            throw new IllegalArgumentException("OIDC transport issuer may change only the port of the public issuer");
        jwksUri=transport+"/protocol/openid-connect/certs";
        audience=env.getRequiredProperty("MENTORFLOW_KEYCLOAK_AUDIENCE").strip();
        clientId=env.getRequiredProperty("MENTORFLOW_KEYCLOAK_CLIENT_ID").strip();
        skew=Integer.parseInt(env.getProperty("MENTORFLOW_KEYCLOAK_CLOCK_SKEW_SECONDS","30"));
        maxLifetime=Integer.parseInt(env.getProperty("MENTORFLOW_KEYCLOAK_MAX_TOKEN_LIFETIME_SECONDS","86400"));
        timeout=Integer.parseInt(env.getProperty("MENTORFLOW_KEYCLOAK_HTTP_TIMEOUT_SECONDS","10"));
        cacheSeconds=Integer.parseInt(env.getProperty("MENTORFLOW_KEYCLOAK_JWKS_CACHE_SECONDS","300"));
        String redirect=validatedUrl(env.getRequiredProperty("MENTORFLOW_KEYCLOAK_REDIRECT_URI").strip(), "MENTORFLOW_KEYCLOAK_REDIRECT_URI", local);
        String configuredCookie=env.getProperty("MENTORFLOW_IDENTITY_SESSION_COOKIE_NAME", "").strip();
        cookieName=configuredCookie.isEmpty()
            ? (local && redirect.startsWith("http:") ? "mentorflow_enterprise_session" : "__Host-mentorflow_enterprise_session")
            : configuredCookie;
        String[] configuredAlgorithms=Arrays.stream(env.getProperty("MENTORFLOW_KEYCLOAK_ALGORITHMS","RS256").split(","))
            .map(String::strip).filter(s->!s.isEmpty()).map(s->s.toUpperCase(Locale.ROOT)).toArray(String[]::new);
        if (!Arrays.equals(configuredAlgorithms,new String[]{"RS256"})) throw new IllegalArgumentException("RS256 only");
        if (clientId.isBlank() || audience.isBlank() || clientId.equals(audience) || skew<0 || skew>300 || timeout<1 || timeout>60 || cacheSeconds<30 || cacheSeconds>3600 || maxLifetime<60 || maxLifetime>86400)
            throw new IllegalArgumentException("OIDC configuration is invalid");
        if (!canonicalIssuer.equals(issuer))
            throw new IllegalArgumentException("Canonical issuer aliases require the Python identity migration flow in this first migration step");
        if (!env.getProperty("MENTORFLOW_KEYCLOAK_ISSUER_MIGRATION_JSON", "").isBlank() ||
            Arrays.stream(new String[]{"MENTORFLOW_IDENTITY_CONTINUITY_FILE", "MENTORFLOW_IDENTITY_CONTINUITY_SIGNATURE_HEX",
                "MENTORFLOW_IDENTITY_CONTINUITY_PUBLIC_KEY_HEX", "MENTORFLOW_IDENTITY_CONTINUITY_SHA256"})
                .anyMatch(key -> !env.getProperty(key, "").isBlank()))
            throw new IllegalArgumentException("Issuer migration/continuity must remain on Python in this first migration step");
    }

    private static boolean truthy(String value) {
        return Set.of("1", "true", "yes", "on").contains(value.strip().toLowerCase(Locale.ROOT));
    }
    private static String trimPath(String path) { return path == null ? "" : path.replaceAll("/+$", ""); }
    private static String validatedUrl(String value, String field, boolean allowLoopbackHttp) {
        URI uri=URI.create(value);
        if (uri.getUserInfo()!=null || uri.getQuery()!=null || uri.getFragment()!=null || uri.getHost()==null)
            throw new IllegalArgumentException(field+" must not contain credentials, query, or fragment");
        boolean https="https".equalsIgnoreCase(uri.getScheme());
        boolean loopbackHttp=allowLoopbackHttp && "http".equalsIgnoreCase(uri.getScheme()) &&
            Set.of("127.0.0.1","localhost","::1").contains(uri.getHost().toLowerCase(Locale.ROOT));
        if (!https && !loopbackHttp) throw new IllegalArgumentException(field+" must use HTTPS; loopback HTTP requires explicit opt-in");
        return value.replaceAll("/+$", "");
    }
}
