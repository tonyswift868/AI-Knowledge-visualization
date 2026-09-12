/* Offline FreeMarker rendering, not a Keycloak server or token verifier.
 * Args: repo root, extracted official theme directory, output directory.
 * Compile/run with the pinned runtime's freemarker-2.3.32.jar. The layout shell
 * is a fixture; password/error/info templates and the logout macro are real.
 * Fixtures contain no password values, credentials or reset tokens.
 */
import freemarker.cache.*;
import freemarker.core.HTMLOutputFormat;
import freemarker.template.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.text.MessageFormat;
import java.util.*;

public class ResetPasswordThemeFixture {
    public static void main(String[] args) throws Exception {
        Path login = Path.of(args[0], "deploy/enterprise/themes/mentorflow-desktop-v1/login");
        Path base = Path.of(args[1], "base/login");
        Path out = Path.of(args[2]);
        Files.createDirectories(out);
        Configuration cfg = new Configuration(Configuration.VERSION_2_3_32);
        cfg.setDefaultEncoding("UTF-8");
        cfg.setOutputFormat(HTMLOutputFormat.INSTANCE);
        cfg.setLogTemplateExceptions(false);
        StringTemplateLoader shell = new StringTemplateLoader();
        shell.putTemplate("template.ftl", """
            <#macro registrationLayout displayMessage=true>
            <!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
            <meta name="viewport" content="width=device-width,initial-scale=1">
            <link rel="stylesheet" href="/resources/css/mentorflow.css"></head>
            <body class="mentorflow-login"><div class="login-pf-page"><div class="card-pf">
            <h1 id="kc-page-title"><#nested "header"></h1>
            <#if displayMessage && message??><p id="server-message" role="alert">${message.summary}</p></#if>
            <#nested "form"></div></div></body></html></#macro>
            """);
        cfg.setTemplateLoader(new MultiTemplateLoader(new TemplateLoader[] {
            shell, new FileTemplateLoader(login.toFile()), new FileTemplateLoader(base.toFile())
        }));
        Properties messages = new Properties();
        try (var r = Files.newBufferedReader(base.resolve("messages/messages_en.properties"))) { messages.load(r); }
        try (var r = Files.newBufferedReader(login.resolve("messages/messages_zh_CN.properties"))) { messages.load(r); }
        Properties properties = new Properties();
        try (var r = Files.newBufferedReader(Path.of(args[1], "keycloak/login/theme.properties"))) { properties.load(r); }
        for (String state : List.of("normal", "server-mismatch", "policy-min", "policy-max", "policy-denied",
                "policy-username", "policy-email", "invalid", "expired", "used", "success-specific", "success-generic", "cancel")) {
            Map<String, Object> data = new HashMap<>();
            Map<String, String> errors = new HashMap<>();
            data.put("properties", properties);
            data.put("url", Map.of("loginAction", "/native-required-action", "resourcesPath", "/resources"));
            data.put("client", Map.of("baseUrl", "/normal-login"));
            data.put("pageRedirectUri", "");
            data.put("actionUri", "");
            data.put("msg", (TemplateMethodModelEx) a -> MessageFormat.format(
                messages.getProperty(a.get(0).toString(), a.get(0).toString()),
                a.subList(1, a.size()).stream().map(Object::toString).toArray()));
            // Deliberately plain fixture messages; no mock XSS sanitizer claim.
            data.put("kcSanitize", (TemplateMethodModelEx) a -> a.get(0).toString());
            data.put("messagesPerField", Map.of(
                "existsError", (TemplateMethodModelEx) a -> a.stream().anyMatch(k -> errors.containsKey(k.toString())),
                "get", (TemplateMethodModelEx) a -> errors.getOrDefault(a.get(0).toString(), "")));
            String template = "login-update-password.ftl";
            if (state.equals("cancel")) data.put("isAppInitiatedAction", true);
            if (state.equals("server-mismatch")) errors.put("password-confirm", messages.getProperty("notMatchPasswordMessage"));
            String messageKey = switch (state) {
                case "policy-min" -> "invalidPasswordMinLengthMessage";
                case "policy-max" -> "invalidPasswordMaxLengthMessage";
                case "policy-denied" -> "invalidPasswordRegexPatternMessage";
                case "policy-username" -> "invalidPasswordNotUsernameMessage";
                case "policy-email" -> "invalidPasswordNotEmailMessage";
                case "invalid", "used" -> "invalidCodeMessage";
                case "expired" -> "expiredActionTokenNoSessionMessage";
                case "success-specific" -> "accountPasswordUpdatedMessage";
                case "success-generic" -> "accountUpdatedMessage";
                default -> "";
            };
            if (!messageKey.isEmpty()) {
                data.put("message", Map.of("summary", MessageFormat.format(messages.getProperty(messageKey), "N")));
            }
            if (List.of("invalid", "expired", "used").contains(state)) template = "error.ftl";
            if (state.startsWith("success-")) template = "info.ftl";
            try (var writer = Files.newBufferedWriter(out.resolve(state + ".html"), StandardCharsets.UTF_8)) {
                cfg.getTemplate(template).process(data, writer);
            }
        }
        System.out.println("Rendered 13 password/error/info fixtures (offline; no auth or tokens).");
    }
}
