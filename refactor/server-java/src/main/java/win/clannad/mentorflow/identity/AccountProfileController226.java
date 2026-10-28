package win.clannad.mentorflow.identity;

import com.fasterxml.jackson.core.StreamReadFeature;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.json.JsonMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Map;
import java.util.Set;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** Private profile routes. The authenticated token alone selects the profile owner. */
@RestController
@RequestMapping("/api/enterprise/account/profile")
public final class AccountProfileController226 {
    static final int MAX_JSON_BYTES = 4096;
    private static final ObjectMapper JSON = JsonMapper.builder()
        .enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
        .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS).build();
    private final AccountProfileRepository226 profiles;

    public AccountProfileController226(AccountProfileRepository226 profiles) { this.profiles = profiles; }

    @GetMapping
    public ResponseEntity<ProfileResponse> profile(HttpServletRequest request) {
        var actor = actor(request);
        var profile = profiles.profile(actor.issuer, actor.subject);
        return ResponseEntity.ok()
            .header(HttpHeaders.ETAG, profileEtag(profile.version)).body(view(profile));
    }

    @PatchMapping(consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<ProfileResponse> update(HttpServletRequest request) throws IOException {
        var actor = actor(request);
        long expected = expectedVersion(request.getHeader(HttpHeaders.IF_MATCH));
        if (request.getContentLengthLong() > MAX_JSON_BYTES)
            throw invalid("资料内容格式不正确");
        byte[] body = request.getInputStream().readNBytes(MAX_JSON_BYTES + 1);
        if (body.length > MAX_JSON_BYTES) {
            java.util.Arrays.fill(body, (byte) 0);
            throw invalid("资料内容格式不正确");
        }
        if (request.getContentType() == null || !request.getContentType().split(";", 2)[0].strip().equalsIgnoreCase("application/json"))
            throw invalid("资料内容格式不正确");
        try {
        Map<?, ?> values = jsonObject(body);
        if (!values.keySet().equals(Set.of("nickname", "signature"))) throw invalid("昵称和签名必须同时提供");
        String nickname = text(values.get("nickname"), "昵称", 30);
        String signature = text(values.get("signature"), "签名", 60);
        var profile = profiles.updateText(actor.issuer, actor.subject, expected, nickname, signature);
        return ResponseEntity.ok()
            .header(HttpHeaders.ETAG, profileEtag(profile.version)).body(view(profile));
        } finally { java.util.Arrays.fill(body, (byte) 0); }
    }

    @GetMapping("/avatar")
    public ResponseEntity<byte[]> avatar(HttpServletRequest request) {
        var actor = actor(request);
        var profile = profiles.profile(actor.issuer, actor.subject); // Auth/RLS check always precedes conditional 304.
        if (profile.avatarBytes == null) throw new IdentityFailure(404, "ACCOUNT_PROFILE_AVATAR_NOT_FOUND", "还没有上传头像");
        String etag = quote(profile.avatarEtag);
        if (matches(request.getHeader(HttpHeaders.IF_NONE_MATCH), etag)) {
            return ResponseEntity.status(304)
                .header(HttpHeaders.ETAG, etag).build();
        }
        return ResponseEntity.ok().contentType(MediaType.IMAGE_JPEG)
            .header(HttpHeaders.ETAG, etag).body(profile.avatarBytes);
    }

    @PutMapping(value = "/avatar", consumes = {"image/jpeg", "image/png"})
    public ResponseEntity<ProfileResponse> upload(HttpServletRequest request) throws IOException {
        var actor = actor(request);
        long expected = expectedVersion(request.getHeader(HttpHeaders.IF_MATCH));
        if (request.getContentLengthLong() > AccountProfileImage226.MAX_UPLOAD_BYTES)
            throw new IdentityFailure(413, "ACCOUNT_PROFILE_AVATAR_TOO_LARGE", "头像文件不得超过 256 KiB");
        byte[] source = request.getInputStream().readNBytes(AccountProfileImage226.MAX_UPLOAD_BYTES + 1);
        try {
            if (source.length > AccountProfileImage226.MAX_UPLOAD_BYTES)
                throw new IdentityFailure(413, "ACCOUNT_PROFILE_AVATAR_TOO_LARGE", "头像文件不得超过 256 KiB");
            var image = AccountProfileImage226.normalize(source, request.getContentType());
            String etag = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(image.bytes()));
            var profile = profiles.updateAvatar(actor.issuer, actor.subject, expected, image.bytes(), etag,
                image.width(), image.height());
            return ResponseEntity.ok()
                .header(HttpHeaders.ETAG, profileEtag(profile.version)).body(view(profile));
        } catch (IdentityFailure failure) { throw failure; }
        catch (Exception failure) { throw new IdentityFailure(503, "ACCOUNT_PROFILE_AVATAR_UNAVAILABLE", "头像暂时无法处理"); }
        finally { java.util.Arrays.fill(source, (byte) 0); }
    }

    @DeleteMapping("/avatar")
    public ResponseEntity<ProfileResponse> delete(HttpServletRequest request) {
        var actor = actor(request);
        long expected = expectedVersion(request.getHeader(HttpHeaders.IF_MATCH));
        var profile = profiles.deleteAvatar(actor.issuer, actor.subject, expected);
        return ResponseEntity.ok()
            .header(HttpHeaders.ETAG, profileEtag(profile.version)).body(view(profile));
    }

    private static Actor actor(HttpServletRequest request) {
        Object principal = request.getAttribute("account_principal");
        if (!(principal instanceof Jwt jwt)) throw new IdentityFailure(401, "ENTERPRISE_AUTH_REQUIRED", "登录后才能访问账号资料");
        String issuer = jwt.getClaimAsString("iss"), subject = jwt.getSubject();
        if (issuer == null || issuer.isBlank() || subject == null || subject.isBlank())
            throw new IdentityFailure(401, "ENTERPRISE_AUTH_REQUIRED", "登录后才能访问账号资料");
        String kind = jwt.getClaimAsString("actor_kind");
        String client = jwt.getClaimAsString("azp");
        if (client == null) client = jwt.getClaimAsString("client_id");
        boolean service = client != null && ("service-account-" + client).equals(jwt.getClaimAsString("preferred_username"));
        if (service || (kind != null && !"HUMAN".equals(kind)))
            throw new IdentityFailure(403, "ACCOUNT_PROFILE_HUMAN_REQUIRED", "仅账号本人可以访问个人资料");
        return new Actor(issuer, subject);
    }

    static ProfileResponse view(AccountProfileRepository226.Profile profile) {
        Avatar avatar = profile.avatarBytes == null ? null : new Avatar(quote(profile.avatarEtag), profile.avatarWidth, profile.avatarHeight);
        return new ProfileResponse(profile.nickname, profile.signature, profile.version, avatar);
    }

    private static Map<?, ?> jsonObject(byte[] body) {
        if (body == null || body.length == 0 || body.length > MAX_JSON_BYTES)
            throw invalid("资料内容格式不正确");
        try {
            Object value = JSON.readValue(body, Object.class);
            if (!(value instanceof Map<?, ?> map)) throw invalid("资料内容格式不正确");
            return map;
        } catch (IdentityFailure failure) { throw failure; }
        catch (Exception failure) { throw invalid("资料内容格式不正确"); }
    }

    private static String text(Object value, String label, int maximum) {
        if (!(value instanceof String text)) throw invalid(label + "必须是文字");
        if (text.codePointCount(0, text.length()) > maximum) throw invalid(label + "最多" + maximum + "字");
        if (text.codePoints().anyMatch(point -> Character.getType(point) == Character.CONTROL
                || Character.getType(point) == Character.SURROGATE)) throw invalid(label + "包含不支持的控制字符");
        return text;
    }

    private static long expectedVersion(String header) {
        if (header == null || header.isBlank()) throw new IdentityFailure(428, "ACCOUNT_PROFILE_IF_MATCH_REQUIRED", "保存前请先读取最新资料版本");
        if (!header.matches("\"profile-([1-9][0-9]{0,18})\""))
            throw new IdentityFailure(400, "ACCOUNT_PROFILE_IF_MATCH_INVALID", "资料版本标记无效");
        try { return Long.parseLong(header.substring(9, header.length() - 1)); }
        catch (NumberFormatException failure) { throw new IdentityFailure(400, "ACCOUNT_PROFILE_IF_MATCH_INVALID", "资料版本标记无效"); }
    }

    private static boolean matches(String header, String etag) {
        if (header == null) return false;
        for (String item : header.split(",")) {
            String candidate = item.strip();
            if (candidate.equals("*") || candidate.equals(etag) || candidate.equals("W/" + etag)) return true;
        }
        return false;
    }

    private static String profileEtag(long version) { return quote("profile-" + version); }
    private static String quote(String value) { return "\"" + value + "\""; }
    private static IdentityFailure invalid(String message) {
        return new IdentityFailure(422, "ACCOUNT_PROFILE_INPUT_INVALID", message);
    }

    private record Actor(String issuer, String subject) {}
    public record Avatar(String etag, int width, int height) {}
    public record ProfileResponse(String nickname, String signature, long version, Avatar avatar) {}
}
