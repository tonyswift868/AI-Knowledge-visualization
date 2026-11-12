package win.clannad.mentorflow.identity;

import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.core.StreamReadFeature;
import com.fasterxml.jackson.databind.json.JsonMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.nimbusds.jose.*;
import com.nimbusds.jose.proc.*;
import com.nimbusds.jose.jwk.source.*;
import com.nimbusds.jose.util.DefaultResourceRetriever;
import com.nimbusds.jose.util.Resource;
import com.nimbusds.jose.util.ResourceRetriever;
import com.fasterxml.jackson.core.type.TypeReference;
import java.io.IOException;
import java.math.BigInteger;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.net.URI;
import java.net.URL;
import java.nio.ByteBuffer;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.TimeUnit;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.stereotype.Component;
import org.springframework.beans.factory.ObjectProvider;

/** Nimbus/Spring performs RSA verification; this adapter preserves Python errors/order. */
@Component
public final class CompatibleJwtDecoder implements JwtDecoder {
    private final IdentitySettings settings;
    private final NativeAccessAuthenticator nativeAccess;
    private final DefaultJOSEProcessor<SecurityContext> signatures;
    private final ObjectMapper strict=JsonMapper.builder()
        .enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
        .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
        .build();
    public CompatibleJwtDecoder(IdentitySettings settings, ObjectProvider<NativeAccessAuthenticator> nativeAccess) throws Exception {
        this.settings=settings;
        this.nativeAccess=nativeAccess.getIfAvailable();
        ResourceRetriever retriever=new ValidatingResourceRetriever(
            settings.timeout*1000,settings.timeout*1000,Integer.MAX_VALUE,strict);
        var source=new RemoteJWKSet<SecurityContext>(URI.create(settings.jwksUri).toURL(),
            retriever,
            new DefaultJWKSetCache(settings.cacheSeconds,settings.cacheSeconds,TimeUnit.SECONDS));
        signatures=new DefaultJOSEProcessor<>();
        signatures.setJWSTypeVerifier((type,context)->{}); // Python accepts three case-insensitive types below.
        var selector=new JWSVerificationKeySelector<SecurityContext>(JWSAlgorithm.RS256,source);
        signatures.setJWSKeySelector((header,context)->selector.selectJWSKeys(
            new JWSHeader.Builder(header).keyID(header.getKeyID().strip()).build(),context));
    }
    private JsonNode segment(String value,String field,int max) {
        if (value.isEmpty() || value.length()>max*2 || !value.matches("[A-Za-z0-9_-]+")) throw new IdentityFailure("OIDC_TOKEN_INVALID","OIDC "+field+" is malformed");
        byte[] bytes;
        try { bytes=Base64.getUrlDecoder().decode(value); }
        catch (IllegalArgumentException e) { throw new IdentityFailure("OIDC_TOKEN_INVALID","OIDC "+field+" is malformed"); }
        if (bytes.length>max) throw new IdentityFailure("OIDC_TOKEN_INVALID","OIDC "+field+" is too large");
        if (field.equals("signature")) return null;
        JsonNode node;
        try {
            // Python decodes JWT JSON as strict UTF-8, rather than accepting the
            // UTF-16/UTF-32 auto-detection Jackson offers for byte arrays.
            String json=StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(bytes)).toString();
            node=strict.readTree(json);
        }
        catch (Exception e) { throw new IdentityFailure("OIDC_TOKEN_INVALID","OIDC "+field+" is invalid JSON"); }
        if (node==null || !node.isObject()) throw new IdentityFailure("OIDC_TOKEN_INVALID","OIDC "+field+" must be an object");
        return node;
    }
    private String text(JsonNode node,String name,int limit,boolean optional) {
        JsonNode v=node.get(name);
        if (optional && (v==null || v.isNull())) return null;
        if (v==null || !v.isTextual() || v.asText().strip().isEmpty() || v.asText().length()>limit)
            throw new IdentityFailure("OIDC_CLAIM_INVALID","OIDC "+name+" claim is invalid");
        return v.asText().strip();
    }
    private double number(JsonNode node,String name,boolean optional) {
        JsonNode v=node.get(name);
        if (optional && (v==null || v.isNull())) return Double.NaN;
        double value=v!=null&&v.isNumber()?v.doubleValue():Double.NaN;
        // Python datetime.fromtimestamp accepts UTC years 1 through 9999.
        // Bound both sides before converting to Nimbus' java.time types.
        if (v==null || !v.isNumber() || !Double.isFinite(value) || value < -62135596800d || value >= 253402300800d)
            throw new IdentityFailure("OIDC_CLAIM_INVALID","OIDC "+name+" claim is invalid");
        return value;
    }
    @Override public Jwt decode(String token) {
        if(token!=null&&token.startsWith("mfn1_a_")) {
            if(nativeAccess==null) throw new IdentityFailure("ENTERPRISE_SESSION_REAUTH_REQUIRED","登录已失效，请重新登录。");
            return nativeAccess.authenticateAccess(token);
        }
        if (token==null || token.isEmpty() || token.length()>24000 || token.chars().anyMatch(Character::isWhitespace))
            throw new IdentityFailure("OIDC_TOKEN_INVALID","OIDC token is missing, too large, or malformed");
        String[] parts=token.split("\\.",-1);
        if (parts.length!=3) throw new IdentityFailure("OIDC_TOKEN_INVALID","OIDC token must have three segments");
        if (token.substring(0, token.lastIndexOf('.')).chars().anyMatch(c->c>0x7f))
            throw new IdentityFailure("OIDC_TOKEN_INVALID","OIDC token is not ASCII");
        JsonNode header=segment(parts[0],"header",8192), claims=segment(parts[1],"claims",16384);
        segment(parts[2],"signature",2048);
        if (!"RS256".equals(text(header,"alg",20,false))) throw new IdentityFailure("OIDC_ALGORITHM_REJECTED","OIDC signing algorithm is not allowed");
        if (header.hasNonNull("crit")) throw new IdentityFailure("OIDC_HEADER_UNSUPPORTED","OIDC critical headers are not supported");
        JsonNode type=header.get("typ");
        if (type==null || !type.isTextual() || type.asText().length()>100 || !Set.of("jwt","bearer","at+jwt").contains(type.asText().strip().toLowerCase(Locale.ROOT)))
            throw new IdentityFailure("OIDC_TOKEN_TYPE_INVALID","OIDC token type is invalid");
        text(header,"kid",300,false);
        try { signatures.process(JWSObject.parse(token),null); }
        catch (Exception e) {
            String chain=""; for(Throwable c=e;c!=null;c=c.getCause()) chain+=" "+c.getClass().getName()+" "+c.getMessage();
            for(Throwable c=e;c!=null;c=c.getCause()) if(c instanceof InvalidJwksIOException invalid)
                throw new IdentityFailure(invalid.code,invalid.getMessage(),503,invalid);
            if (chain.contains("JWKSetParseException"))
                throw new IdentityFailure("OIDC_JWKS_INVALID","OIDC JWKS response is invalid",503,e);
            if (chain.contains("RemoteKeySourceException") || chain.contains("ResourceRetrievalException"))
                throw new IdentityFailure("OIDC_JWKS_UNAVAILABLE","OIDC signing keys are unavailable",503,e);
            if (chain.contains("no matching key") || chain.contains("No matching key") || chain.contains("Another algorithm expected"))
                throw new IdentityFailure("OIDC_SIGNING_KEY_NOT_FOUND","OIDC signing key was not found");
            throw new IdentityFailure("OIDC_SIGNATURE_INVALID","OIDC token signature is invalid");
        }
        for(String name:List.of("iss","aud","exp","iat","jti","sub")) if (!claims.has(name))
            throw new IdentityFailure("OIDC_CLAIM_REQUIRED","OIDC "+name+" claim is required");
        String issuer=text(claims,"iss",500,false);
        if (!issuer.equals(claims.get("iss").asText()) || !settings.acceptsIssuer(issuer,(long)(Instant.now().toEpochMilli()/1000d))) throw new IdentityFailure("OIDC_ISSUER_INVALID","OIDC token issuer is invalid");
        JsonNode aud=claims.get("aud"); List<String> audiences=new ArrayList<>();
        if (aud.isTextual() && !aud.asText().strip().isEmpty()) audiences.add(aud.asText().strip());
        else if (aud.isArray() && aud.size()>0 && aud.size()<=32) {
            for(JsonNode a:aud) { if(!a.isTextual() || a.asText().strip().isEmpty()) throw new IdentityFailure("OIDC_CLAIM_INVALID","OIDC aud claim is invalid"); audiences.add(a.asText().strip()); }
            if (new HashSet<>(audiences).size()!=audiences.size()) throw new IdentityFailure("OIDC_CLAIM_INVALID","OIDC aud claim is invalid");
        } else throw new IdentityFailure("OIDC_CLAIM_INVALID","OIDC aud claim is invalid");
        if (!audiences.contains(settings.audience)) throw new IdentityFailure("OIDC_AUDIENCE_INVALID","OIDC token audience is invalid");
        String azp=text(claims,"azp",300,true);
        if (audiences.size()>1 && azp==null) throw new IdentityFailure("OIDC_AUTHORIZED_PARTY_REQUIRED","OIDC azp is required for multiple audiences");
        if (azp!=null && !azp.equals(settings.clientId)) throw new IdentityFailure("OIDC_AUTHORIZED_PARTY_INVALID","OIDC authorized party is invalid");
        double now=Instant.now().toEpochMilli()/1000d, iat=number(claims,"iat",false), nbf=number(claims,"nbf",true), exp=number(claims,"exp",false);
        if(iat>now+settings.skew) throw new IdentityFailure("OIDC_ISSUED_AT_INVALID","OIDC token issuance is in the future");
        if(nbf>now+settings.skew) throw new IdentityFailure("OIDC_TOKEN_NOT_ACTIVE","OIDC token is not active yet");
        if(exp<=now-settings.skew) throw new IdentityFailure("OIDC_TOKEN_EXPIRED","OIDC token has expired");
        if(exp<=iat) throw new IdentityFailure("OIDC_CLAIM_INVALID","OIDC token expiry must follow issuance");
        if(exp-iat>settings.maxLifetime+settings.skew) throw new IdentityFailure("OIDC_TOKEN_LIFETIME_INVALID","OIDC token lifetime is too long");
        String subject=text(claims,"sub",300,false);
        String canonicalIssuer;
        try { canonicalIssuer=settings.canonicalizeIssuer(issuer,subject); }
        catch(IllegalArgumentException denied) { throw new IdentityFailure("OIDC_IDENTITY_CONTINUITY_REQUIRED","Account continuity is not authorized"); }
        number(claims,"auth_time",true);text(claims,"jti",300,false);text(claims,"sub",300,false);
        for(String name:List.of("client_id","preferred_username","sid","name")) text(claims,name,300,true);
        text(claims,"email",320,true);text(claims,"acr",160,true);
        double authenticationTime=number(claims,"auth_time",true);
        if(!Double.isNaN(authenticationTime) && authenticationTime>iat)
            throw new PrincipalValidationFailure();
        // Keep the remaining zero-skew checks in the authenticated request
        // context. Python checks organization and account closure first, then
        // maps an expiry accepted only by clock skew to ENTERPRISE_SESSION_INVALID.
        Instant issuedAt=instant(iat), expiresAt=instant(exp);
        Map<String,Object> headerValues=strict.convertValue(header,new TypeReference<Map<String,Object>>(){});
        Map<String,Object> claimValues=strict.convertValue(claims,new TypeReference<Map<String,Object>>(){});
        // Nimbus' JWTClaimsSet parser rejects malformed exp before signature
        // verification and can mask Python's claim-validation errors. We used
        // Nimbus only for JWS signature verification above, then normalize the
        // already validated registered dates for Spring's Jwt contract.
        claimValues.put("iat",issuedAt); claimValues.put("exp",expiresAt);
        if(!Double.isNaN(nbf)) claimValues.put("nbf",instant(nbf));
        double authTime=number(claims,"auth_time",true);
        if(!Double.isNaN(authTime)) claimValues.put("auth_time",instant(authTime));
        claimValues.put("iss",canonicalIssuer);
        claimValues.put("aud",audiences);
        claimValues.put("jti",text(claims,"jti",300,false));
        claimValues.put("sub",text(claims,"sub",300,false));
        if(azp!=null) claimValues.put("azp",azp);
        for(String name:List.of("client_id","preferred_username","sid","name","email","acr")) {
            JsonNode value=claims.get(name);
            if(value!=null&&!value.isNull()) {
                int max=name.equals("email")?320:name.equals("acr")?160:300;
                claimValues.put(name,text(claims,name,max,false));
            }
        }
        return Jwt.withTokenValue(token).headers(values->values.putAll(headerValues))
            .claims(values->values.putAll(claimValues)).issuedAt(issuedAt).expiresAt(expiresAt).build();
    }

    private static Instant instant(double timestamp) {
        BigDecimal value=BigDecimal.valueOf(timestamp);
        BigDecimal floor=value.setScale(0,RoundingMode.FLOOR);
        long seconds=floor.longValueExact();
        long nanos=value.subtract(floor).movePointRight(9).setScale(0,RoundingMode.HALF_UP).longValueExact();
        if(nanos==1_000_000_000L) return Instant.ofEpochSecond(seconds+1);
        return Instant.ofEpochSecond(seconds,nanos);
    }

    /** Python's principal model raises an unhandled validation error for this
     * signed-but-inconsistent provider claim. Preserve its HTTP 500 contract. */
    public static final class PrincipalValidationFailure extends RuntimeException {}

    /** Validate the same JWKS envelope and RSA bounds as the Python resolver. */
    private static final class ValidatingResourceRetriever extends DefaultResourceRetriever {
        private final ObjectMapper json;
        private long lastSuccessfulFetchNanos=Long.MIN_VALUE;
        private String lastContent;
        private String lastContentType;
        ValidatingResourceRetriever(int connectTimeout,int readTimeout,int sizeLimit,ObjectMapper json) {
            super(connectTimeout,readTimeout,sizeLimit);
            this.json=json;
        }
        @Override public synchronized Resource retrieveResource(URL url) throws IOException {
            long now=System.nanoTime();
            // Nimbus refreshes a still-cached JWKS immediately when a kid is
            // absent. Python rate-limits that forced refresh for 30 seconds;
            // returning the last validated set lets Nimbus produce the same
            // SIGNING_KEY_NOT_FOUND without another HTTP request.
            if(lastContent!=null && lastSuccessfulFetchNanos!=Long.MIN_VALUE
                && now-lastSuccessfulFetchNanos<TimeUnit.SECONDS.toNanos(30))
                return new Resource(lastContent,lastContentType);
            Resource result=super.retrieveResource(url);
            String accepted=validateAndProject(result.getContent());
            lastContent=accepted;
            lastContentType=result.getContentType();
            lastSuccessfulFetchNanos=System.nanoTime();
            return new Resource(accepted,lastContentType);
        }
        private String validateAndProject(String content) throws IOException {
            byte[] utf8=content.getBytes(StandardCharsets.UTF_8);
            if(utf8.length>1_048_576) throw invalid("OIDC_JWKS_INVALID","OIDC JWKS response is too large");
            JsonNode root;
            try { root=json.readTree(content); }
            catch(Exception e) { throw invalid("OIDC_JWKS_INVALID","OIDC JWKS response is invalid"); }
            JsonNode entries=root!=null&&root.isObject()?root.get("keys"):null;
            if(entries==null||!entries.isArray()||entries.size()<1||entries.size()>32)
                throw invalid("OIDC_JWKS_INVALID","OIDC JWKS keys are invalid");
            Set<String> seen=new HashSet<>(); ArrayNode accepted=json.createArrayNode();
            for(JsonNode entry:entries) {
                if(!entry.isObject()) throw invalid("OIDC_JWKS_INVALID","OIDC JWK entry is invalid");
                JsonNode kidNode=entry.get("kid");
                if(kidNode==null||!kidNode.isTextual()||kidNode.asText().strip().isEmpty()||kidNode.asText().length()>300)
                    throw invalid("OIDC_JWKS_INVALID","OIDC JWK key id is invalid");
                String kid=kidNode.asText().strip();
                if(!seen.add(kid)) throw invalid("OIDC_JWKS_INVALID","OIDC JWK key ids must be unique");
                if(!"RSA".equals(entry.path("kty").asText())) continue;
                JsonNode use=entry.get("use"),alg=entry.get("alg");
                if((use!=null&&!use.isNull()&&!(use.isTextual()&&"sig".equals(use.asText())))
                    ||(alg!=null&&!alg.isNull()&&!(alg.isTextual()&&"RS256".equals(alg.asText())))) continue;
                JsonNode n=entry.get("n"),e=entry.get("e");
                if(n==null||!n.isTextual()||e==null||!e.isTextual())
                    throw invalid("OIDC_JWKS_INVALID","OIDC RSA key is missing n or e");
                BigInteger modulus,exponent;
                try { modulus=new BigInteger(1,decodeJwk(n.asText(),1024)); exponent=new BigInteger(1,decodeJwk(e.asText(),16)); }
                catch(IllegalArgumentException failure) { throw invalid("OIDC_JWKS_INVALID","OIDC RSA key parameters are malformed"); }
                if(modulus.bitLength()<2048||modulus.bitLength()>8192||exponent.compareTo(BigInteger.valueOf(3))<0||!exponent.testBit(0))
                    throw invalid("OIDC_JWKS_INVALID","OIDC RSA key parameters are unsafe");
                // Python ignores non-RSA keys and irrelevant JWK metadata.
                // Project only the accepted RSA signing-key contract so Nimbus
                // cannot reject an otherwise-ignored EC/extra parameter.
                ObjectNode key=json.createObjectNode().put("kty","RSA").put("kid",kid)
                    .put("n",n.asText()).put("e",e.asText());
                if(use!=null&&use.isTextual()) key.put("use",use.asText());
                if(alg!=null&&alg.isTextual()) key.put("alg",alg.asText());
                accepted.add(key);
            }
            if(accepted.isEmpty()) throw invalid("OIDC_JWKS_INVALID","OIDC JWKS has no acceptable signing key");
            ObjectNode projected=json.createObjectNode(); projected.set("keys",accepted);
            try { return json.writeValueAsString(projected); }
            catch(Exception failure) { throw invalid("OIDC_JWKS_INVALID","OIDC JWKS response is invalid"); }
        }
        private static byte[] decodeJwk(String value,int maxBytes) {
            if(value.isEmpty()||value.length()>maxBytes*2||!value.matches("[A-Za-z0-9_-]+")) throw new IllegalArgumentException();
            byte[] decoded;
            try { decoded=Base64.getUrlDecoder().decode(value); }
            catch(IllegalArgumentException failure) { throw new IllegalArgumentException(); }
            if(decoded.length>maxBytes) throw new IllegalArgumentException();
            return decoded;
        }
        private static InvalidJwksIOException invalid(String code,String message) { return new InvalidJwksIOException(code,message); }
    }
    private static final class InvalidJwksIOException extends IOException {
        final String code;
        InvalidJwksIOException(String code,String message) { super(message); this.code=code; }
    }
}
