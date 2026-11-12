package win.clannad.mentorflow.admin;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.core.StreamReadFeature;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.json.JsonMapper;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.SQLException;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import javax.sql.DataSource;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Service;
import org.springframework.beans.factory.ObjectProvider;
import win.clannad.mentorflow.account.NativeIdentityBridge;
import win.clannad.mentorflow.health.HealthProbe;
import win.clannad.mentorflow.identity.IdentitySettings;
import win.clannad.mentorflow.identity.IdentityFailure;

/** Port of Python AdminOverviewService; only scalar SQL aggregate is run as the app role. */
@Service
public final class AdminOverviewService {
    private static final JsonMapper JSON = JsonMapper.builder().enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
        .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS).build();
    private static final List<String> COUNTERS = List.of("registered_human_users", "registrations_today",
        "registrations_7d", "registrations_30d", "enabled_users", "disabled_users", "email_verified_users",
        "email_unverified_users", "required_action_users", "backend_account_count", "workspace_member_count",
        "identity_account_mismatch_count");
    private final DataSource dataSource;
    private final IdentitySettings identity;
    private final HealthProbe health;
    private final NativeEmailHealthProbe emailHealth;
    private final Environment env;
    private final AtomicBoolean collecting = new AtomicBoolean();
    private String adminAccessToken;
    private long adminTokenDeadline;

    public AdminOverviewService(ObjectProvider<DataSource> dataSource, IdentitySettings identity, HealthProbe health,
            NativeEmailHealthProbe emailHealth, Environment env) {
        this.dataSource=dataSource.getIfAvailable(); this.identity=identity; this.health=health; this.emailHealth=emailHealth; this.env=env;
    }

    public byte[] collect() {
        if (!collecting.compareAndSet(false, true)) throw new IdentityFailure(503,"ADMIN_OVERVIEW_BUSY","Overview is refreshing");
        long started=System.nanoTime();
        try {
            Instant now=Instant.now();
            Map<String,Object> payload=new LinkedHashMap<>(); COUNTERS.forEach(key -> payload.put(key,null));
            Map<String,Object> services=new LinkedHashMap<>();
            for(String name:List.of("keycloak","backend","database","email")) services.put(name,status("unknown","NOT_OBSERVED"));
            services.put("email",status("unavailable","EMAIL_AUTHORITY_UNAVAILABLE"));
            if(health.started()) services.put("backend",status("healthy","RUNTIME_STARTED"));
            else services.put("backend",status("unavailable","RUNTIME_STARTING"));
            CompletableFuture<HealthSnapshot> healthFuture=CompletableFuture.supplyAsync(this::healthSnapshot);
            long usersDeadline=System.nanoTime()+Duration.ofSeconds(6).toNanos();
            CompletableFuture<List<JsonNode>> usersFuture=configuredReader()?CompletableFuture.supplyAsync(()->{
                try{return readUsers();}catch(Exception error){throw new java.util.concurrent.CompletionException(error);}
            }):null;
            try { HealthSnapshot snapshot=healthFuture.get(7,TimeUnit.SECONDS);services.putAll(snapshot.services); }
            catch(Exception unavailable) { healthFuture.cancel(true); }

            List<JsonNode> users=null;
            String statisticsCode="ADMIN_READER_NOT_CONFIGURED";
            if (configuredReader()) {
                try {
                    long remaining=usersDeadline-System.nanoTime();
                    if(remaining<=0)throw new java.util.concurrent.TimeoutException();
                    users=usersFuture.get(remaining,TimeUnit.NANOSECONDS);
                    payload.putAll(aggregate(users,now));
                    services.put("keycloak",status("healthy","ADMIN_READ_OK")); statisticsCode="ADMIN_READ_OK";
                } catch (Exception unavailable) {
                    usersFuture.cancel(true);
                    users=null;
                    statisticsCode="ADMIN_READ_FAILED";
                    if (!"healthy".equals(value(services.get("keycloak"),"status"))) services.put("keycloak",status("unavailable",statisticsCode));
                }
            }
            Map<String,Object> statistics=status(users==null?"unavailable":"healthy",statisticsCode);
            try {
                if(dataSource==null)throw new SQLException("Database is not configured");
                Map<String,Object> counts=backendCounts(users==null?Set.of():humanSubjects(users));
                payload.putAll(counts);
                if(users==null) payload.put("identity_account_mismatch_count",null);
                services.put("database",status("healthy","CANONICAL_AGGREGATE_OK"));
            } catch(Exception unavailable) {
                services.put("database",status("unavailable","CANONICAL_AGGREGATE_UNAVAILABLE"));
            }
            if(System.nanoTime()-started>Duration.ofSeconds(10).toNanos())
                throw new IdentityFailure(503,"ADMIN_OVERVIEW_TIMEOUT","Administrator overview timed out");
            boolean complete=COUNTERS.stream().allMatch(key -> payload.get(key)!=null);
            payload.put("schema","mentorflow.admin-overview.v1"); payload.put("services",services);
            payload.put("data_status",complete?"current":"unavailable");
            String observed=formatUtc(now);
            payload.put("refreshed_at",complete?observed:null); payload.put("observed_at",observed);
            payload.put("timezone","UTC"); payload.put("refresh_seconds",60); payload.put("identity_statistics",statistics);
            return JSON.writeValueAsBytes(payload);
        } catch(IdentityFailure known) { throw known; }
        catch(Exception failure) { throw new IdentityFailure("ADMIN_OVERVIEW_UNAVAILABLE","Administrator overview is unavailable",503,failure); }
        finally { collecting.set(false); }
    }

    private record HealthSnapshot(Map<String,Object> services) {}
    private HealthSnapshot healthSnapshot() {
        Map<String,Object> result=new LinkedHashMap<>();
        try {
            for(HealthProbe.Dependency dependency:health.dependencies()) {
                String target=switch(dependency.name()){case "postgres"->"database";case "keycloak_discovery"->"keycloak";case "email"->"email";default->null;};
                if(target!=null) result.put(target,status(dependency.ready()?"healthy":"unavailable",dependency.code()));
            }
        } catch(Exception ignored) { }
        result.put("email",emailHealth.sample());
        return new HealthSnapshot(result);
    }

    private boolean configuredReader() { return !env.getProperty("MENTORFLOW_ADMIN_OVERVIEW_CONFIG","").isEmpty(); }
    private List<JsonNode> readUsers() throws Exception {
        String dedicated=env.getProperty("MENTORFLOW_ADMIN_OVERVIEW_CONFIG","");
        if(dedicated.isEmpty())throw new IllegalArgumentException();
        Path file=Path.of(dedicated);
        if(!file.isAbsolute()||Files.isSymbolicLink(file)||Files.size(file)>8192) throw new IllegalArgumentException();
        JsonNode config=JSON.readTree(Files.readAllBytes(file));
        if(config==null||!config.isObject())throw new IllegalArgumentException();
        Set<String> required=Set.of("origin","realm","client_id","secret_file","ca_file");
        Set<String> optional=Set.of("client_cert_file","client_key_file");
        Set<String> fields=new HashSet<>();config.fieldNames().forEachRemaining(fields::add);
        Set<String> allowed=new HashSet<>(required);allowed.addAll(optional);
        if(!fields.containsAll(required)||!allowed.containsAll(fields)) throw new IllegalArgumentException();
        String origin=config.path("origin").asText(),realm=config.path("realm").asText(),clientId=config.path("client_id").asText();
        URI originUri=URI.create(origin); URI expected=URI.create(identity.jwksUri.replaceFirst("/protocol/openid-connect/certs$",""));
        if(!"https".equals(originUri.getScheme())||originUri.getHost()==null||originUri.getUserInfo()!=null
            ||originUri.getQuery()!=null||originUri.getFragment()!=null||!List.of("","/").contains(originUri.getPath())
            ||!realm.matches("[A-Za-z0-9_-]{1,255}")||!clientId.matches("[A-Za-z0-9_-]{1,255}")
            ||!(origin.replaceAll("/+$","")+"/realms/"+realm).equals(expected.toString())) throw new IllegalArgumentException();
        String cert=pythonTruthy(config.get("client_cert_file"))?config.path("client_cert_file").asText():null;
        String key=pythonTruthy(config.get("client_key_file"))?config.path("client_key_file").asText():null;
        if((cert==null)!=(key==null)) throw new IllegalArgumentException();
        String ca=pythonTruthy(config.get("ca_file"))?config.path("ca_file").asText():null;
        HttpClient client=HttpClient.newBuilder().sslContext(NativeIdentityBridge.tls(ca,cert,key))
            .connectTimeout(Duration.ofSeconds(10)).followRedirects(HttpClient.Redirect.NEVER).build();
        Path secretPath=Path.of(config.path("secret_file").asText());
        if(Files.isSymbolicLink(secretPath)||Files.size(secretPath)>4096)throw new IllegalArgumentException();
        String secret=Files.readString(secretPath,StandardCharsets.UTF_8).strip();
        if(secret.length()<16||secret.length()>4096)throw new IllegalArgumentException();
        String tokenUrl=origin.replaceAll("/+$","")+"/realms/"+enc(realm)+"/protocol/openid-connect/token";
        long deadline=System.nanoTime()+Duration.ofSeconds(6).toNanos();
        String token=adminToken(client,tokenUrl,clientId,secret);
        var users=new ArrayList<JsonNode>(); Set<String> seen=new HashSet<>();
        for(int first=0;first<=100_000;first+=50) {
            long remaining=deadline-System.nanoTime();if(remaining<=0)throw new java.net.http.HttpTimeoutException("Admin read deadline exceeded");
            String url=origin.replaceAll("/+$","")+"/admin/realms/"+enc(realm)+"/users?first="+first+"&max=50&briefRepresentation=false";
            JsonNode page=send(client,HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofNanos(remaining))
                .header("Accept","application/json").header("Authorization","Bearer "+token).GET().build(),65536,Duration.ofNanos(remaining));
            if(!page.isArray()||page.size()>50)throw new IllegalArgumentException();
            for(JsonNode user:page) {
                if(!user.path("id").isTextual())throw new IllegalArgumentException();
                String id=user.path("id").textValue(); if(id.isEmpty()||!seen.add(id))throw new IllegalArgumentException();
                if(seen.size()>100_000)throw new IllegalArgumentException();
                if(pythonTruthy(user.get("serviceAccountClientId")))continue;
                if(!user.path("enabled").isBoolean()||!user.path("emailVerified").isBoolean()
                    ||!user.path("createdTimestamp").isIntegralNumber()
                    ||(user.has("requiredActions")&&!user.path("requiredActions").isArray()))throw new IllegalArgumentException();
                users.add(user);
            }
            if(page.size()<50)return users;
        }
        throw new IllegalArgumentException();
    }

    private synchronized String adminToken(HttpClient client,String tokenUrl,String clientId,String secret) throws Exception {
        if(adminAccessToken!=null&&System.nanoTime()<adminTokenDeadline)return adminAccessToken;
        adminAccessToken=null;adminTokenDeadline=0;
        String form="grant_type=client_credentials&client_id="+enc(clientId)+"&client_secret="+enc(secret);
        JsonNode reply=send(client,HttpRequest.newBuilder(URI.create(tokenUrl)).timeout(Duration.ofSeconds(6))
            .header("Content-Type","application/x-www-form-urlencoded").POST(HttpRequest.BodyPublishers.ofString(form)).build(),32768,Duration.ofSeconds(6));
        String token=reply.path("access_token").asText();
        int ttl=reply.path("expires_in").isIntegralNumber()?reply.path("expires_in").asInt(-1):-1;
        if(token.isEmpty()||token.contains("\r")||token.contains("\n")||ttl<10||ttl>3600)throw new IllegalArgumentException();
        adminAccessToken=token;adminTokenDeadline=System.nanoTime()+Duration.ofSeconds(ttl-5L).toNanos();return token;
    }

    private JsonNode send(HttpClient client,HttpRequest request,int maxBytes,Duration bodyTimeout) throws Exception {
        HttpResponse<java.io.InputStream> response=client.send(request,HttpResponse.BodyHandlers.ofInputStream());
        try(java.io.InputStream stream=response.body()) {
            if(response.statusCode()!=200)throw new IllegalStateException();
            var task=new java.util.concurrent.FutureTask<>(()->stream.readNBytes(maxBytes+1));Thread.ofVirtual().start(task);
            byte[] body;
            try{body=task.get(Math.max(1,bodyTimeout.toNanos()),TimeUnit.NANOSECONDS);}
            catch(Exception timeout){try{stream.close();}catch(Exception ignored){}task.cancel(true);throw timeout;}
            if(body.length>maxBytes)throw new IllegalStateException();
            return JSON.readTree(body);
        }
    }
    private static String enc(String value){return URLEncoder.encode(value,StandardCharsets.UTF_8);}

    private Map<String,Object> aggregate(List<JsonNode> users,Instant now) {
        Set<String> subjects=new HashSet<>(); var created=new ArrayList<Instant>(); int enabled=0,verified=0,actions=0;
        for(JsonNode user:users) {
            String id=user.path("id").asText();if(!subjects.add(id))throw new IllegalArgumentException();
            JsonNode timestamp=user.path("createdTimestamp");
            if(!timestamp.canConvertToLong())throw new IllegalArgumentException();
            Instant time=Instant.ofEpochMilli(timestamp.longValue());
            int year=time.atOffset(ZoneOffset.UTC).getYear();if(year<1||year>9999)throw new IllegalArgumentException();
            created.add(time);
            if(user.path("enabled").booleanValue())enabled++;if(user.path("emailVerified").booleanValue())verified++;
            if(user.path("requiredActions").isArray()&&!user.path("requiredActions").isEmpty())actions++;
        }
        Instant seven=now.minus(Duration.ofDays(7)),thirty=now.minus(Duration.ofDays(30));
        long today=created.stream().filter(t->t.atOffset(ZoneOffset.UTC).toLocalDate().equals(now.atOffset(ZoneOffset.UTC).toLocalDate())&&!t.isAfter(now)).count();
        long days7=created.stream().filter(t->!t.isBefore(seven)&&!t.isAfter(now)).count();
        long days30=created.stream().filter(t->!t.isBefore(thirty)&&!t.isAfter(now)).count();
        Map<String,Object> result=new LinkedHashMap<>();result.put("registered_human_users",subjects.size());result.put("registrations_today",today);
        result.put("registrations_7d",days7);result.put("registrations_30d",days30);result.put("enabled_users",enabled);
        result.put("disabled_users",subjects.size()-enabled);result.put("email_verified_users",verified);
        result.put("email_unverified_users",subjects.size()-verified);result.put("required_action_users",actions);return result;
    }
    private static Set<String> humanSubjects(List<JsonNode> users){Set<String> result=new HashSet<>();for(JsonNode user:users)result.add(user.path("id").asText());return result;}
    private Map<String,Object> backendCounts(Set<String> subjects) throws Exception {
        try(Connection connection=dataSource.getConnection()) {
            connection.setAutoCommit(false);
            try(var statement=connection.createStatement()){statement.execute("SET TRANSACTION READ ONLY");statement.execute("SET LOCAL statement_timeout = '2000ms'");}
            try(var statement=connection.prepareStatement("SELECT public.mentorflow_admin_account_counts(?, ?)")) {
                statement.setString(1,identity.canonicalIssuer.replaceAll("/+$",""));
                java.sql.Array array=connection.createArrayOf("text",subjects.stream().sorted().toArray(String[]::new));statement.setArray(2,array);
                try(var row=statement.executeQuery()) {
                    if(!row.next())throw new SQLException();String json=row.getString(1);
                    JsonNode counts=JSON.readTree(json);if(!counts.isObject())throw new SQLException();Map<String,Object> result=new LinkedHashMap<>();
                    for(String key:List.of("backend_account_count","workspace_member_count","identity_account_mismatch_count")) {
                        if(!counts.path(key).isIntegralNumber())throw new SQLException();result.put(key,counts.path(key).longValue());
                    }
                    connection.commit();array.free();return result;
                }
            } catch(Exception failure){connection.rollback();throw failure;}
        }
    }
    private static Map<String,Object> status(String state,String code){Map<String,Object> value=new LinkedHashMap<>();value.put("status",state);value.put("code",code);return value;}
    private static String value(Object map,String key){return map instanceof Map<?,?> value?String.valueOf(value.get(key)):"";}
    private static boolean pythonTruthy(JsonNode value){if(value==null||value.isNull()||value.isMissingNode())return false;if(value.isBoolean())return value.booleanValue();if(value.isNumber())return value.decimalValue().signum()!=0;if(value.isTextual())return !value.textValue().isEmpty();return value.isArray()||value.isObject();}
    private static String formatUtc(Instant value){return java.time.format.DateTimeFormatter.ofPattern("uuuu-MM-dd'T'HH:mm:ss.SSSSSSxxx")
        .withZone(ZoneOffset.UTC).format(value.truncatedTo(java.time.temporal.ChronoUnit.MICROS));}
}
