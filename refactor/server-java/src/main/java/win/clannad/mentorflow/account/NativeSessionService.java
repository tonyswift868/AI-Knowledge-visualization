package win.clannad.mentorflow.account;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Types;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import javax.sql.DataSource;
import org.springframework.security.oauth2.jwt.Jwt;
import win.clannad.mentorflow.identity.IdentityFailure;
import win.clannad.mentorflow.identity.IdentitySettings;
import win.clannad.mentorflow.identity.NativeAccessAuthenticator;

/** Python-compatible native opaque sessions backed by the existing 0043 table. */
public final class NativeSessionService implements NativeAccessAuthenticator {
    public static final String PREFIX = "/api/enterprise/auth/native";
    public static final String ACCESS_PREFIX = "mfn1_a_";
    public static final String REFRESH_PREFIX = "mfn1_r_";
    private static final String DENIED = "账号或密码不正确";
    private static final String EXPIRED = "登录已失效，请重新登录。";
    private static final String SUPERSEDED = "账号已在另一台设备登录";
    private static final String UNAVAILABLE = "账号服务暂不可用，请稍后重试。";
    private static final int ACCESS_SECONDS = 300;
    private static final int MAX_TOKEN = 128;
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final SecureRandom RANDOM = new SecureRandom();

    @FunctionalInterface
    public interface CredentialVerifier {
        NativeIdentity authenticate(String username, String password);
    }

    @FunctionalInterface
    public interface AccountStateChecker {
        /** Returns the current Keycloak password-credential binding or throws an IdentityFailure. */
        String currentBinding(String issuer, String subject);
    }

    public record NativeIdentity(String issuer, String subject, String displayName,
                                 String authenticationStrength, String preferredUsername,
                                 String email, String clientId, Instant authenticationTime,
                                 String verificationHandle) {}

    private record Family(String familyId, String issuer, String subject, String refreshDigest,
                          Set<String> usedDigests, Map<String,Object> principal,
                          String accountBinding, Instant deadline, String revokedReason) {}
    private record Access(String familyId, String issuer, String subject, String digest,
                          String refreshDigest, Instant deadline, Map<String,Object> principal) {}
    private record Attempt(int count, long deadline) {}

    private final DataSource dataSource;
    private final String issuer;
    private final IdentitySettings identitySettings;
    private final CredentialVerifier verifier;
    private final AccountStateChecker accountState;
    private final Clock clock;
    private final long ttlSeconds;
    private final ObjectMapper mapper = new ObjectMapper();
    private final Map<String,Access> accessTokens = new ConcurrentHashMap<>();
    private final Map<String,String> accessByFamily = new ConcurrentHashMap<>();
    private final LinkedHashMap<String,Instant> supersededAccess = new LinkedHashMap<>();
    private final Set<String> inflightRefreshes = ConcurrentHashMap.newKeySet();
    private final Map<String,Set<String>> pendingLogins = new HashMap<>();
    private final Map<String,Attempt> attempts = new HashMap<>();
    private final Object processLock = new Object();

    public NativeSessionService(DataSource ds, IdentitySettings settings,
                                CredentialVerifier credentialVerifier,
                                AccountStateChecker stateChecker, Clock serviceClock,
                                long sessionTtlSeconds) {
        this.dataSource = ds;
        this.identitySettings = settings;
        this.issuer = settings.canonicalIssuer;
        this.verifier = credentialVerifier;
        this.accountState = stateChecker;
        this.clock = serviceClock == null ? Clock.systemUTC() : serviceClock;
        if (sessionTtlSeconds < 0) throw new IllegalArgumentException("session TTL must be zero or positive");
        this.ttlSeconds = sessionTtlSeconds;
    }

    public String issuer() {return issuer;}

    public Map<String,Object> login(String username, String password, String peer) {
        throttle(username, peer);
        String ticket=randomHex(16);
        synchronized(processLock){if(pendingLogins.size()>=4096)throw capacity();pendingLogins.put(ticket,new HashSet<>());}
        try{return loginWithTicket(ticket,username,password,peer);}
        finally{synchronized(processLock){pendingLogins.remove(ticket);}}
    }

    private Map<String,Object> loginWithTicket(String ticket,String username,String password,String peer) {
        NativeIdentity verified;
        try {
            verified = verifier.authenticate(username, password);
        } catch (IdentityFailure failure) {
            throw failure;
        } catch (Exception failure) {
            throw unavailable();
        }
        if (verified == null || verified.subject() == null || verified.subject().isBlank()) throw denied();
        String canonical;
        try { canonical = identitySettings.canonicalizeIssuer(verified.issuer(), verified.subject()); }
        catch (IdentityFailure failure) { throw failure; }
        catch (Exception failure) { throw new IdentityFailure("NATIVE_IDENTITY_UNAVAILABLE", "账号服务暂不可用。", 503); }
        if (!issuer.equals(canonical) || !"password_verified".equals(verified.authenticationStrength())
                || !"mentorflow-desktop".equals(verified.clientId()) || verified.authenticationTime() == null
                || verified.verificationHandle() == null || !verified.verificationHandle().matches("[A-Za-z0-9_-]{43}"))
            throw new IdentityFailure("NATIVE_IDENTITY_INVALID", "账号服务响应无法验证。", 503);
        String binding = currentBinding(issuer, verified.subject());
        Instant actualNow = clock.instant();
        Instant now = verified.authenticationTime();
        if(now.isAfter(actualNow)||!now.plusSeconds(ACCESS_SECONDS).isAfter(actualNow))throw denied();
        Instant deadline = ttlSeconds == 0 ? null : actualNow.plusSeconds(ttlSeconds);
        String familyId = randomHex(32);
        String access = ACCESS_PREFIX + randomToken();
        String refresh = REFRESH_PREFIX + randomToken();
        Instant accessDeadline = actualNow.plusSeconds(ACCESS_SECONDS);
        Instant principalExpiry = now.plusSeconds(ACCESS_SECONDS);
        if (principalExpiry.isBefore(accessDeadline)) accessDeadline = principalExpiry;
        if (deadline != null && deadline.isBefore(accessDeadline)) accessDeadline = deadline;
        Map<String,Object> principal = newPrincipal(verified, now, principalExpiry);
        String accessDigest = digest(access), refreshDigest = digest(refresh);
        synchronized (processLock) {
            if(pendingLogins.getOrDefault(ticket,Set.of()).contains(verified.subject()))throw expired();
            try (Connection connection = dataSource.getConnection()) {
                connection.setAutoCommit(false);
                try {
                    lockSubject(connection, issuer, verified.subject());
                    lockSubject(connection,issuer,"__native_session_capacity__");
                    try(PreparedStatement count=connection.prepareStatement(
                            "SELECT count(*) FROM native_session_families WHERE issuer=? AND subject<>? AND revoked_reason IS NULL")){
                        count.setString(1,issuer);count.setString(2,verified.subject());
                        try(ResultSet row=count.executeQuery()){if(row.next()&&row.getLong(1)>=4096)throw capacity();}
                    }
                    try (PreparedStatement update = connection.prepareStatement(
                            "UPDATE native_session_families SET revoked_reason='NATIVE_SESSION_SUPERSEDED' "
                            + "WHERE issuer=? AND subject=? AND revoked_reason IS NULL")) {
                        update.setString(1, issuer); update.setString(2, verified.subject()); update.executeUpdate();
                    }
                    try (PreparedStatement insert = connection.prepareStatement(
                            "INSERT INTO native_session_families "
                            + "(family_id,issuer,subject,refresh_digest,used_digests,principal,account_binding,deadline,revoked_reason) "
                            + "VALUES (?,?,?,?,?::json,?::json,?,?,NULL)")) {
                        insert.setString(1, familyId); insert.setString(2, issuer); insert.setString(3, verified.subject());
                        insert.setString(4, refreshDigest); insert.setString(5, "[]");
                        insert.setString(6, mapper.writeValueAsString(stripVerificationHandle(principal)));
                        insert.setString(7, binding);
                        if (deadline == null) insert.setNull(8, Types.DOUBLE); else insert.setDouble(8, deadline.toEpochMilli() / 1000.0);
                        insert.executeUpdate();
                    }
                    connection.commit();
                } catch (Exception failure) { rollback(connection); throw failure; }
            } catch (Exception failure) { if (failure instanceof IdentityFailure identity) throw identity; throw unavailable(); }
            evictSubjectAccess(verified.subject(),true);
            rememberAccess(familyId, issuer, verified.subject(), accessDigest, refreshDigest, accessDeadline, principal);
        }
        return tokenResult(access, refresh, actualNow, accessDeadline, verified.displayName(), verified.subject());
    }

    public Map<String,Object> refresh(String rawRefresh) {
        if (!validToken(rawRefresh, REFRESH_PREFIX)) throw expired();
        String oldDigest = digest(rawRefresh);
        if (!inflightRefreshes.add(oldDigest)) {
            revokeByDigest(oldDigest);
            throw expired();
        }
        try {
            Family family = findFamilyByDigest(oldDigest, true);
            if (family == null) throw expired();
            if (family.revokedReason() != null) {
                if ("NATIVE_SESSION_SUPERSEDED".equals(family.revokedReason())) throw superseded();
                throw expired();
            }
            if (family.deadline() != null && !family.deadline().isAfter(clock.instant())) {
                revokeFamily(family.familyId(),false); throw expired();
            }
            if (!oldDigest.equals(family.refreshDigest()) || family.usedDigests().contains(oldDigest)) {
                revokeFamily(family.familyId(), false);
                throw expired();
            }
            String binding;
            try { binding = currentBinding(family.issuer(), family.subject()); }
            catch (IdentityFailure failure) {
                if (failure.status == 401) revokeFamily(family.familyId(),false);
                throw failure;
            }
            if (!binding.equals(family.accountBinding())) {
                revokeFamily(family.familyId(), false);
                throw expired();
            }
            Map<String,Object> oldPrincipal = family.principal();
            Instant actualNow = clock.instant();
            Instant now = Instant.ofEpochSecond(actualNow.getEpochSecond());
            Instant accessDeadline = actualNow.plusSeconds(ACCESS_SECONDS);
            Instant principalExpiry = now.plusSeconds(ACCESS_SECONDS);
            if (principalExpiry.isBefore(accessDeadline)) accessDeadline = principalExpiry;
            if (family.deadline() != null && family.deadline().isBefore(accessDeadline)) accessDeadline = family.deadline();
            Map<String,Object> nextPrincipal = new LinkedHashMap<>(oldPrincipal);
            nextPrincipal.put("issued_at", now.toString());
            nextPrincipal.put("expires_at", now.plusSeconds(ACCESS_SECONDS).toString());
            nextPrincipal.put("token_id", randomToken());
            String access = ACCESS_PREFIX + randomToken(), nextRefresh = REFRESH_PREFIX + randomToken();
            String newDigest = digest(nextRefresh), accessDigest = digest(access);
            Set<String> used = new HashSet<>(family.usedDigests()); used.add(oldDigest);
            synchronized (processLock) {
                try (Connection connection = dataSource.getConnection()) {
                    connection.setAutoCommit(false);
                    try {
                        Family current = findFamilyByDigest(connection, oldDigest, true);
                        if (current == null || !current.familyId().equals(family.familyId())
                                || !current.refreshDigest().equals(oldDigest)) {
                            revokeInTransaction(connection, family.familyId(), false);
                            connection.commit(); throw expired();
                        }
                        try (PreparedStatement update = connection.prepareStatement(
                                "UPDATE native_session_families SET refresh_digest=?,used_digests=?::json,principal=?::json,deadline=? "
                                + "WHERE family_id=? AND refresh_digest=? AND revoked_reason IS NULL")) {
                            update.setString(1, newDigest); update.setString(2, mapper.writeValueAsString(used));
                            update.setString(3, mapper.writeValueAsString(stripVerificationHandle(nextPrincipal)));
                            if (family.deadline() == null) update.setNull(4, Types.DOUBLE); else update.setDouble(4, family.deadline().toEpochMilli()/1000.0);
                            update.setString(5, family.familyId()); update.setString(6, oldDigest);
                            if (update.executeUpdate() != 1) { connection.commit(); throw expired(); }
                        }
                        connection.commit();
                    } catch (Exception failure) { rollback(connection); throw failure; }
                } catch (Exception failure) { if (failure instanceof IdentityFailure identity) throw identity; throw unavailable(); }
                removeFamilyAccess(family.familyId());
                rememberAccess(family.familyId(), family.issuer(), family.subject(), accessDigest, newDigest, accessDeadline, nextPrincipal);
            }
            String display = stringValue(oldPrincipal.get("display_name"));
            return tokenResult(access, nextRefresh, actualNow, accessDeadline, display, family.subject());
        } finally { inflightRefreshes.remove(oldDigest); }
    }

    public void logout(String rawRefresh) {
        if (!validToken(rawRefresh, REFRESH_PREFIX)) return;
        String hash = digest(rawRefresh);
        Family family = findFamilyByDigest(hash, false);
        if (family != null) revokeFamily(family.familyId(), false);
    }

    public Jwt session(String authorization) {
        if (authorization == null || !authorization.startsWith("Bearer ") || authorization.indexOf(' ', 7) >= 0)
            throw expired();
        return authenticateAccess(authorization.substring(7));
    }

    @Override
    public Jwt authenticateAccess(String rawToken) {
        if (!validToken(rawToken, ACCESS_PREFIX)) throw expired();
        String tokenHash = digest(rawToken);
        Access access = accessTokens.get(tokenHash);
        if (access == null) {
            synchronized (supersededAccess) {
                Instant deadline=supersededAccess.get(tokenHash);
                if(deadline!=null){if(deadline.isAfter(clock.instant()))throw superseded();supersededAccess.remove(tokenHash);}
            }
            throw expired();
        }
        if (!access.deadline().isAfter(clock.instant())) throw expired();
        Family current = findFamily(access.familyId());
        if (current != null && "NATIVE_SESSION_SUPERSEDED".equals(current.revokedReason())) throw superseded();
        if(current!=null&&current.revokedReason()==null&&current.deadline()!=null&&!current.deadline().isAfter(clock.instant())){revokeFamily(current.familyId(),false);throw expired();}
        if (current == null || current.revokedReason() != null || !current.issuer().equals(issuer)
                || !current.subject().equals(access.subject())) throw expired();
        if (!current.refreshDigest().equals(access.refreshDigest())) { removeFamilyAccess(access.familyId()); throw expired(); }
        // Both backends reload the durable principal on every authentication;
        // verification handles are deliberately not persisted or resurrected.
        Map<String,Object> claims = current.principal();
        return toJwt(rawToken, claims);
    }

    /** Called by credential-reset/logout flows after their authoritative mutation commits. */
    public void revokeSubject(String subject) {
        synchronized(processLock){
            for(Set<String> cancelled:pendingLogins.values())cancelled.add(subject);
            try (Connection connection = dataSource.getConnection()) {
                connection.setAutoCommit(false);
                try {
                    lockSubject(connection, issuer, subject);
                    try (PreparedStatement update = connection.prepareStatement(
                            "UPDATE native_session_families SET revoked_reason='REVOKED' WHERE issuer=? AND subject=? AND revoked_reason IS NULL")) {
                        update.setString(1, issuer); update.setString(2, subject); update.executeUpdate();
                    }
                    connection.commit();
                } catch (Exception failure) { rollback(connection); throw failure; }
            } catch (Exception failure) { throw unavailable(); }
            evictSubjectAccess(subject,false);
        }
    }

    private String currentBinding(String canonicalIssuer, String subject) {
        try {
            String value = accountState.currentBinding(canonicalIssuer, subject);
            if (value == null || value.isBlank()) throw new IllegalStateException();
            return value;
        } catch (IdentityFailure failure) { throw failure; }
        catch (Exception failure) { throw unavailable(); }
    }

    /** Reuse the native attempt counter for authenticated password reauthentication. */
    public void throttle(String username, String peer) {
        long now = clock.millis();
        synchronized (attempts) {
            attempts.entrySet().removeIf(entry -> entry.getValue().deadline() <= now);
            String account = "account:" + digest(username.strip().toLowerCase(java.util.Locale.ROOT));
            String remote = "peer:" + digest(peer == null ? "unknown" : peer);
            String[] keys = {account, remote, "global"}; int[] limits = {5,20,100};
            if (attempts.size() + 2 > 4096) throw rateLimited();
            for (int index=0; index<3; index++) {
                Attempt attempt=attempts.get(keys[index]);
                if (attempt!=null && attempt.count()>=limits[index]) throw rateLimited();
            }
            for (int index=0; index<3; index++) {
                Attempt previous=attempts.get(keys[index]);
                attempts.put(keys[index], new Attempt(previous==null?1:previous.count()+1, previous==null?now+60_000:previous.deadline()));
            }
        }
    }

    private Family findFamilyByDigest(String digest, boolean lock) {
        try (Connection connection=dataSource.getConnection()) { return findFamilyByDigest(connection,digest,lock); }
        catch (Exception failure) { throw unavailable(); }
    }
    private Family findFamilyByDigest(Connection connection,String digest,boolean lock) throws Exception {
        String sql="SELECT family_id,issuer,subject,refresh_digest,used_digests::text,principal::text,account_binding,deadline,revoked_reason "
                +"FROM native_session_families WHERE issuer=? AND (refresh_digest=? OR used_digests::jsonb @> ?::jsonb) ORDER BY family_id LIMIT 1"+(lock?" FOR UPDATE":"");
        try (PreparedStatement query=connection.prepareStatement(sql)) {
            query.setString(1,issuer); query.setString(2,digest); query.setString(3,"[\""+digest+"\"]");
            try(ResultSet row=query.executeQuery()){return row.next()?family(row):null;}
        }
    }
    private Family findFamily(String familyId) {
        try(Connection connection=dataSource.getConnection(); PreparedStatement query=connection.prepareStatement(
                "SELECT family_id,issuer,subject,refresh_digest,principal::text,deadline,revoked_reason FROM native_session_families WHERE family_id=?")) {
            query.setString(1,familyId);
            try(ResultSet row=query.executeQuery()){
                if(!row.next())return null;
                Map<String,Object> principal=mapper.readValue(row.getString(5),new TypeReference<LinkedHashMap<String,Object>>(){});
                double deadline=row.getDouble(6); Instant until=row.wasNull()?null:Instant.ofEpochMilli((long)(deadline*1000));
                return new Family(row.getString(1),row.getString(2),row.getString(3),row.getString(4),Set.of(),principal,null,until,row.getString(7));
            }
        } catch(Exception failure){throw unavailable();}
    }
    private Family family(ResultSet row) throws Exception {
        List<String> used=mapper.readValue(row.getString(5),new TypeReference<List<String>>(){});
        Map<String,Object> principal=mapper.readValue(row.getString(6),new TypeReference<LinkedHashMap<String,Object>>(){});
        double deadline=row.getDouble(8); Instant until=row.wasNull()?null:Instant.ofEpochMilli((long)(deadline*1000));
        return new Family(row.getString(1),row.getString(2),row.getString(3),row.getString(4),new HashSet<>(used),principal,row.getString(7),until,row.getString(9));
    }
    private void revokeByDigest(String digest) { Family family=findFamilyByDigest(digest,false); if(family!=null)revokeFamily(family.familyId(),false); }
    private void revokeFamily(String familyId,boolean superseding) {
        try(Connection connection=dataSource.getConnection()) { connection.setAutoCommit(false); try {revokeInTransaction(connection,familyId,superseding);connection.commit();}catch(Exception e){rollback(connection);throw e;} }
        catch(Exception failure){throw unavailable();}
        synchronized(processLock){removeFamilyAccess(familyId);}
    }
    private void revokeInTransaction(Connection connection,String familyId,boolean superseding)throws SQLException {
        try(PreparedStatement update=connection.prepareStatement("UPDATE native_session_families SET revoked_reason=? WHERE family_id=? AND revoked_reason IS NULL")){
            update.setString(1,superseding?"NATIVE_SESSION_SUPERSEDED":"REVOKED");update.setString(2,familyId);update.executeUpdate();
        }
    }
    private static void lockSubject(Connection connection,String issuer,String subject)throws SQLException {
        try(PreparedStatement lock=connection.prepareStatement("SELECT pg_advisory_xact_lock(hashtextextended(?,0))")){
            lock.setString(1,issuer+"\u001f"+subject);lock.execute();
        }
    }
    private void rememberAccess(String familyId,String issuer,String subject,String digest,String refreshDigest,Instant deadline,Map<String,Object> principal){
        accessTokens.put(digest,new Access(familyId,issuer,subject,digest,refreshDigest,deadline,new LinkedHashMap<>(principal)));accessByFamily.put(familyId,digest);
    }
    private void removeFamilyAccess(String familyId){String old=accessByFamily.remove(familyId);if(old!=null)accessTokens.remove(old);}
    private void evictSubjectAccess(String subject,boolean superseding){
        for(Access value:new ArrayList<>(accessTokens.values()))if(value.subject().equals(subject)){
            if(superseding){synchronized(supersededAccess){Instant deadline=clock.instant().plusSeconds(8*3600);supersededAccess.remove(value.digest());supersededAccess.put(value.digest(),deadline);while(supersededAccess.size()>16384)supersededAccess.remove(supersededAccess.keySet().iterator().next());}}
            removeFamilyAccess(value.familyId());
        }
    }
    private static void rollback(Connection connection){try{connection.rollback();}catch(SQLException ignored){}}

    private Map<String,Object> newPrincipal(NativeIdentity identity,Instant now,Instant expiry){
        Map<String,Object> value=new LinkedHashMap<>();
        value.put("issuer",issuer);value.put("audiences",List.of("mentorflow-api"));value.put("token_id",randomToken());
        value.put("subject",identity.subject());value.put("issued_at",now.toString());value.put("expires_at",expiry.toString());
        value.put("authentication_time",identity.authenticationTime().toString());value.put("authentication_strength",identity.authenticationStrength());
        value.put("not_before",null);value.put("identity_provider_session_id",identity.verificationHandle());
        value.put("preferred_username",identity.preferredUsername());value.put("email",identity.email());
        value.put("display_name",identity.displayName());value.put("client_id",identity.clientId());value.put("actor_kind","HUMAN");
        return value;
    }
    private static Map<String,Object> stripVerificationHandle(Map<String,Object> principal){Map<String,Object> value=new LinkedHashMap<>(principal);value.put("identity_provider_session_id",null);return value;}
    private Map<String,Object> tokenResult(String access,String refresh,Instant now,Instant expiry,String displayName,String subject){
        Map<String,Object> identity=new LinkedHashMap<>();identity.put("issuer",issuer);identity.put("subject",subject);identity.put("display_name",displayName==null||displayName.isBlank()?"MentorFlow":displayName);
        Map<String,Object> result=new LinkedHashMap<>();result.put("schema","mentorflow.native-session.v1");result.put("access_token",access);result.put("refresh_token",refresh);result.put("expires_in",Math.max(0,Duration.between(clock.instant(),expiry).getSeconds()));result.put("identity",identity);return result;
    }
    private Jwt toJwt(String raw,Map<String,Object> source){
        Instant issued=instant(source.get("issued_at")), expires=instant(source.get("expires_at"));
        if(!issuer.equals(source.get("issuer"))||issued==null||expires==null||!expires.isAfter(clock.instant())
                ||!"HUMAN".equals(source.get("actor_kind"))||!"password_verified".equals(source.get("authentication_strength")))throw expired();
        Map<String,Object> claims=new LinkedHashMap<>();claims.put("iss",issuer);claims.put("sub",source.get("subject"));claims.put("jti",source.get("token_id"));
        claims.put("iat",issued);claims.put("exp",expires);claims.put("auth_time",instant(source.get("authentication_time")));
        claims.put("acr",source.get("authentication_strength"));claims.put("aud",source.get("audiences"));claims.put("client_id",source.get("client_id"));
        claims.put("preferred_username",source.get("preferred_username"));claims.put("email",source.get("email"));claims.put("name",source.get("display_name"));claims.put("sid",source.get("identity_provider_session_id"));claims.put("native",true);
        return new Jwt(raw,issued,expires,Map.of("alg","opaque","typ","JWT"),claims);
    }
    private static Instant instant(Object value){if(value instanceof Instant instant)return instant;if(value instanceof String string){try{return Instant.parse(string);}catch(Exception ignored){return null;}}return null;}
    private static String stringValue(Object value){return value instanceof String string?string:null;}
    private static String digest(String value){try{return java.util.HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));}catch(Exception exception){throw new IllegalStateException(exception);}}
    private static String randomToken(){byte[] bytes=new byte[32];RANDOM.nextBytes(bytes);return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);}
    private static String randomHex(int bytes){byte[] value=new byte[bytes];RANDOM.nextBytes(value);return java.util.HexFormat.of().formatHex(value);}
    private static boolean validToken(String token,String prefix){return token!=null&&token.startsWith(prefix)&&token.length()<=MAX_TOKEN&&token.length()>prefix.length();}
    private static IdentityFailure denied(){return new IdentityFailure("NATIVE_CREDENTIALS_INVALID",DENIED,401);}
    private static IdentityFailure expired(){return new IdentityFailure("ENTERPRISE_SESSION_REAUTH_REQUIRED",EXPIRED,401);}
    private static IdentityFailure superseded(){return new IdentityFailure("NATIVE_SESSION_SUPERSEDED",SUPERSEDED,401);}
    private static IdentityFailure unavailable(){return new IdentityFailure("NATIVE_AUTH_UNAVAILABLE",UNAVAILABLE,503);}
    private static IdentityFailure rateLimited(){return new IdentityFailure("NATIVE_RATE_LIMITED","登录尝试过于频繁，请稍后重试。",429);}
    private static IdentityFailure capacity(){return new IdentityFailure("NATIVE_SESSION_CAPACITY","账号服务暂不可用。",503);}
}
