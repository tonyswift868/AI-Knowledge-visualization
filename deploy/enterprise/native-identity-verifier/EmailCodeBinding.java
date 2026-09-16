package win.clannad.mentorflow;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import jakarta.persistence.LockModeType;
import org.keycloak.common.util.Time;
import org.keycloak.email.EmailSenderProvider;
import org.keycloak.models.*;
import org.keycloak.connections.jpa.JpaConnectionProvider;
import org.keycloak.models.jpa.entities.UserEntity;
import org.keycloak.models.utils.KeycloakModelUtils;
import org.keycloak.events.EventBuilder;
import org.keycloak.events.EventType;

/** Authenticated self-service email verification. Caller is the pinned Backend;
 * subject must come from its revalidated native actor, never public request JSON.
 * Separate purpose namespace from password recovery; no credential/session minting.
 */
final class EmailCodeBinding {
    private static final int TTL = 600;
    private static String hash(String s) { return ResetCompletions.hash(s); }
    private static String prefix(KeycloakSession s) { return "mf-email-binding-v1:"+s.getContext().getRealm().getId()+":"; }
    private static Map<String,String> outcome(String s) { return Map.of("status",s); }
    private static String email(UserModel u) { return Objects.toString(u.getEmail(), ""); }
    private static UserModel user(KeycloakSession s, String subject) {
        if(subject==null || subject.isBlank() || subject.length()>128)return null;
        var u=s.users().getUserById(s.getContext().getRealm(),subject);
        return u==null || !u.isEnabled() || u.getServiceAccountClientLink()!=null || u.getFederationLink()!=null ? null : u;
    }
    static Map<String,String> profile(KeycloakSession s,String subject) {
        var u=user(s,subject);
        return u==null ? outcome("INVALID") : Map.of("status","PROFILE","email",email(u),
            "email_verified",Boolean.toString(!email(u).isEmpty() && u.isEmailVerified()));
    }
    static Map<String,String> request(KeycloakSession s,String subject,String ticket,String target) throws Exception {
        var u=user(s,subject);
        if(u==null || !EmailCodeReset.token(ticket) || target==null || target.length()>254
                || !target.equals(target.trim()) || !target.matches("[^\\s@]+@[^\\s@]+\\.[^\\s@]+"))return outcome("INVALID");
        String original=email(u);
        if(!original.isEmpty() && !original.equalsIgnoreCase(target))return outcome("EMAIL_ALREADY_BOUND");
        if(!original.isEmpty() && u.isEmailVerified())return outcome("EMAIL_ALREADY_VERIFIED");
        // Preserve the authority's spelling of an already bound address.
        if(!original.isEmpty())target=original;
        String p=prefix(s),key=p+"code:"+hash(ticket);var store=s.singleUseObjects();
        if(!store.putIfAbsent(key+":reserved",TTL+60))return outcome("INVALID");
        if(!store.putIfAbsent(p+"send:"+hash(subject),60)
                || !store.putIfAbsent(p+"mail:"+hash(target.toLowerCase(Locale.ROOT)),60))return outcome("RATE_LIMITED");
        boolean allowed=false;
        for(int i=0;i<10;i++)if(store.putIfAbsent(p+"hour:"+Time.currentTime()/3600+":"+hash(subject)+":"+i,3660)){allowed=true;break;}
        if(!allowed)return outcome("RATE_LIMITED");
        String code=EmailCodeReset.randomCode();
        store.put(key,TTL+60,Map.of("subject",subject,"original",original,"target",target,
            "binding",EmailCodeReset.credentialBinding(u),"code",hash(ticket+"\0"+code),
            "expires",Integer.toString(Time.currentTime()+TTL)));
        try {
            s.getProvider(EmailSenderProvider.class).send(s.getContext().getRealm().getSmtpConfig(),target,
                "MentorFlow 邮箱验证验证码","您的 MentorFlow 邮箱验证验证码是："+code+
                "\n有效期为 10 分钟。请在 MentorFlow 账号设置中输入。此验证码不能用于重置密码。",null);
        } catch(Exception failure) { store.remove(key);throw failure; }
        return outcome("SENT");
    }
    static Map<String,String> verify(KeycloakSession s,String subject,String ticket,String code) {
        if(!EmailCodeReset.token(ticket) || code==null || !code.matches("[0-9]{6}"))return outcome("INVALID");
        String key=prefix(s)+"code:"+hash(ticket);var store=s.singleUseObjects();var row=store.get(key);
        var u=user(s,subject);
        if(row==null || u==null || !subject.equals(row.get("subject"))
                || Integer.parseInt(row.get("expires"))<=Time.currentTime()
                || !EmailCodeReset.credentialBinding(u).equals(row.get("binding"))
                || store.contains(key+":try:4") || store.contains(key+":used"))return outcome("INVALID");
        if(!MessageDigest.isEqual(hash(ticket+"\0"+code).getBytes(StandardCharsets.US_ASCII),
                row.get("code").getBytes(StandardCharsets.US_ASCII))){
            for(int i=0;i<5;i++)if(store.putIfAbsent(key+":try:"+i,TTL+60))break;
            return outcome("INVALID");
        }
        // Serialize competing initial binds in the identity's existing DB transaction.
        // Refresh under lock: a cached UserModel alone cannot prevent last-writer-wins.
        var em=s.getProvider(JpaConnectionProvider.class).getEntityManager();
        var entity=em.find(UserEntity.class,subject,LockModeType.PESSIMISTIC_WRITE);
        if(entity==null)return outcome("INVALID");
        em.refresh(entity,LockModeType.PESSIMISTIC_WRITE);
        if(!s.getContext().getRealm().getId().equals(entity.getRealmId()) || !entity.isEnabled())return outcome("INVALID");
        if(!Objects.toString(entity.getEmail(),"").equals(row.get("original")))return outcome("EMAIL_ALREADY_BOUND");
        if(entity.isEmailVerified() && !row.get("original").isEmpty())return outcome("EMAIL_ALREADY_VERIFIED");
        if(!AtomicActionTokens.claim(s,key+":used",Integer.parseInt(row.get("expires"))))return outcome("INVALID");
        u.setEmail(row.get("target"));u.setEmailVerified(true);
        u.removeRequiredAction(UserModel.RequiredAction.VERIFY_EMAIL);
        var factory=s.getKeycloakSessionFactory();
        s.getTransactionManager().enlistAfterCompletion(new AbstractKeycloakTransaction(){
            protected void commitImpl(){KeycloakModelUtils.runJobInTransaction(factory,fresh ->
                fresh.singleUseObjects().put(key+":completed",TTL+60,Map.of("subject",subject,"target",row.get("target"))));}
            protected void rollbackImpl() { }
        });
        new EventBuilder(s.getContext().getRealm(),s,s.getContext().getConnection()).event(EventType.VERIFY_EMAIL)
            .detail("mentorflow_email","binding-code").user(u).success();
        return outcome("PENDING_COMMIT");
    }
    static Map<String,String> status(KeycloakSession s,String subject,String ticket) {
        if(!EmailCodeReset.token(ticket))return outcome("INVALID");
        var fact=s.singleUseObjects().get(prefix(s)+"code:"+hash(ticket)+":completed");
        var u=user(s,subject);
        if(fact==null || u==null || !subject.equals(fact.get("subject")) || !u.isEmailVerified()
                || !email(u).equals(fact.get("target")))return outcome("INVALID");
        return profile(s,subject);
    }
}
