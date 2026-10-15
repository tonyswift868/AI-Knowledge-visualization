package win.clannad.mentorflow.account;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyStore;
import java.security.SecureRandom;
import java.security.cert.CertificateFactory;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManagerFactory;
import win.clannad.mentorflow.identity.IdentityFailure;
import win.clannad.mentorflow.identity.IdentitySecurity;
import win.clannad.mentorflow.identity.IdentitySettings;

/** The same private mTLS identity bridge and Admin REST credential-state reads as Python. */
public final class NativeIdentityBridge implements NativeSessionService.CredentialVerifier,
        NativeSessionService.AccountStateChecker {
    private final ObjectMapper json=new ObjectMapper().enable(com.fasterxml.jackson.databind.DeserializationFeature.FAIL_ON_TRAILING_TOKENS);
    private final ObjectMapper authorityJson=json.copy().enable(com.fasterxml.jackson.core.JsonParser.Feature.STRICT_DUPLICATE_DETECTION);
    private final IdentitySettings settings;
    private final Clock clock;
    private final URI endpoint;
    private final String externalIssuer;
    private final HttpClient identityHttp;
    private final Map<String,Object> lifecycle;
    private final HttpClient adminHttp;
    private final String adminBase,tokenEndpoint;
    private final Instant lifecycleExpiry;
    private String adminToken;
    private long adminTokenDeadline;

    public NativeIdentityBridge(IdentitySettings settings,Clock clock,String endpoint,String ca,String cert,
            String key,String externalIssuer,Map<String,Object> lifecycle) throws Exception {
        this.settings=settings;this.clock=clock;
        this.endpoint=https(endpoint,false);
        String issuerPath=URI.create(settings.canonicalIssuer).getPath().replaceAll("/+$","");
        if (!Set.of("/mentorflow-native-identity/v1",issuerPath+"/mentorflow-native-identity/v1").contains(this.endpoint.getPath()))
            throw new IllegalArgumentException("Exact HTTPS native identity bridge endpoint required");
        this.externalIssuer=externalIssuer==null||externalIssuer.isEmpty()?settings.canonicalIssuer:externalIssuer;
        identityHttp=http(tls(ca,cert,key));
        this.lifecycle=lifecycle;
        if(lifecycle==null) {adminHttp=null;adminBase=null;tokenEndpoint=null;lifecycleExpiry=null;}
        else {
            URI origin=https(text(lifecycle,"origin"),true);
            String realm=identifier(text(lifecycle,"realm"));identifier(text(lifecycle,"client_id"));
            adminBase=origin.toString().replaceAll("/+$","")+"/admin/realms/"+realm;
            tokenEndpoint=origin.toString().replaceAll("/+$","")+"/realms/"+realm+"/protocol/openid-connect/token";
            adminHttp=http(tls(text(lifecycle,"ca_file"),(String)lifecycle.get("client_cert_file"),(String)lifecycle.get("client_key_file")));
            lifecycleExpiry=lifecycle.containsKey("expires_at")?Instant.parse(text(lifecycle,"expires_at")):null;
        }
    }

    @Override public NativeSessionService.NativeIdentity authenticate(String username,String password) {
        var body=new LinkedHashMap<String,Object>();body.put("operation","authenticate");body.put("username",username);body.put("password",password);
        try {
            var result=call(identityHttp,HttpRequest.newBuilder(endpoint).header("Content-Type","application/json")
                .POST(HttpRequest.BodyPublishers.ofByteArray(json.writeValueAsBytes(body))),8192);
            if(result.status==401)throw fail(401,"NATIVE_CREDENTIALS_INVALID","账号或密码不正确");
            if(result.status!=200)throw fail(503,"NATIVE_IDENTITY_UNAVAILABLE","账号服务暂不可用。");
            JsonNode v=result.body;
            if(v.path("outcome").asText().equals("challenge_required"))
                throw fail(403,"NATIVE_ADDITIONAL_VERIFICATION_REQUIRED","此账号需要额外验证，请联系账号管理员。");
            if(!v.isObject())throw fail(503,"NATIVE_IDENTITY_UNAVAILABLE","账号服务暂不可用。");
            if(v.size()!=7||!v.has("schema")||!v.has("outcome")||!v.has("issuer")||!v.has("subject")||!v.has("display_name")||!v.has("authentication_strength")||!v.has("verification_handle")
                    ||!v.path("schema").asText().equals("mentorflow.native-identity.v1")||!v.path("outcome").asText().equals("authenticated")
                    ||!v.path("issuer").asText().equals(externalIssuer)||!v.path("authentication_strength").asText().equals("password_verified")
                    ||!v.path("subject").isTextual()||v.path("subject").asText().isEmpty()||!v.path("subject").asText().equals(IdentitySettings.stripIdentityWhitespace(v.path("subject").asText()))
                    ||!v.path("verification_handle").isTextual()||!v.path("verification_handle").asText().matches("[A-Za-z0-9_-]{43}"))
                throw fail(503,"NATIVE_IDENTITY_INVALID","账号服务响应无法验证。");
            String subject=v.path("subject").asText();settings.canonicalizeIssuer(externalIssuer,subject);
            String display=v.path("display_name").isNull()?null:IdentitySettings.stripIdentityWhitespace(v.path("display_name").asText());
            if(subject.codePointCount(0,subject.length())>300||display!=null&&(!v.path("display_name").isTextual()||display.codePointCount(0,display.length())>300))
                throw fail(503,"NATIVE_IDENTITY_UNAVAILABLE","账号服务暂不可用。");
            return new NativeSessionService.NativeIdentity(settings.canonicalIssuer,subject,
                display,"password_verified",null,null,"mentorflow-desktop",
                Instant.ofEpochSecond(clock.instant().getEpochSecond()),v.path("verification_handle").asText());
        } catch(IdentityFailure error){throw error;}
        catch(Exception error){throw fail(503,"NATIVE_IDENTITY_UNAVAILABLE","账号服务暂不可用。");}
        finally{body.clear();}
    }

    @Override public String currentBinding(String issuer,String subject) {
        if(lifecycle==null||lifecycleExpiry!=null&&!clock.instant().isBefore(lifecycleExpiry))throw unavailable();
        try {
            String path="/users/"+identifier(subject);
            JsonNode user=adminGet(path);
            if(!user.isObject()||!user.path("id").asText().equals(subject))throw unavailable();
            if(user.path("enabled").isBoolean()&&!user.path("enabled").booleanValue())throw expired();
            if(!user.path("enabled").isBoolean())throw unavailable();
            JsonNode credentials=adminGet(path+"/credentials");
            if(!credentials.isArray())throw unavailable();
            JsonNode password=null;
            for(JsonNode credential:credentials) {
                if(!credential.isObject())throw unavailable();
                if(credential.path("type").asText().equals("password")) {
                    if(password!=null)throw unavailable();password=credential;
                }
            }
            if(password==null)throw expired();
            if(!password.path("id").isTextual()||!password.path("createdDate").isIntegralNumber()
                    ||user.has("notBefore")&&!user.path("notBefore").isIntegralNumber())throw unavailable();
            // Python hashlib.sha256(json.dumps([id,createdDate,notBefore]).encode()).
            // json.dumps uses ASCII escapes and a space after separators.
            String id=pythonString(password.path("id").asText());
            return IdentitySecurity.sha256("["+id+", "+password.path("createdDate").bigIntegerValue()+", "+user.path("notBefore").asText("0")+"]");
        } catch(IdentityFailure failure){throw failure;}
        catch(Exception error){throw unavailable();}
    }

    /** Lifecycle availability is the existing opt-in/expiry, not a new capability switch. */
    public boolean lifecycleActive() {
        return lifecycle!=null && (lifecycleExpiry==null || clock.instant().isBefore(lifecycleExpiry));
    }

    /** Existing private Keycloak SPI owns codes, one-time state, mutations and mail. */
    public JsonNode authority(Map<String,Object> payload,int maxBytes,String errorCode,String errorMessage) {
        try {
            Reply result=call(identityHttp,HttpRequest.newBuilder(endpoint).header("Content-Type","application/json")
                .POST(HttpRequest.BodyPublishers.ofByteArray(json.writeValueAsBytes(payload))),maxBytes,authorityJson);
            if(result.status!=200 || result.body==null || !result.body.isObject() || !result.body.path("status").isTextual())
                throw new IllegalArgumentException("Invalid authority response");
            return result.body;
        } catch(Exception error) {
            throw fail(503,errorCode,errorMessage);
        }
    }

    public record AdminResult(int status,JsonNode body,String location) {}

    /** One explicit Admin REST operation. No retry, redirect or mutation reconciliation. */
    public AdminResult adminCall(String method,String path,Map<String,Object> payload,Map<String,String> params) {
        if(!lifecycleActive())throw fail(503,"ADMIN_AUTHORITY_UNAVAILABLE","账号服务暂不可用。");
        long operationDeadline=System.nanoTime()+Duration.ofSeconds(12).toNanos();
        try {
            StringBuilder target=new StringBuilder(adminBase).append(path);
            if(params!=null&&!params.isEmpty()) {
                boolean first=true;
                for(var item:params.entrySet()) {
                    target.append(first?'?':'&');first=false;
                    target.append(URLEncoder.encode(item.getKey(),StandardCharsets.UTF_8)).append('=')
                        .append(URLEncoder.encode(item.getValue(),StandardCharsets.UTF_8));
                }
            }
            HttpRequest.BodyPublisher body=payload==null?HttpRequest.BodyPublishers.noBody()
                :HttpRequest.BodyPublishers.ofByteArray(json.writeValueAsBytes(payload));
            String accessToken;
            try{accessToken=adminToken();}
            catch(Exception error){throw fail(503,"ADMIN_AUTHORITY_UNAVAILABLE","账号服务暂不可用。");}
            Reply result=call(adminHttp,HttpRequest.newBuilder(URI.create(target.toString()))
                .header("Accept","application/json").header("Content-Type","application/json")
                .header("Authorization","Bearer "+accessToken).method(method,body),65536,json,
                Math.min(operationDeadline,System.nanoTime()+Duration.ofSeconds(10).toNanos()));
            return new AdminResult(result.status,result.body,result.location);
        } catch(IdentityFailure error) {
            if("NATIVE_IDENTITY_INVALID".equals(error.code))throw fail(503,"KEYCLOAK_RESPONSE_INVALID","账号服务暂不可用。");
            throw error;
        } catch(Exception error) {
            throw fail(503,"KEYCLOAK_OUTCOME_UNKNOWN","账号服务暂不可用。");
        }
    }

    private JsonNode adminGet(String path) throws Exception {
        var result=call(adminHttp,HttpRequest.newBuilder(URI.create(adminBase+path)).header("Accept","application/json")
            .header("Content-Type","application/json").header("Authorization","Bearer "+adminToken()).GET(),65536);
        if(result.status==404)throw expired();
        if(result.status!=200)throw unavailable();
        return result.body;
    }
    private synchronized String adminToken() throws Exception {
        if(adminToken!=null&&System.nanoTime()<adminTokenDeadline)return adminToken;
        try {
            Path path=Path.of(text(lifecycle,"secret_file"));
            if(Files.isSymbolicLink(path)||Files.size(path)>4096)throw unavailable();
            String secret=Files.readString(path,StandardCharsets.UTF_8).strip();
            if(secret.length()<16||secret.length()>4096)throw unavailable();
            String body="grant_type=client_credentials&client_id="+URLEncoder.encode(text(lifecycle,"client_id"),StandardCharsets.UTF_8)
                +"&client_secret="+URLEncoder.encode(secret,StandardCharsets.UTF_8);
            var result=call(adminHttp,HttpRequest.newBuilder(URI.create(tokenEndpoint)).header("Content-Type","application/x-www-form-urlencoded")
                .POST(HttpRequest.BodyPublishers.ofString(body)),32768);
            JsonNode token=result.body.path("access_token"),ttl=result.body.path("expires_in");
            if(result.status!=200||!token.isTextual()||token.asText().isEmpty()||token.asText().contains("\r")||token.asText().contains("\n")
                    ||!ttl.isIntegralNumber()||ttl.asInt()<10||ttl.asInt()>3600)throw unavailable();
            adminToken=token.asText();adminTokenDeadline=System.nanoTime()+Duration.ofSeconds(ttl.asInt()-5).toNanos();return adminToken;
        } catch(Exception e){adminToken=null;adminTokenDeadline=0;throw e;}
    }
    private record Reply(int status,JsonNode body,String location) {}
    private Reply call(HttpClient client,HttpRequest.Builder request,int max) throws Exception {
        return call(client,request,max,json);
    }
    private Reply call(HttpClient client,HttpRequest.Builder request,int max,ObjectMapper reader) throws Exception {
        return call(client,request,max,reader,System.nanoTime()+Duration.ofSeconds(10).toNanos());
    }
    private Reply call(HttpClient client,HttpRequest.Builder request,int max,ObjectMapper reader,long deadline) throws Exception {
        if(deadline<=System.nanoTime())throw new java.net.http.HttpTimeoutException("Operation deadline exceeded");
        var result=client.send(request.timeout(Duration.ofNanos(Math.max(1,deadline-System.nanoTime()))).build(),java.net.http.HttpResponse.BodyHandlers.ofInputStream());
        try(var stream=result.body()) {
            String location=result.headers().firstValue("Location").orElse(null);
            if(result.statusCode()!=200)return new Reply(result.statusCode(),json.nullNode(),location);
            var read=new java.util.concurrent.FutureTask<byte[]>(()->stream.readNBytes(max+1));
            Thread.ofVirtual().start(read);
            byte[] raw;
            try{raw=read.get(Math.max(1,deadline-System.nanoTime()),java.util.concurrent.TimeUnit.NANOSECONDS);}
            finally{if(!read.isDone())read.cancel(true);}
            if(raw.length>max)throw fail(503,"NATIVE_IDENTITY_INVALID","账号服务响应无法验证。");
            return new Reply(result.statusCode(),reader.readTree(raw),location);
        }
    }
    private static HttpClient http(SSLContext context) {
        return HttpClient.newBuilder().sslContext(context).proxy(new java.net.ProxySelector() {
            @Override public List<java.net.Proxy> select(URI uri){return List.of(java.net.Proxy.NO_PROXY);}
            @Override public void connectFailed(URI uri,java.net.SocketAddress address,java.io.IOException error){}
        }).connectTimeout(Duration.ofSeconds(10)).followRedirects(HttpClient.Redirect.NEVER).build();
    }
    public static SSLContext tls(String ca,String cert,String key) throws Exception {
        var factory=CertificateFactory.getInstance("X.509");
        var trust=KeyStore.getInstance(KeyStore.getDefaultType());trust.load(null,null);
        try(var stream=Files.newInputStream(Path.of(ca))) {
            int i=0;for(var certificate:factory.generateCertificates(stream))trust.setCertificateEntry("ca-"+(i++),certificate);
        }
        var managers=TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());managers.init(trust);
        javax.net.ssl.KeyManager[] keys=null;
        if(cert!=null||key!=null) {
            if(cert==null||key==null)throw new IllegalArgumentException("TLS certificate and key must be paired");
            java.security.cert.Certificate[] chain;
            try(var stream=Files.newInputStream(Path.of(cert))) {chain=factory.generateCertificates(stream).toArray(java.security.cert.Certificate[]::new);}
            // Reuse Boot's PKCS#8, PKCS#1 RSA and SEC1 EC support instead of a new PEM/DER parser.
            java.security.PrivateKey privateKey=org.springframework.boot.ssl.pem.PemContent.load(Path.of(key)).getPrivateKey();
            if(privateKey==null)throw new IllegalArgumentException("Unsupported mTLS private key");
            var store=KeyStore.getInstance(KeyStore.getDefaultType());store.load(null,null);char[] password=new char[0];
            store.setKeyEntry("client",privateKey,password,chain);
            var km=KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());km.init(store,password);keys=km.getKeyManagers();
        }
        var context=SSLContext.getInstance("TLS");context.init(keys,managers.getTrustManagers(),new SecureRandom());return context;
    }
    private static URI https(String value,boolean origin) {
        URI uri=URI.create(value);
        if(!"https".equals(uri.getScheme())||uri.getHost()==null||uri.getUserInfo()!=null||uri.getQuery()!=null||uri.getFragment()!=null
                ||origin&&!Set.of("","/").contains(uri.getPath()))throw new IllegalArgumentException("Exact verified HTTPS endpoint required");
        return uri;
    }
    private static String text(Map<String,Object> value,String key) {if(!(value.get(key) instanceof String s)||s.isEmpty())throw new IllegalArgumentException("Missing configuration: "+key);return s;}
    private static String identifier(String value){if(!value.matches("[A-Za-z0-9_-]{1,255}"))throw unavailable();return value;}
    private static IdentityFailure fail(int status,String code,String message){return new IdentityFailure(status,code,message);}
    private static IdentityFailure expired(){return fail(401,"ENTERPRISE_SESSION_REAUTH_REQUIRED","登录已失效，请重新登录。");}
    private static IdentityFailure unavailable(){return fail(503,"NATIVE_AUTH_UNAVAILABLE","账号服务暂不可用，请稍后重试。");}
    private static String pythonString(String value) {
        StringBuilder result=new StringBuilder("\"");
        for(int i=0;i<value.length();i++) {char c=value.charAt(i);switch(c) {
            case '"' -> result.append("\\\"");case '\\' -> result.append("\\\\");case '\n' -> result.append("\\n");case '\r' -> result.append("\\r");
            case '\t' -> result.append("\\t");case '\b' -> result.append("\\b");case '\f' -> result.append("\\f");
            default -> {if(c<32||c>126)result.append(String.format("\\u%04x",(int)c));else result.append(c);}
        }}return result.append('"').toString();
    }
}
