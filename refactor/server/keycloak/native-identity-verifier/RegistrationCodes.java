package win.clannad.mentorflow;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.*;
import jakarta.persistence.LockModeType;
import org.keycloak.common.util.Time;
import org.keycloak.connections.jpa.JpaConnectionProvider;
import org.keycloak.email.EmailSenderProvider;
import org.keycloak.events.EventBuilder;
import org.keycloak.events.EventType;
import org.keycloak.models.*;
import org.keycloak.models.jpa.entities.UserEntity;
import org.keycloak.models.utils.KeycloakModelUtils;
import org.keycloak.policy.PasswordPolicyManagerProvider;

/** Anonymous registration authority. Reachable only through the pinned backend SPI. */
final class RegistrationCodes {
    static final String MARKER = "mf.registration.v1", EXPIRES = MARKER + ".expires",
        ACTIVE = MARKER + ".active", ACTIONS = MARKER + ".actions";
    private static final int TTL = 600;
    private static final SecureRandom RANDOM = new SecureRandom();
    private static String hash(String value) { return ResetCompletions.hash(value); }
    private static String prefix(KeycloakSession s) { return "mf-register-v1:" + s.getContext().getRealm().getId() + ":"; }
    private static Map<String,String> result(String status) { return Map.of("status", status); }
    private static String token() {
        byte[] bytes = new byte[32]; RANDOM.nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }
    private static UserEntity lock(KeycloakSession s, UserModel user) {
        var em = s.getProvider(JpaConnectionProvider.class).getEntityManager();
        var entity = em.find(UserEntity.class, user.getId(), LockModeType.PESSIMISTIC_WRITE);
        if (entity != null) em.refresh(entity, LockModeType.PESSIMISTIC_WRITE);
        return entity;
    }
    private static String attribute(UserEntity e, String key) {
        return e.getAttributes().stream().filter(a -> key.equals(a.getName())).map(a -> a.getValue()).findFirst().orElse("");
    }
    private static boolean pending(KeycloakSession s, UserModel u, UserEntity e) {
        return e != null && e.isEnabled() && !e.isEmailVerified()
            && e.getRealmId().equals(s.getContext().getRealm().getId())
            && u.getServiceAccountClientLink() == null && u.getFederationLink() == null
            && "pending".equals(attribute(e, MARKER));
    }
    private static String sendLimit(KeycloakSession s, String email) {
        var store = s.singleUseObjects(); String key = prefix(s) + "send:" + hash(email.toLowerCase(Locale.ROOT));
        if (!store.putIfAbsent(key + ":cooldown", 60)) return "COOLDOWN";
        // Ten sends in any rolling hour, shared by restart/resend and all credentials.
        for (int i=0; i<10; i++) if (store.putIfAbsent(key + ":hour:" + i, 3600)) return "OK";
        return "SEND_LIMIT";
    }
    private static Map<String,String> issue(KeycloakSession s, UserModel u) throws Exception {
        String credential = token(), code = EmailCodeReset.randomCode(), digest = hash(credential);
        String key = prefix(s) + "code:" + digest;
        int expires = Time.currentTime() + TTL;
        u.setSingleAttribute(ACTIVE, digest); u.setSingleAttribute(EXPIRES, Integer.toString(expires));
        s.singleUseObjects().put(key, TTL + 60, Map.of("subject", u.getId(),
            "email", hash(u.getEmail().toLowerCase(Locale.ROOT)), "binding", EmailCodeReset.credentialBinding(u),
            "expires", Integer.toString(expires), "code", hash(credential + "\0" + code)));
        String text = "您的 MentorFlow 注册验证码是：" + code
            + "\n有效期为 10 分钟。请在 MentorFlow 注册页面输入。\n请勿向他人提供验证码。如非本人操作，请忽略此邮件。";
        try {
            s.getProvider(EmailSenderProvider.class).send(s.getContext().getRealm().getSmtpConfig(), u,
                "MentorFlow 注册验证码", text, null);
        } catch (Exception failure) {
            s.singleUseObjects().remove(key);
            s.getTransactionManager().setRollbackOnly();
            throw failure;
        }
        return Map.of("status", "CODE_SENT", "credential", credential, "expires_in", "600", "resend_after", "60");
    }
    static Map<String,String> start(KeycloakSession s, String username, String email, String password) throws Exception {
        if (username == null || username.isBlank() || username.length()>255 || !username.equals(username.trim())
                || username.codePoints().anyMatch(Character::isISOControl) || email == null || email.length()>254
                || !email.matches("[^\\s@]+@[^\\s@]+\\.[^\\s@]+") || password == null || password.length()<8 || password.length()>128)
            return result("INPUT_INVALID");
        var realm = s.getContext().getRealm();
        // Fail closed if the deployment policy was not applied. Do not erase forced actions.
        var action = realm.getRequiredActionProviderByAlias("UPDATE_PASSWORD");
        if (action == null || !action.isEnabled() || action.isDefaultAction() || realm.isDuplicateEmailsAllowed())
            return result("UNAVAILABLE");
        var user = s.users().getUserByUsername(realm, username);
        var byEmail = s.users().getUserByEmail(realm, email);
        boolean restart = false;
        if (user != null) {
            var entity = lock(s, user);
            restart = pending(s, user, entity) && byEmail != null && byEmail.getId().equals(user.getId())
                && email.equalsIgnoreCase(entity.getEmail())
                && Long.parseLong(attribute(entity, EXPIRES)) <= Time.currentTime();
            if (!restart) return result("USERNAME_ALREADY_IN_USE");
        } else if (byEmail != null) return result("EMAIL_ALREADY_IN_USE");
        String limit = sendLimit(s, email); if (!"OK".equals(limit)) return result(limit);
        if (!restart) {
            user = s.users().addUser(realm, username);
            user.setEnabled(true); user.setEmail(email); user.setEmailVerified(false);
            user.addRequiredAction(UserModel.RequiredAction.VERIFY_EMAIL);
            user.setSingleAttribute(MARKER, "pending");
            user.setAttribute(ACTIONS, user.getRequiredActionsStream().toList());
        }
        if (s.getProvider(PasswordPolicyManagerProvider.class).validate(realm, user, password) != null) {
            s.getTransactionManager().setRollbackOnly();
            return result("PASSWORD_POLICY_REJECTED");
        }
        // Password + marker + ticket binding live in the same identity transaction.
        user.credentialManager().updateCredential(UserCredentialModel.password(password, false));
        return issue(s, user);
    }
    private static UserModel bound(KeycloakSession s, String credential, Map<String,String> row) {
        if (row == null) return null;
        var u = s.users().getUserById(s.getContext().getRealm(), row.get("subject"));
        if (u == null) return null;
        var e = lock(s, u);
        if (!pending(s, u, e) || !hash(credential).equals(attribute(e, ACTIVE))
                || !hash(Objects.toString(e.getEmail(), "").toLowerCase(Locale.ROOT)).equals(row.get("email"))
                || !EmailCodeReset.credentialBinding(u).equals(row.get("binding"))) return null;
        return u;
    }
    static Map<String,String> resend(KeycloakSession s, String credential) throws Exception {
        if (!EmailCodeReset.token(credential)) return result("INVALID");
        String key = prefix(s) + "code:" + hash(credential);
        var row = s.singleUseObjects().get(key);
        if (row == null || Integer.parseInt(row.get("expires"))<=Time.currentTime()) return result("EXPIRED");
        var u = bound(s, credential, row);
        if (u == null || s.singleUseObjects().contains(key + ":used")) return result("INVALID");
        String limit = sendLimit(s, u.getEmail()); if (!"OK".equals(limit)) return result(limit);
        var response = issue(s, u);
        s.singleUseObjects().remove(key); // Rotated credential; previous code cannot verify.
        return response;
    }
    static Map<String,String> verify(KeycloakSession s, String credential, String code) {
        if (!EmailCodeReset.token(credential) || code == null || !code.matches("[0-9]{6}")) return result("INVALID");
        String key = prefix(s) + "code:" + hash(credential); var store = s.singleUseObjects(); var row = store.get(key);
        if (row == null || Integer.parseInt(row.get("expires"))<=Time.currentTime()) return result("EXPIRED");
        var u = bound(s, credential, row);
        if (u == null || store.contains(key + ":used")) return result("INVALID");
        if (store.contains(key + ":try:4")) return result("EXHAUSTED");
        if (!MessageDigest.isEqual(hash(credential + "\0" + code).getBytes(StandardCharsets.US_ASCII),
                row.get("code").getBytes(StandardCharsets.US_ASCII))) {
            for (int i=0; i<5; i++) if (store.putIfAbsent(key + ":try:" + i, TTL+60))
                return result(i == 4 ? "EXHAUSTED" : "WRONG_CODE");
            return result("EXHAUSTED");
        }
        if (!AtomicActionTokens.claim(s, key + ":used", Integer.parseInt(row.get("expires")))) return result("INVALID");
        u.setEmailVerified(true);
        // Only actions recorded at creation; later administrator actions remain enforced.
        u.getAttributeStream(ACTIONS).toList().forEach(u::removeRequiredAction);
        u.setSingleAttribute(MARKER, "verified"); u.removeAttribute(ACTIVE); u.removeAttribute(EXPIRES); u.removeAttribute(ACTIONS);
        var factory = s.getKeycloakSessionFactory();
        s.getTransactionManager().enlistAfterCompletion(new AbstractKeycloakTransaction() {
            protected void commitImpl() { KeycloakModelUtils.runJobInTransaction(factory, fresh ->
                fresh.singleUseObjects().put(key + ":completed", 60, Map.of("status", "VERIFIED"))); }
            protected void rollbackImpl() { }
        });
        new EventBuilder(s.getContext().getRealm(), s, s.getContext().getConnection()).event(EventType.VERIFY_EMAIL)
            .detail("mentorflow_email", "registration-code").user(u).success();
        return result("PENDING_COMMIT");
    }
    static Map<String,String> status(KeycloakSession s, String credential) {
        if (!EmailCodeReset.token(credential)) return result("INVALID");
        var fact = s.singleUseObjects().get(prefix(s) + "code:" + hash(credential) + ":completed");
        return fact == null ? result("INVALID") : fact;
    }
}
