package win.clannad.mentorflow.identity;

import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyFactory;
import java.security.MessageDigest;
import java.security.Signature;
import java.security.spec.X509EncodedKeySpec;
import java.util.Arrays;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Pattern;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.core.StreamReadFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.json.JsonMapper;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

@Component
public final class IdentitySettings {
    public final String issuer, canonicalIssuer, jwksUri, audience, clientId, cookieName, mode;
    public final int skew, maxLifetime, timeout, cacheSeconds;
    public final boolean cookieSecure;
    private final IssuerMigration issuerMigration;
    private final IdentityContinuity identityContinuity;
    private static final String OLD_ISSUER="https://localhost:18080/realms/mentorflow";
    private static final String NEW_ISSUER="https://auth.clannad.win/realms/mentorflow";
    private static final String AUTHORITY_SHA256="5662cb790710dde77eaf1d8d49edd6e4d476426def0c2ded73146d38d8d0d550";
    public IdentitySettings(Environment env) {
        mode=env.getProperty("MENTORFLOW_DEPLOYMENT_MODE","ENTERPRISE_SERVER").strip().toUpperCase(Locale.ROOT);
        if (!"ENTERPRISE_SERVER".equals(mode)) throw new IllegalArgumentException("Java identity runtime supports ENTERPRISE_SERVER mode only");
        boolean local=truthy(env.getProperty("MENTORFLOW_KEYCLOAK_ALLOW_LOOPBACK_HTTP","false"));
        issuer=validatedUrl(env.getRequiredProperty("MENTORFLOW_KEYCLOAK_ISSUER").strip(), "MENTORFLOW_KEYCLOAK_ISSUER", local);
        String identityIssuer=env.getProperty("MENTORFLOW_KEYCLOAK_IDENTITY_ISSUER", "").strip();
        canonicalIssuer=identityIssuer.isEmpty() ? issuer : identityIssuer;
        issuerMigration=IssuerMigration.parse(env.getProperty("MENTORFLOW_KEYCLOAK_ISSUER_MIGRATION_JSON", ""));
        identityContinuity=IdentityContinuity.load(env);
        validateIssuerBinding(issuer,identityIssuer.isEmpty()?null:identityIssuer,issuerMigration);
        String transportValue=env.getProperty("MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER", "").strip();
        String transport=validatedUrl(transportValue.isEmpty() ? issuer : transportValue, "MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER", local);
        URI publicUri=URI.create(issuer), transportUri=URI.create(transport);
        boolean pairedMigration=issuerMigration!=null && issuer.equals(NEW_ISSUER) && transport.equals(OLD_ISSUER);
        if (!pairedMigration && (!publicUri.getScheme().equalsIgnoreCase(transportUri.getScheme())
            || !publicUri.getHost().equalsIgnoreCase(transportUri.getHost())
            || !trimPath(publicUri.getPath()).equals(trimPath(transportUri.getPath()))))
            throw new IllegalArgumentException("OIDC transport issuer may change only the port of the public issuer");
        jwksUri=transport+"/protocol/openid-connect/certs";
        audience=env.getRequiredProperty("MENTORFLOW_KEYCLOAK_AUDIENCE").strip();
        clientId=env.getRequiredProperty("MENTORFLOW_KEYCLOAK_CLIENT_ID").strip();
        skew=Integer.parseInt(env.getProperty("MENTORFLOW_KEYCLOAK_CLOCK_SKEW_SECONDS","30"));
        maxLifetime=Integer.parseInt(env.getProperty("MENTORFLOW_KEYCLOAK_MAX_TOKEN_LIFETIME_SECONDS","86400"));
        timeout=Integer.parseInt(env.getProperty("MENTORFLOW_KEYCLOAK_HTTP_TIMEOUT_SECONDS","10"));
        cacheSeconds=Integer.parseInt(env.getProperty("MENTORFLOW_KEYCLOAK_JWKS_CACHE_SECONDS","300"));
        String redirect=validatedUrl(env.getRequiredProperty("MENTORFLOW_KEYCLOAK_REDIRECT_URI").strip(), "MENTORFLOW_KEYCLOAK_REDIRECT_URI", local);
        cookieSecure=!(local&&redirect.startsWith("http:"));
        String configuredCookie=env.getProperty("MENTORFLOW_IDENTITY_SESSION_COOKIE_NAME", "").strip();
        cookieName=configuredCookie.isEmpty()
            ? (local && redirect.startsWith("http:") ? "mentorflow_enterprise_session" : "__Host-mentorflow_enterprise_session")
            : configuredCookie;
        String[] configuredAlgorithms=Arrays.stream(env.getProperty("MENTORFLOW_KEYCLOAK_ALGORITHMS","RS256").split(","))
            .map(String::strip).filter(s->!s.isEmpty()).map(s->s.toUpperCase(Locale.ROOT)).toArray(String[]::new);
        if (!Arrays.equals(configuredAlgorithms,new String[]{"RS256"})) throw new IllegalArgumentException("RS256 only");
        if (clientId.isBlank() || audience.isBlank() || clientId.equals(audience) || skew<0 || skew>300 || timeout<1 || timeout>60 || cacheSeconds<30 || cacheSeconds>3600 || maxLifetime<60 || maxLifetime>86400)
            throw new IllegalArgumentException("OIDC configuration is invalid");
    }

    public boolean acceptsIssuer(String candidate,long nowSeconds) {
        return issuer.equals(candidate) || (issuerMigration!=null && issuerMigration.active(nowSeconds)
            && (OLD_ISSUER.equals(candidate)||NEW_ISSUER.equals(candidate)));
    }

    /** Return Python's account namespace after both the time window and signed subject allow-list pass. */
    public String canonicalizeIssuer(String externalIssuer,String subject) {
        if (canonicalIssuer.equals(externalIssuer)) return canonicalIssuer;
        if (canonicalIssuer.equals(OLD_ISSUER) && (OLD_ISSUER.equals(externalIssuer)||NEW_ISSUER.equals(externalIssuer))
            && identityContinuity!=null && identityContinuity.subjects.contains(subject)) return OLD_ISSUER;
        throw new IllegalArgumentException("Account continuity is not authorized");
    }

    private static void validateIssuerBinding(String issuer,String identityIssuer,IssuerMigration migration) {
        if (NEW_ISSUER.equals(issuer) && !OLD_ISSUER.equals(identityIssuer))
            throw new IllegalArgumentException("The E109 public issuer must retain its canonical OLD account namespace");
        if (migration!=null && (!(OLD_ISSUER.equals(issuer)||NEW_ISSUER.equals(issuer)) || !OLD_ISSUER.equals(identityIssuer)))
            throw new IllegalArgumentException("Migration is limited to the exact E109 realm and its existing identity namespace");
        if (identityIssuer!=null && !(OLD_ISSUER.equals(identityIssuer) && (OLD_ISSUER.equals(issuer)||NEW_ISSUER.equals(issuer))))
            throw new IllegalArgumentException("Unapproved identity issuer alias");
    }

    private record IssuerMigration(long notBefore,long notAfter) {
        static IssuerMigration parse(String raw) {
            // Python's issuer_migration.from_json treats only None and the
            // empty string as absent; whitespace-only configuration is invalid.
            if (raw==null||raw.isEmpty()) return null;
            try {
                JsonNode node=JsonMapper.builder().enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS).build().readTree(raw);
                if (node==null||!node.isObject()||node.size()!=3||!node.has("not_before")||!node.has("not_after")||!node.has("authority_sha256")
                    ||!node.get("not_before").isIntegralNumber()||!node.get("not_after").isIntegralNumber()
                    ||!node.get("authority_sha256").isTextual()||!AUTHORITY_SHA256.equals(node.get("authority_sha256").asText()))
                    throw new IllegalArgumentException("Issuer migration requires the exact authority and a window of at most 48 hours");
                if(!node.get("not_before").canConvertToLong()||!node.get("not_after").canConvertToLong()) throw new IllegalArgumentException("Issuer migration requires the exact authority and a window of at most 48 hours");
                long before=node.get("not_before").longValue(),after=node.get("not_after").longValue();
                if(before<=0||after<=before||after-before>172800) throw new IllegalArgumentException("Issuer migration requires the exact authority and a window of at most 48 hours");
                return new IssuerMigration(before,after);
            } catch (IllegalArgumentException e) {throw e;}
            catch (Exception e) {throw new IllegalArgumentException("Issuer migration configuration is invalid",e);}
        }
        boolean active(long now) { return notBefore<=now&&now<notAfter; }
    }

    private record IdentityContinuity(Set<String> subjects) {
        private static final Pattern AUDIT=Pattern.compile("[A-Za-z0-9._-]{1,128}");
        static IdentityContinuity load(org.springframework.core.env.Environment env) {
            String[] names={"MENTORFLOW_IDENTITY_CONTINUITY_FILE","MENTORFLOW_IDENTITY_CONTINUITY_SIGNATURE_HEX",
                "MENTORFLOW_IDENTITY_CONTINUITY_PUBLIC_KEY_HEX","MENTORFLOW_IDENTITY_CONTINUITY_SHA256"};
            String[] values=Arrays.stream(names).map(name->env.getProperty(name,"").strip()).toArray(String[]::new);
            boolean any=Arrays.stream(values).anyMatch(value->!value.isEmpty());
            if(!any)return null;
            if(Arrays.stream(values).anyMatch(String::isEmpty))throw new IllegalArgumentException("All continuity trust anchors are required");
            try {
                byte[] manifest;
                try(var input=Files.newInputStream(Path.of(values[0]))) {
                    manifest=input.readNBytes(1_048_577);
                }
                if(manifest.length<1||manifest.length>1_048_576)throw new IllegalArgumentException("Invalid continuity manifest size");
                String digest=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(manifest));
                if(!values[3].matches("[0-9a-f]{64}")||!MessageDigest.isEqual(digest.getBytes(StandardCharsets.US_ASCII),values[3].getBytes(StandardCharsets.US_ASCII)))
                    throw new IllegalArgumentException("Continuity manifest digest mismatch");
                byte[] signature=HexFormat.of().parseHex(values[1]),rawKey=HexFormat.of().parseHex(values[2]);
                if(signature.length!=64||rawKey.length!=32)throw new IllegalArgumentException("Continuity signature or document invalid");
                byte[] prefix=HexFormat.of().parseHex("302a300506032b6570032100"),encoded=new byte[prefix.length+rawKey.length];
                System.arraycopy(prefix,0,encoded,0,prefix.length);System.arraycopy(rawKey,0,encoded,prefix.length,rawKey.length);
                var key=KeyFactory.getInstance("Ed25519").generatePublic(new X509EncodedKeySpec(encoded));
                var verifier=Signature.getInstance("Ed25519");verifier.initVerify(key);verifier.update(manifest);
                if(!verifier.verify(signature))throw new IllegalArgumentException("Continuity signature or document invalid");
                String json=StandardCharsets.UTF_8.newDecoder().decode(java.nio.ByteBuffer.wrap(manifest)).toString();
                JsonNode doc=JsonMapper.builder().enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS).build().readTree(json);
                if(doc==null||!doc.isObject()||doc.size()!=5||!doc.has("schema")||!doc.has("audit_id")||!doc.has("old_issuer")||!doc.has("new_issuer")||!doc.has("subjects")
                    ||!"mentorflow.identity-continuity.v1".equals(doc.get("schema").asText())||!OLD_ISSUER.equals(doc.get("old_issuer").asText())||!NEW_ISSUER.equals(doc.get("new_issuer").asText())
                    ||!doc.get("audit_id").isTextual()||!AUDIT.matcher(doc.get("audit_id").asText()).matches()||!doc.get("subjects").isArray()||doc.get("subjects").isEmpty())
                    throw new IllegalArgumentException("Invalid continuity contract");
                Set<String> subjects=new HashSet<>();
                for(JsonNode item:doc.get("subjects")) {
                    if(!item.isTextual())throw new IllegalArgumentException("Invalid continuity subject");
                    String sub=item.asText();
                    if(sub.isEmpty()||sub.codePointCount(0,sub.length())>128||!sub.equals(stripPythonWhitespace(sub))||sub.codePoints().anyMatch(c->c<32||c==127)||!subjects.add(sub))
                        throw new IllegalArgumentException("Invalid continuity subject");
                }
                return new IdentityContinuity(Set.copyOf(subjects));
            } catch(IllegalArgumentException e){throw e;}
            catch(Exception e){throw new IllegalArgumentException("Continuity signature or document invalid",e);}
        }
        // Match Python str.strip()'s Unicode whitespace set, including NBSP,
        // NEL and the Unicode space separators omitted by Character.isWhitespace.
        private static String stripPythonWhitespace(String value) {
            int start=0,end=value.length();
            while(start<end) {
                int cp=value.codePointAt(start);
                if(!isPythonWhitespace(cp)) break;
                start+=Character.charCount(cp);
            }
            while(end>start) {
                int cp=value.codePointBefore(end);
                if(!isPythonWhitespace(cp)) break;
                end-=Character.charCount(cp);
            }
            return value.substring(start,end);
        }
        private static boolean isPythonWhitespace(int cp) {
            return (cp>=0x0009&&cp<=0x000D)||(cp>=0x001C&&cp<=0x0020)||cp==0x0085||cp==0x00A0
                ||cp==0x1680||(cp>=0x2000&&cp<=0x200A)||cp==0x2028||cp==0x2029||cp==0x202F
                ||cp==0x205F||cp==0x3000;
        }
    }

    /** Python's identity contract trims Unicode whitespace, including NBSP/NEL. */
    public static String stripIdentityWhitespace(String value) {
        return IdentityContinuity.stripPythonWhitespace(value);
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
