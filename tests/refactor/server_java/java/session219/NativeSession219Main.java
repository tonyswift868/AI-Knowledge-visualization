package session219;

import java.lang.reflect.*;
import java.sql.*;
import java.time.*;
import java.util.*;
import javax.sql.DataSource;
import org.springframework.core.env.Environment;
import win.clannad.mentorflow.account.NativeSessionService;
import win.clannad.mentorflow.identity.IdentitySettings;

public final class NativeSession219Main {
    static final String ISSUER = "https://id.example.test/realms/mentorflow";
    static final String SUBJECT = "fixture-user";
    static final String TOKEN = NativeSessionService.ACCESS_PREFIX + "x".repeat(43);
    static final Instant NOW = Instant.parse("2026-10-28T00:00:00Z");
    static final String HUGE = "[\"" + "a".repeat(7_000_000) + "\"]";
    static final class Row { String refresh = "current-refresh", revoked; boolean usedPayloadRead; String sql; }

    static Object proxy(Class<?> type, InvocationHandler handler) {
        return Proxy.newProxyInstance(type.getClassLoader(), new Class<?>[]{type}, handler);
    }
    static Object defaultValue(Class<?> type) {
        if (!type.isPrimitive()) return null;
        if (type == boolean.class) return false;
        if (type == int.class) return 0;
        if (type == long.class) return 0L;
        if (type == double.class) return 0d;
        return null;
    }
    static DataSource dataSource(Row row) {
        return (DataSource)proxy(DataSource.class, (o,m,a) -> {
            if (m.getName().equals("getConnection")) return proxy(Connection.class, (c,cm,ca) -> {
                if (cm.getName().equals("prepareStatement")) {
                    row.sql = (String)ca[0];
                    return proxy(PreparedStatement.class, (p,pm,pa) -> {
                        if (pm.getName().equals("executeQuery")) {
                            final boolean[] first = {true};
                            return proxy(ResultSet.class, (rs,rm,ra) -> {
                                if (rm.getName().equals("next")) { boolean value = first[0]; first[0] = false; return value; }
                                if (rm.getName().equals("getString")) {
                                    int n=(Integer)ra[0];
                                    if (n==1) return "family-219";
                                    if (n==2) return ISSUER;
                                    if (n==3) return SUBJECT;
                                    if (n==4) return row.refresh;
                                    String principal="{\"issuer\":\""+ISSUER+"\",\"audiences\":[\"mentorflow-api\"],\"token_id\":\"id-219\",\"subject\":\""+SUBJECT+"\",\"issued_at\":\""+NOW+"\",\"expires_at\":\""+NOW.plusSeconds(300)+"\",\"authentication_time\":\""+NOW+"\",\"authentication_strength\":\"password_verified\",\"not_before\":null,\"identity_provider_session_id\":null,\"preferred_username\":null,\"email\":null,\"display_name\":\"Fixture\",\"client_id\":\"mentorflow-desktop\",\"actor_kind\":\"HUMAN\"}";
                                    boolean oldLayout=row.sql.contains("used_digests");
                                    if (oldLayout && n==5) { row.usedPayloadRead=true; return HUGE; }
                                    if (n==(oldLayout?6:5)) return principal;
                                    if (n==(oldLayout?9:7)) return row.revoked;
                                }
                                if (rm.getName().equals("getDouble")) return 0d;
                                if (rm.getName().equals("wasNull")) return true;
                                return defaultValue(rm.getReturnType());
                            });
                        }
                        return defaultValue(pm.getReturnType());
                    });
                }
                if (cm.getName().equals("close")) return null;
                return defaultValue(cm.getReturnType());
            });
            return defaultValue(m.getReturnType());
        });
    }
    static IdentitySettings settings() {
        Map<String,String> values = Map.of("MENTORFLOW_KEYCLOAK_ISSUER",ISSUER,
            "MENTORFLOW_KEYCLOAK_AUDIENCE","mentorflow-api", "MENTORFLOW_KEYCLOAK_CLIENT_ID","mentorflow-desktop",
            "MENTORFLOW_KEYCLOAK_REDIRECT_URI","https://app.example.test/callback");
        return new IdentitySettings((Environment)proxy(Environment.class,(o,m,a)->{
            if (m.getName().equals("getRequiredProperty")) return values.get((String)a[0]);
            if (m.getName().equals("getProperty")) return values.getOrDefault((String)a[0], (String)a[1]);
            return defaultValue(m.getReturnType());
        }));
    }
    static NativeSessionService service(Row row) throws Exception {
        NativeSessionService service = new NativeSessionService(dataSource(row), settings(), (u,p)->null,
            (i,s)->"binding", Clock.fixed(NOW, ZoneOffset.UTC), 0);
        Method digest = NativeSessionService.class.getDeclaredMethod("digest", String.class); digest.setAccessible(true);
        String hash=(String)digest.invoke(null,TOKEN);
        Class<?> access=Arrays.stream(NativeSessionService.class.getDeclaredClasses()).filter(c->c.getSimpleName().equals("Access")).findFirst().orElseThrow();
        Constructor<?> ctor=access.getDeclaredConstructors()[0]; ctor.setAccessible(true);
        Object value=ctor.newInstance("family-219",ISSUER,SUBJECT,hash,"current-refresh",NOW.plusSeconds(300),Map.of());
        Field map=NativeSessionService.class.getDeclaredField("accessTokens"); map.setAccessible(true);
        ((Map<String,Object>)map.get(service)).put(hash,value);
        return service;
    }
    public static void main(String[] args) throws Exception {
        Row row=new Row(); row.sql="";
        var jwt=service(row).authenticateAccess(TOKEN);
        if (!SUBJECT.equals(jwt.getSubject())) throw new AssertionError("principal subject changed");
        if (row.sql.contains("used_digests") || row.usedPayloadRead) throw new AssertionError("full refresh history queried or read");
        row.revoked="REVOKED";
        try { service(row).authenticateAccess(TOKEN); throw new AssertionError("revoked family accepted"); }
        catch (win.clannad.mentorflow.identity.IdentityFailure expected) { if (expected.status != 401) throw expected; }
        row.revoked=null; row.refresh="rotated-refresh";
        try { service(row).authenticateAccess(TOKEN); throw new AssertionError("rotated family accepted"); }
        catch (win.clannad.mentorflow.identity.IdentityFailure expected) { if (expected.status != 401) throw expected; }
        System.out.println("java access auth accepted current family, excluded used_digests, rejected revoked and rotated rows");
    }
}
