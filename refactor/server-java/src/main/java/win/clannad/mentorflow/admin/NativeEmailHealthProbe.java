package win.clannad.mentorflow.admin;

import com.fasterxml.jackson.core.StreamReadFeature;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.json.JsonMapper;
import java.net.Proxy;
import java.net.ProxySelector;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.LinkedHashMap;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;
import win.clannad.mentorflow.account.NativeIdentityBridge;
import win.clannad.mentorflow.identity.IdentitySettings;

/** Cached view of the identity authority's SMTP probe, matching the Python registry adapter. */
@Component
public final class NativeEmailHealthProbe {
    private static final JsonMapper JSON=JsonMapper.builder().enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
        .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS).build();
    private static final long CACHE_NANOS=Duration.ofSeconds(60).toNanos();
    private final Environment env;
    private final IdentitySettings settings;
    private long deadline;
    private Map<String,String> cached=status("unavailable","EMAIL_AUTHORITY_UNAVAILABLE");

    NativeEmailHealthProbe(Environment env,IdentitySettings settings){this.env=env;this.settings=settings;}

    public synchronized Map<String,String> sample() {
        if(System.nanoTime()<deadline)return cached;
        Map<String,String> value=status("unavailable","EMAIL_TRANSPORT_UNAVAILABLE");
        try {
            String endpoint=env.getProperty("MENTORFLOW_NATIVE_IDENTITY_ENDPOINT","");
            if(endpoint.isEmpty()) {
                cached=status("unavailable","EMAIL_AUTHORITY_NOT_CONFIGURED");
                deadline=System.nanoTime()+CACHE_NANOS;return cached;
            }
            URI uri=URI.create(endpoint);
            String realmPath=URI.create(settings.jwksUri).getPath().replaceFirst("/protocol/openid-connect/certs$","");
            if(!"https".equals(uri.getScheme())||uri.getHost()==null||uri.getUserInfo()!=null||uri.getQuery()!=null
                ||uri.getFragment()!=null||!Set.of("/mentorflow-native-identity/v1",realmPath+"/mentorflow-native-identity/v1").contains(uri.getPath()))
                throw new IllegalArgumentException();
            String cert=env.getRequiredProperty("MENTORFLOW_NATIVE_IDENTITY_CERT_FILE");
            String key=env.getRequiredProperty("MENTORFLOW_NATIVE_IDENTITY_KEY_FILE");
            var client=HttpClient.newBuilder().sslContext(NativeIdentityBridge.tls(
                env.getRequiredProperty("MENTORFLOW_NATIVE_IDENTITY_CA_FILE"),cert,key))
                .connectTimeout(Duration.ofSeconds(5)).proxy(new ProxySelector(){
                    @Override public List<Proxy> select(URI ignored){return List.of(Proxy.NO_PROXY);}
                    @Override public void connectFailed(URI uri,java.net.SocketAddress address,java.io.IOException error){}
                }).followRedirects(HttpClient.Redirect.NEVER).build();
            byte[] request="{\"operation\":\"email-transport-health\"}".getBytes(StandardCharsets.UTF_8);
            HttpRequest http=HttpRequest.newBuilder(uri).timeout(Duration.ofSeconds(5)).header("Content-Type","application/json")
                .POST(HttpRequest.BodyPublishers.ofByteArray(request)).build();
            HttpResponse<java.io.InputStream> response=client.send(http,HttpResponse.BodyHandlers.ofInputStream());
            try(var stream=response.body()) {
                byte[] raw=boundedRead(stream,513);
                if(response.statusCode()!=200||raw.length>512)throw new IllegalArgumentException();
                JsonNode body=JSON.readTree(raw);
                if(body==null||!body.isObject()||body.size()!=2||!body.has("status")||!body.path("status").isTextual()
                    ||!body.has("code")||!body.path("code").isTextual())throw new IllegalArgumentException();
                String status=body.path("status").asText(),code=body.path("code").asText();
                if("READY".equals(status)&&"EMAIL_TLS_AUTH_READY".equals(code))value=status("healthy",code);
                else if("UNAVAILABLE".equals(status)&&Set.of("EMAIL_AUTHORITY_NOT_CONFIGURED","EMAIL_CONFIGURATION_UNAVAILABLE","EMAIL_TRANSPORT_UNAVAILABLE").contains(code))
                    value=status("unavailable",code);
                else throw new IllegalArgumentException();
            }
        } catch(Exception unavailable) { }
        cached=value;deadline=System.nanoTime()+CACHE_NANOS;return cached;
    }
    private static Map<String,String> status(String state,String code){var value=new LinkedHashMap<String,String>();value.put("status",state);value.put("code",code);return value;}
    private static byte[] boundedRead(java.io.InputStream stream,int max) throws Exception {
        var task=new java.util.concurrent.FutureTask<>(()->stream.readNBytes(max));Thread.ofVirtual().start(task);
        try{return task.get(5,java.util.concurrent.TimeUnit.SECONDS);}
        catch(Exception timeout){try{stream.close();}catch(Exception ignored){}task.cancel(true);throw timeout;}
    }
}
