package win.clannad.mentorflow;

import java.security.MessageDigest;
import java.security.SecureRandom;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.keycloak.common.util.Time;
import org.keycloak.email.EmailSenderProvider;
import org.keycloak.models.*;
import org.keycloak.policy.PasswordPolicyManagerProvider;
import org.keycloak.models.utils.KeycloakModelUtils;
import org.keycloak.events.EventBuilder;
import org.keycloak.events.EventType;

/** Backend-pinned email recovery, using Keycloak's existing expiring/atomic stores. */
final class EmailCodeReset {
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final int CODE_TTL = 600, PROOF_TTL = 300, ATTEMPTS = 5;
    private static String hash(String s) { return ResetCompletions.hash(s); }
    private static String prefix(KeycloakSession s) { return "mf-email-reset-v1:" + s.getContext().getRealm().getId() + ":"; }
    static boolean token(String s) { return s != null && s.matches("[A-Za-z0-9_-]{43}"); }
    static String randomCode() { return String.format(Locale.ROOT,"%06d",RANDOM.nextInt(1000000)); }
    private static String randomToken() {
        byte[] b = new byte[32]; RANDOM.nextBytes(b);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(b);
    }
    static String credentialBinding(UserModel user) {
        var passwords = user.credentialManager().getStoredCredentialsByTypeStream("password").toList();
        if (passwords.size() != 1) throw new IllegalStateException();
        var c = passwords.get(0);
        return hash(c.getId()+"\n"+c.getCreatedDate()+"\n"+c.getCredentialData()+"\n"+c.getSecretData());
    }
    private static Map<String,String> outcome(String status) { return Map.of("status",status); }
    static Map<String,String> request(KeycloakSession s, String ticket, String email) throws Exception {
        if (!token(ticket) || email == null || email.length()>254 || !email.equals(email.trim())
                || !email.matches("[^\\s@]+@[^\\s@]+\\.[^\\s@]+")) return outcome("INVALID");
        var realm=s.getContext().getRealm(); var store=s.singleUseObjects(); String p=prefix(s);
        String key=p+"code:"+hash(ticket);
        if (!store.putIfAbsent(key+":reserved",CODE_TTL+60)) return outcome("INVALID");
        String normalized=email.toLowerCase(Locale.ROOT);
        // Shared authority-side sending limits: same external response for every mailbox.
        if (!store.putIfAbsent(p+"send:"+hash(normalized),60)) return outcome("ACCEPTED");
        int hour=Time.currentTime()/3600;
        boolean allowed=false;
        for(int i=0;i<10;i++) if(store.putIfAbsent(p+"hour:"+hour+":"+hash(normalized)+":"+i,3660)){allowed=true;break;}
        if(!allowed) return outcome("ACCEPTED");
        var user=s.users().getUserByEmail(realm,email);
        if(user==null || !user.isEnabled() || !user.isEmailVerified() || user.getServiceAccountClientLink()!=null
                || user.getEmail()==null || !normalized.equals(user.getEmail().toLowerCase(Locale.ROOT))) return outcome("ACCEPTED");
        String code=randomCode();
        int expires=Time.currentTime()+CODE_TTL;
        store.put(key,CODE_TTL+60,Map.of("subject",user.getId(),"email",hash(normalized),
            "binding",credentialBinding(user),"code",hash(ticket+"\0"+code),"expires",Integer.toString(expires)));
        String text="您的 MentorFlow 密码重置验证码是："+code+"\n有效期为 10 分钟。请在 MentorFlow 登录窗口输入验证码。\n请勿向他人提供验证码。如非本人操作，请忽略此邮件。";
        try {
            s.getProvider(EmailSenderProvider.class).send(realm.getSmtpConfig(),user,
                "MentorFlow 密码重置验证码",text,null);
        } catch(Exception failure) { store.remove(key); throw failure; }
        return Map.of("status","ACCEPTED","delivery","SUBMITTED");
    }
    private static UserModel boundUser(KeycloakSession s, Map<String,String> row) {
        if(row==null || Integer.parseInt(row.get("expires"))<=Time.currentTime()) return null;
        var user=s.users().getUserById(s.getContext().getRealm(),row.get("subject"));
        if(user==null || !user.isEnabled() || !user.isEmailVerified() || user.getEmail()==null
                || !hash(user.getEmail().toLowerCase(Locale.ROOT)).equals(row.get("email"))
                || !credentialBinding(user).equals(row.get("binding"))) return null;
        return user;
    }
    static Map<String,String> verify(KeycloakSession s, String ticket, String code) {
        if(!token(ticket) || code==null || !code.matches("[0-9]{6}")) return outcome("INVALID");
        String key=prefix(s)+"code:"+hash(ticket);var store=s.singleUseObjects();var row=store.get(key);
        if(boundUser(s,row)==null || store.contains(key+":try:4") || store.contains(key+":used")) return outcome("INVALID");
        if(!MessageDigest.isEqual(hash(ticket+"\0"+code).getBytes(StandardCharsets.US_ASCII),row.get("code").getBytes(StandardCharsets.US_ASCII))){
            for(int i=0;i<ATTEMPTS;i++) if(store.putIfAbsent(key+":try:"+i,CODE_TTL+60))break;
            return outcome("INVALID");
        }
        if(!AtomicActionTokens.claim(s,key+":used",Integer.parseInt(row.get("expires")))) return outcome("INVALID");
        String proof=randomToken(); var grant=new HashMap<>(row);grant.remove("code");
        grant.put("transaction",hash(ticket));grant.put("expires",Integer.toString(Time.currentTime()+PROOF_TTL));
        store.put(prefix(s)+"proof:"+hash(proof),PROOF_TTL+60,grant);
        return Map.of("status","VERIFIED","reset_proof",proof,"expires_in",Integer.toString(PROOF_TTL));
    }
    static Map<String,String> complete(KeycloakSession s, String proof, String password) {
        if(!token(proof)) return outcome("INVALID");
        String key=prefix(s)+"proof:"+hash(proof);var row=s.singleUseObjects().get(key);var user=boundUser(s,row);
        if(user==null || s.singleUseObjects().contains(key+":used") || s.revokedTokens().contains(key+":used")) return outcome("INVALID");
        if(password==null || password.length()<8 || password.length()>128) return outcome("PASSWORD_POLICY_REJECTED");
        // Compare against the authority's current salted hash; do not invent password history.
        if(user.credentialManager().isValid(UserCredentialModel.password(password))) return outcome("SAME_CURRENT_PASSWORD");
        if(s.getProvider(PasswordPolicyManagerProvider.class).validate(s.getContext().getRealm(),user,password)!=null)
            return outcome("PASSWORD_POLICY_REJECTED");
        if(!AtomicActionTokens.claim(s,key+":used",Integer.parseInt(row.get("expires")))) return outcome("INVALID");
        if(!AtomicActionTokens.claim(s,prefix(s)+"credential:"+hash(user.getId()+"\0"+row.get("binding")),
                Integer.parseInt(row.get("expires")))) return outcome("INVALID");
        if(!user.credentialManager().updateCredential(UserCredentialModel.password(password))){
            s.getTransactionManager().setRollbackOnly();return outcome("UNAVAILABLE");
        }
        var realm=s.getContext().getRealm();
        user.removeRequiredAction(UserModel.RequiredAction.UPDATE_PASSWORD);
        s.sessions().removeUserSessions(realm,user);
        s.sessions().getOfflineUserSessionsStream(realm,user).toList().forEach(u->s.sessions().removeOfflineUserSession(realm,u));
        // Native handles bind the password hash and therefore fail their next revalidation.
        String subject=user.getId();var factory=s.getKeycloakSessionFactory();
        s.getTransactionManager().enlistAfterCompletion(new AbstractKeycloakTransaction(){
            protected void commitImpl(){
                KeycloakModelUtils.runJobInTransaction(factory,fresh -> fresh.singleUseObjects().put(
                    key+":completed",PROOF_TTL+60,Map.of("subject",subject)));
            }
            protected void rollbackImpl() { }
        });
        new EventBuilder(realm,s,s.getContext().getConnection()).event(EventType.UPDATE_PASSWORD)
            .detail("mentorflow_reset","email-code").user(user).success();
        return outcome("PENDING_COMMIT");
    }
    static Map<String,String> status(KeycloakSession s,String proof){
        if(!token(proof))return outcome("INVALID");
        var fact=s.singleUseObjects().get(prefix(s)+"proof:"+hash(proof)+":completed");
        return fact==null?outcome("INVALID"):Map.of("status","COMPLETED","subject",fact.get("subject"));
    }
}
