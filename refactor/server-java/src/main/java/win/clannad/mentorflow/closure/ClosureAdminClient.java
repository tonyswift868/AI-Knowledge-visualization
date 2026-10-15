package win.clannad.mentorflow.closure;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import org.springframework.core.env.Environment;
import win.clannad.mentorflow.account.NativeIdentityBridge;

/** Existing lifecycle configuration and exact-user Keycloak deletion protocol. */
public final class ClosureAdminClient implements ClosureProvider {
    private static final ObjectMapper JSON=new ObjectMapper().enable(com.fasterxml.jackson.databind.DeserializationFeature.FAIL_ON_TRAILING_TOKENS);
    private final Clock clock;
    private final Map<String,Object> config;
    private final HttpClient http;
    private final String base,tokenEndpoint;
    private final Instant expiry;
    private String token;
    private long tokenDeadline;
    public ClosureAdminClient(Environment env,Clock clock) {
        this.clock=clock;
        boolean enabled="1".equals(env.getProperty("MENTORFLOW_ACCOUNT_LIFECYCLE_ENABLED"))||"1".equals(env.getProperty("MENTORFLOW_ACCOUNT_MVP_TEST"));
        if(!enabled){config=null;http=null;base=null;tokenEndpoint=null;expiry=null;return;}
        try {
            Path path=Path.of(env.getRequiredProperty("MENTORFLOW_ACCOUNT_LIFECYCLE_CONFIG"));
            if(Files.isSymbolicLink(path)||Files.size(path)>8192)throw new IllegalArgumentException("Invalid lifecycle configuration");
            config=com.fasterxml.jackson.databind.json.JsonMapper.builder()
                .enable(com.fasterxml.jackson.core.StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                .enable(com.fasterxml.jackson.databind.DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
                .build().readValue(Files.readString(path),new com.fasterxml.jackson.core.type.TypeReference<Map<String,Object>>(){});
            String origin=text("origin").replaceAll("/+$","");URI uri=URI.create(origin);
            if(!"https".equals(uri.getScheme())||uri.getHost()==null||uri.getUserInfo()!=null||uri.getQuery()!=null||uri.getFragment()!=null
                    ||!(uri.getPath().isEmpty()||"/".equals(uri.getPath())))throw new IllegalArgumentException("Exact HTTPS origin required");
            String realm=text("realm");if(!realm.matches("[A-Za-z0-9._-]{1,255}"))throw new IllegalArgumentException("Invalid realm");
            base=origin+"/admin/realms/"+realm;tokenEndpoint=origin+"/realms/"+realm+"/protocol/openid-connect/token";
            expiry=config.containsKey("expires_at")?Instant.parse(text("expires_at")):null;
            http=HttpClient.newBuilder().sslContext(NativeIdentityBridge.tls(text("ca_file"),(String)config.get("client_cert_file"),(String)config.get("client_key_file")))
                .connectTimeout(Duration.ofSeconds(10)).followRedirects(HttpClient.Redirect.NEVER).proxy(new java.net.ProxySelector(){
                    public List<java.net.Proxy> select(URI address){return List.of(java.net.Proxy.NO_PROXY);}
                    public void connectFailed(URI address,java.net.SocketAddress socket,java.io.IOException error){}
                }).build();
        }catch(Exception error){throw new IllegalArgumentException("Invalid closure lifecycle configuration",error);}
    }
    public boolean active(){return config!=null&&(expiry==null||clock.instant().isBefore(expiry));}
    private String text(String key){Object value=config.get(key);if(!(value instanceof String s))throw new IllegalArgumentException("Invalid lifecycle configuration");return s;}
    public String username(String subject) {
        try {
            if(!active())throw new ClosureFailure("CLOSURE_UNAVAILABLE");
            Reply reply=request("GET",subject,true);
            JsonNode user=JSON.readTree(reply.body());
            if(reply.status()!=200||!user.isObject()||!user.path("id").asText().equals(subject)||!user.path("username").isTextual()
                    ||user.path("username").asText().isEmpty()||user.path("username").asText().codePointCount(0,user.path("username").asText().length())>255
                    ||!user.path("enabled").isBoolean()||!user.path("enabled").booleanValue())throw new ClosureFailure("CLOSURE_UNAVAILABLE");
            return user.path("username").asText();
        }catch(Exception error){throw new ClosureFailure("CLOSURE_UNAVAILABLE");}
    }
    @Override public void ensureDeleted(String subject) {
        if(providerRequest("GET",subject)==404)return;
        providerRequest("DELETE",subject);
        if(providerRequest("GET",subject)!=404)throw new ClosureFailure("CLOSURE_PROVIDER_DELETE_UNCONFIRMED");
    }
    private int providerRequest(String method,String subject) {
        if(subject==null||!subject.matches("[A-Za-z0-9_-]{1,128}"))throw new ClosureFailure("CLOSURE_IDENTITY_INVALID",403);
        try {
            if(!active())throw new ClosureFailure("CLOSURE_PROVIDER_UNAVAILABLE");
            Reply reply=request(method,subject);
            if(!(method.equals("GET")?java.util.Set.of(200,404):java.util.Set.of(204,404)).contains(reply.status()))
                throw new ClosureFailure("CLOSURE_PROVIDER_UNAVAILABLE");
            if(reply.status()==200||reply.status()==404) {
                if(!reply.contentType().split(";",2)[0].equals("application/json"))throw new ClosureFailure("CLOSURE_PROVIDER_RESPONSE_INVALID");
                JsonNode body=JSON.readTree(reply.body());
                if(!body.isObject()||reply.status()==200&&!body.path("id").asText().equals(subject)
                    ||reply.status()==404&&!body.path("error").asText().equals("User not found"))throw new ClosureFailure("CLOSURE_PROVIDER_RESPONSE_INVALID");
            }
            return reply.status();
        }catch(ClosureFailure error){throw error;}
        catch(Exception error){throw new ClosureFailure("CLOSURE_PROVIDER_OUTCOME_UNKNOWN");}
    }
    private record Reply(int status,String contentType,byte[] body){}
    private Reply request(String method,String subject)throws Exception {
        return request(method,subject,false);
    }
    private Reply request(String method,String subject,boolean lifecycleRead)throws Exception {
        if(subject==null||!subject.matches("[A-Za-z0-9_-]{1,128}"))throw new ClosureFailure("CLOSURE_IDENTITY_INVALID",403);
        long deadline=System.nanoTime()+Duration.ofSeconds(12).toNanos();
        String authority;
        try { authority=adminToken(); }
        catch(Exception unavailable) {
            // Python's resolver raises LifecycleError here, so the provider
            // reports an uncertain outcome rather than a rejected user request.
            throw new java.io.IOException("Admin authority unavailable");
        }
        if(authority.isEmpty()||authority.contains("\r")||authority.contains("\n"))throw new ClosureFailure("CLOSURE_PROVIDER_UNAVAILABLE");
        var builder=HttpRequest.newBuilder(URI.create(base+"/users/"+subject)).timeout(Duration.ofNanos(Math.max(1,deadline-System.nanoTime())))
            .header("Authorization","Bearer "+authority).header("Accept","application/json")
            .method(method,HttpRequest.BodyPublishers.noBody());
        if(lifecycleRead)builder.header("Content-Type","application/json");
        var request=builder.build();
        var response=http.send(request,HttpResponse.BodyHandlers.ofInputStream());
        try(var stream=response.body()) {
            byte[] bytes=new byte[0];
            if(response.statusCode()==200||response.statusCode()==404) {
                bytes=read(stream,65537,deadline);
                if(bytes.length>65536)throw new ClosureFailure("CLOSURE_PROVIDER_RESPONSE_INVALID");
            }
            return new Reply(response.statusCode(),response.headers().firstValue("Content-Type").orElse(""),bytes);
        }
    }
    private synchronized String adminToken()throws Exception {
        if(token!=null&&System.nanoTime()<tokenDeadline)return token;
        Path path=Path.of(text("secret_file"));
        if(Files.isSymbolicLink(path)||Files.size(path)>4096)throw new ClosureFailure("CLOSURE_PROVIDER_UNAVAILABLE");
        String secret=Files.readString(path,StandardCharsets.UTF_8).strip();
        if(secret.length()<16||secret.length()>4096)throw new ClosureFailure("CLOSURE_PROVIDER_UNAVAILABLE");
        String form="grant_type=client_credentials&client_id="+URLEncoder.encode(text("client_id"),StandardCharsets.UTF_8)
            +"&client_secret="+URLEncoder.encode(secret,StandardCharsets.UTF_8);
        long deadline=System.nanoTime()+Duration.ofSeconds(10).toNanos();
        var response=http.send(HttpRequest.newBuilder(URI.create(tokenEndpoint)).timeout(Duration.ofSeconds(10))
            .header("Content-Type","application/x-www-form-urlencoded").POST(HttpRequest.BodyPublishers.ofString(form)).build(),HttpResponse.BodyHandlers.ofInputStream());
        try(var stream=response.body()) {
            byte[] raw=read(stream,32769,deadline);if(raw.length>32768)throw new ClosureFailure("CLOSURE_PROVIDER_UNAVAILABLE");
            JsonNode body=JSON.readTree(raw);String value=body.path("access_token").asText();JsonNode ttl=body.path("expires_in");
            if(response.statusCode()!=200||!body.path("access_token").isTextual()||value.isEmpty()||value.contains("\r")||value.contains("\n")
                    ||!ttl.isIntegralNumber()||ttl.asInt()<10||ttl.asInt()>3600)throw new ClosureFailure("CLOSURE_PROVIDER_UNAVAILABLE");
            token=value;tokenDeadline=System.nanoTime()+Duration.ofSeconds(ttl.asInt()-5).toNanos();return token;
        }
    }
    private static byte[] read(java.io.InputStream stream,int maximum,long deadline)throws Exception {
        var task=new java.util.concurrent.FutureTask<byte[]>(()->stream.readNBytes(maximum));Thread.ofVirtual().start(task);
        try{return task.get(Math.max(1,deadline-System.nanoTime()),java.util.concurrent.TimeUnit.NANOSECONDS);}
        finally{if(!task.isDone())task.cancel(true);}
    }
}
