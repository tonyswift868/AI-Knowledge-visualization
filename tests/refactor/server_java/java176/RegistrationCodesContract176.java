package win.clannad.mentorflow;

import java.lang.reflect.*;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.stream.Stream;
import org.keycloak.email.EmailSenderProvider;
import org.keycloak.models.*;
import org.keycloak.connections.jpa.JpaConnectionProvider;
import org.keycloak.policy.PasswordPolicyManagerProvider;

/**
 * Local proxy-only contract probe for the real RegistrationCodes SPI class.
 * No Keycloak server, account, SMTP relay, network, or credential is used.
 */
public final class RegistrationCodesContract176 {
    private static final class State {
        final Map<String,Object> singleUse = new HashMap<>();
        final List<Mail> mail = new ArrayList<>();
        final List<String> mutations = new ArrayList<>();
        final Map<String,UserState> users = new HashMap<>();
        UserModel existing;
        UserModel created;
    }
    private static final class UserState {
        final String username;
        String email;
        boolean enabled;
        boolean emailVerified;
        final Map<String,String> attributes = new HashMap<>();
        UserState(String username, String email, boolean enabled, boolean emailVerified) {
            this.username=username; this.email=email; this.enabled=enabled; this.emailVerified=emailVerified;
        }
    }
    private record Mail(String to, String subject, String body) {}

    public static void main(String[] args) throws Exception {
        State state = new State();
        RealmModel realm = realm();
        state.existing = user(state, "existing176", "existing176@example.invalid");
        UserProvider users = proxy(UserProvider.class, (self, method, a) -> switch (method.getName()) {
            case "getUserByUsername" -> state.created != null && Objects.equals(a[1], "new176") ? state.created :
                Objects.equals(a[1], "existing176") ? state.existing : null;
            case "getUserByEmail" -> Objects.equals(a[1], "existing176@example.invalid") ? state.existing : null;
            case "addUser" -> { state.created = user(state, (String)a[1], null); yield state.created; }
            case "getUserById" -> Objects.equals(a[1], "existing176") ? state.existing : state.created;
            default -> defaultValue(method.getReturnType());
        });
        SingleUseObjectProvider store = proxy(SingleUseObjectProvider.class, (self, method, a) -> {
            String name = method.getName();
            if (name.equals("putIfAbsent")) {
                String key = (String)a[0];
                if (state.singleUse.containsKey(key)) return false;
                Object value = a.length >= 3 ? a[2] : Boolean.TRUE;
                state.singleUse.put(key, value);
                return true;
            }
            if (name.equals("put")) { state.singleUse.put((String)a[0], a[2]); return null; }
            if (name.equals("get")) return state.singleUse.get((String)a[0]);
            if (name.equals("contains")) return state.singleUse.containsKey((String)a[0]);
            if (name.equals("remove")) { state.singleUse.remove((String)a[0]); return null; }
            return defaultValue(method.getReturnType());
        });
        EmailSenderProvider sender = proxy(EmailSenderProvider.class, (self, method, a) -> {
            if (method.getName().equals("send")) {
                UserModel recipient = (UserModel)a[1];
                state.mail.add(new Mail(recipient.getEmail(), (String)a[2], (String)a[3]));
            }
            return defaultValue(method.getReturnType());
        });
        PasswordPolicyManagerProvider policy = proxy(PasswordPolicyManagerProvider.class,
            (self, method, a) -> method.getName().equals("validate") && a.length > 0
                && "weak-password".equals(a[a.length - 1])
                    ? new org.keycloak.policy.PolicyError("local policy rejection")
                : defaultValue(method.getReturnType()));
        jakarta.persistence.EntityManager entityManager = proxy(jakarta.persistence.EntityManager.class,
            (self, method, a) -> {
                if (method.getName().equals("find")) return mockUserEntity(state, Objects.toString(a[1], ""));
                return defaultValue(method.getReturnType());
            });
        JpaConnectionProvider jpa = proxy(JpaConnectionProvider.class,
            (self, method, a) -> method.getName().equals("getEntityManager") ? entityManager : defaultValue(method.getReturnType()));
        KeycloakContext context = proxy(KeycloakContext.class, (self, method, a) ->
            method.getName().equals("getRealm") ? realm : defaultValue(method.getReturnType()));
        KeycloakSession session = proxy(KeycloakSession.class, (self, method, a) -> {
            if (method.getName().equals("getContext")) return context;
            if (method.getName().equals("users")) return users;
            if (method.getName().equals("singleUseObjects")) return store;
            if (method.getName().equals("getProvider")) {
                Class<?> type = (Class<?>)a[0];
                if (type == EmailSenderProvider.class) return sender;
                if (type == PasswordPolicyManagerProvider.class) return policy;
                if (type == JpaConnectionProvider.class) return jpa;
            }
            return defaultValue(method.getReturnType());
        });

        Map<String,String> newStart = RegistrationCodes.start(session, "new176", "new176@example.invalid", "valid-password-176");
        Mail newMail = last(state);
        require("CODE_SENT".equals(newStart.get("status")), "new address must keep CODE_SENT");
        require("600".equals(newStart.get("expires_in")) && "60".equals(newStart.get("resend_after")), "legacy response timing fields");
        require(newStart.keySet().equals(Set.of("status", "credential", "expires_in", "resend_after")), "legacy response fields");
        require(newMail.to().equals("new176@example.invalid") && newMail.body().contains("注册验证码") && newMail.body().matches("(?s).*\\b[0-9]{6}\\b.*"), "new address receives verification code");
        String newCode = newMail.body().replaceAll("(?s).*?(\\d{6}).*", "$1");
        String guaranteedWrong = "000000".equals(newCode) ? "000001" : "000000";
        Map<String,String> firstWrong = RegistrationCodes.verify(session, newStart.get("credential"), guaranteedWrong);
        require("WRONG_CODE".equals(firstWrong.get("status")), "new ticket is bound and accepts code attempts: " + firstWrong);
        state.singleUse.keySet().removeIf(key -> key.endsWith(":cooldown"));
        Map<String,String> newResend = RegistrationCodes.resend(session, newStart.get("credential"));
        require("CODE_SENT".equals(newResend.get("status")) && newResend.keySet().equals(newStart.keySet()),
            "normal registration resend keeps legacy fields: " + newResend);
        require(!newResend.get("credential").equals(newStart.get("credential")), "normal resend rotates credential");
        String resentCode = last(state).body().replaceAll("(?s).*?(\\d{6}).*", "$1");
        String wrongResentCode = "000000".equals(resentCode) ? "000001" : "000000";
        for (int i=0; i<5; i++) {
            Map<String,String> wrong = RegistrationCodes.verify(session, newResend.get("credential"), wrongResentCode);
            require((i < 4 ? "WRONG_CODE" : "EXHAUSTED").equals(wrong.get("status")),
                "normal ticket has generic bounded wrong-code responses");
        }

        int beforeExisting = state.mail.size();
        int existingMutationStart = state.mutations.size();
        Map<String,String> existingStart = RegistrationCodes.start(session, "other176", "existing176@example.invalid", "valid-password-176");
        Mail existingMail = last(state);
        require(state.mail.size() == beforeExisting + 1, "existing mailbox gets email guidance");
        require(existingStart.keySet().equals(newStart.keySet()) && "CODE_SENT".equals(existingStart.get("status")), "existing/new legacy response schema must match");
        require(existingMail.to().equals("existing176@example.invalid"), "guidance sent only to supplied existing mailbox");
        require(existingMail.body().contains("已经注册过") && (existingMail.body().contains("登录") || existingMail.body().contains("找回密码")), "existing mailbox receives login/recovery guidance");
        require(!existingMail.body().matches("(?s).*\\b[0-9]{6}\\b.*"), "existing mailbox gets no registration code");
        require(!Objects.equals(existingStart.get("credential"), newStart.get("credential")), "credentials are independently random");

        int existingMailCount = state.mail.size();
        state.singleUse.keySet().removeIf(key -> key.endsWith(":cooldown"));
        Map<String,String> existingResend = RegistrationCodes.resend(session, existingStart.get("credential"));
        Mail resentMail = last(state);
        require("CODE_SENT".equals(existingResend.get("status")) && existingResend.keySet().equals(existingStart.keySet()), "resend preserves old response schema");
        require(resentMail.body().contains("已经注册过") && !resentMail.body().matches("(?s).*\\b[0-9]{6}\\b.*"), "existing resend repeats guidance, not code");
        require(state.mail.size() == existingMailCount + 1, "existing resend sends one guidance email");
        for (int i=0; i<5; i++) {
            Map<String,String> existingVerify = RegistrationCodes.verify(session, existingResend.get("credential"), "123456");
            require((i < 4 ? "WRONG_CODE" : "EXHAUSTED").equals(existingVerify.get("status")),
                "existing mailbox verify must match normal ticket's generic attempt behavior");
        }
        require(state.mutations.size() == existingMutationStart, "existing account was not mutated by duplicate registration/resend/verify");

        // Password policy is checked before the address lookup, so it cannot
        // distinguish existing from new mailboxes or send only one side a mail.
        int policyMailStart = state.mail.size();
        Map<String,String> weakNew = RegistrationCodes.start(session, "weak-new176", "weak-new176@example.invalid", "weak-password");
        Map<String,String> weakExisting = RegistrationCodes.start(session, "weak-existing176", "existing176@example.invalid", "weak-password");
        require("PASSWORD_POLICY_REJECTED".equals(weakNew.get("status"))
            && weakNew.equals(weakExisting), "password policy rejection must match across email states");
        require(state.mail.size() == policyMailStart, "policy rejection must not send email for either email state");

        int beforeUsernameConflict = state.mail.size();
        Map<String,String> usernameConflictExistingEmail = RegistrationCodes.start(session, "existing176", "existing176@example.invalid", "valid-password-176");
        Map<String,String> usernameConflictFreshEmail = RegistrationCodes.start(session, "existing176", "probe176@example.invalid", "valid-password-176");
        require("USERNAME_ALREADY_IN_USE".equals(usernameConflictExistingEmail.get("status"))
            && usernameConflictExistingEmail.equals(usernameConflictFreshEmail), "duplicate username response must not depend on email state");
        require(state.mail.size() == beforeUsernameConflict, "username duplicate must not send email or reveal email status");
        Map<String,String> invalidKnownEmail = RegistrationCodes.start(session, "bad\nname176", "existing176@example.invalid", "valid-password-176");
        Map<String,String> invalidFreshEmail = RegistrationCodes.start(session, "bad\nname176", "probe176@example.invalid", "valid-password-176");
        require("INPUT_INVALID".equals(invalidKnownEmail.get("status"))
            && invalidKnownEmail.equals(invalidFreshEmail), "invalid username response must not depend on email state");
        require(state.mail.size() == beforeUsernameConflict, "invalid username must not send email");

        System.out.println("REGISTRATION176={\"new_response_fields\":4,\"existing_response_fields\":4,\"check_email_schema_compatible\":true,\"new_mail_code\":true,\"existing_mail_guidance\":true,\"existing_resend_guidance\":true,\"existing_verify\":\"WRONG_CODE\",\"existing_account_mutations\":0,\"policy_same\":true,\"username_conflict\":\"USERNAME_ALREADY_IN_USE\",\"invalid_username\":\"INPUT_INVALID\",\"username_conflict_mail_count\":0}");
    }

    private static RealmModel realm() throws Exception {
        Object action = Class.forName("org.keycloak.models.RequiredActionProviderModel").getDeclaredConstructor().newInstance();
        for (var pair : List.of(Map.entry("setAlias", "UPDATE_PASSWORD"), Map.entry("setProviderId", "UPDATE_PASSWORD"))) {
            try { action.getClass().getMethod(pair.getKey(), String.class).invoke(action, pair.getValue()); } catch (NoSuchMethodException ignored) { }
        }
        for (var pair : List.of(Map.entry("setEnabled", Boolean.TRUE), Map.entry("setDefaultAction", Boolean.FALSE))) {
            try { action.getClass().getMethod(pair.getKey(), boolean.class).invoke(action, pair.getValue()); } catch (NoSuchMethodException ignored) { }
        }
        return proxy(RealmModel.class, (self, method, a) -> switch (method.getName()) {
            case "getRequiredActionProviderByAlias" -> "UPDATE_PASSWORD".equals(a[0]) ? action : null;
            case "isDuplicateEmailsAllowed" -> false;
            case "getId" -> "realm176";
            case "getSmtpConfig" -> Map.of("host", "loopback.invalid");
            default -> defaultValue(method.getReturnType());
        });
    }

    private static UserModel user(State state, String username, String email) {
        UserState userState = new UserState(username, email, true, false);
        state.users.put(username, userState);
        return proxy(UserModel.class, (self, method, a) -> switch (method.getName()) {
            case "getId" -> username;
            case "getUsername" -> username;
            case "getEmail" -> userState.email;
            case "setEmail" -> { userState.email = (String)a[0]; state.mutations.add("email:" + username); yield null; }
            case "setEnabled" -> { userState.enabled = (boolean)a[0]; state.mutations.add("enabled:" + username); yield null; }
            case "setEmailVerified" -> { userState.emailVerified = (boolean)a[0]; state.mutations.add("verified:" + username); yield null; }
            case "isEmailVerified" -> userState.emailVerified;
            case "isEnabled" -> userState.enabled;
            case "setSingleAttribute" -> { userState.attributes.put((String)a[0], (String)a[1]); state.mutations.add("attribute:" + username); yield null; }
            case "setAttribute" -> { userState.attributes.put((String)a[0], String.valueOf(a[1])); state.mutations.add("attribute:" + username); yield null; }
            case "removeAttribute" -> { userState.attributes.remove((String)a[0]); state.mutations.add("attribute:" + username); yield null; }
            case "getAttributeStream" -> userState.attributes.containsKey((String)a[0]) ? Stream.of(userState.attributes.get((String)a[0])) : Stream.empty();
            case "addRequiredAction", "removeRequiredAction" -> { state.mutations.add(method.getName() + ":" + username); yield null; }
            case "getRequiredActionsStream" -> Stream.empty();
            case "getServiceAccountClientLink", "getFederationLink" -> null;
            case "credentialManager" -> proxy(UserCredentialManager.class, (s,m,args) -> {
                if (m.getName().equals("updateCredential")) { state.mutations.add("credential:" + username); return true; }
                if (m.getName().equals("getStoredCredentialsByTypeStream")) return Stream.of(credential());
                return defaultValue(m.getReturnType());
            });
            default -> defaultValue(method.getReturnType());
        });
    }

    private static Object credential() {
        try {
            Class<?> type = Class.forName("org.keycloak.credential.CredentialModel");
            Object value = type.getDeclaredConstructor().newInstance();
            set(value, "id", "password176");
            set(value, "type", "password");
            set(value, "createdDate", 1L);
            set(value, "credentialData", "{}");
            set(value, "secretData", "{}");
            return value;
        } catch (ReflectiveOperationException error) {
            throw new IllegalStateException("Unable to create local test credential", error);
        }
    }

    private static Object mockUserEntity(State state, String id) {
        try {
            Class<?> type = Class.forName("org.keycloak.models.jpa.entities.UserEntity");
            Object entity = type.getDeclaredConstructor().newInstance();
            UserState user = state.users.get(id);
            set(entity, "id", id);
            set(entity, "email", user == null ? null : user.email);
            set(entity, "enabled", user != null && user.enabled);
            set(entity, "emailVerified", user != null && user.emailVerified);
            set(entity, "realmId", "realm176");
            List<Object> attributes = new ArrayList<>();
            if (user != null) for (var entry : user.attributes.entrySet()) {
                Class<?> attributeType = Class.forName("org.keycloak.models.jpa.entities.UserAttributeEntity");
                Object attribute = attributeType.getDeclaredConstructor().newInstance();
                set(attribute, "id", UUID.randomUUID().toString());
                set(attribute, "name", entry.getKey()); set(attribute, "value", entry.getValue());
                attributes.add(attribute);
            }
            set(entity, "attributes", attributes);
            return entity;
        } catch (ReflectiveOperationException error) {
            throw new IllegalStateException("Unable to create local test user entity", error);
        }
    }

    private static void set(Object target, String name, Object value) throws ReflectiveOperationException {
        for (Class<?> type = target.getClass(); type != null; type = type.getSuperclass()) {
            try {
                Field field = type.getDeclaredField(name);
                field.setAccessible(true); field.set(target, value); return;
            } catch (NoSuchFieldException ignored) { }
        }
        String setter = "set" + Character.toUpperCase(name.charAt(0)) + name.substring(1);
        for (Method method : target.getClass().getMethods()) if (method.getName().equals(setter) && method.getParameterCount() == 1) {
            method.invoke(target, value); return;
        }
        throw new NoSuchFieldException(target.getClass().getName() + "." + name);
    }

    private interface Invocation { Object call(Object self, Method method, Object[] args) throws Throwable; }
    @SuppressWarnings("unchecked") private static <T> T proxy(Class<T> type, Invocation call) {
        return (T)Proxy.newProxyInstance(type.getClassLoader(), new Class<?>[]{type}, (self, method, args) -> {
            if (method.getDeclaringClass() == Object.class) return switch (method.getName()) {
                case "toString" -> "Proxy<" + type.getSimpleName() + ">";
                case "hashCode" -> System.identityHashCode(self);
                case "equals" -> self == args[0];
                default -> null;
            };
            return call.call(self, method, args == null ? new Object[0] : args);
        });
    }
    private static Object defaultValue(Class<?> type) {
        if (!type.isPrimitive() || type == void.class) return null;
        if (type == boolean.class) return false;
        if (type == char.class) return '\0';
        if (type == byte.class) return (byte)0;
        if (type == short.class) return (short)0;
        if (type == int.class) return 0;
        if (type == long.class) return 0L;
        if (type == float.class) return 0f;
        if (type == double.class) return 0d;
        return null;
    }
    private static Mail last(State state) { return state.mail.get(state.mail.size() - 1); }
    private static void require(boolean condition, String message) { if (!condition) throw new AssertionError(message); }
}
