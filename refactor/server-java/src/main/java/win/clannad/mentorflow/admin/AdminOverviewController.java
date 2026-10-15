package win.clannad.mentorflow.admin;

import jakarta.servlet.http.HttpServletRequest;
import java.nio.charset.StandardCharsets;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;
import com.fasterxml.jackson.core.StreamReadFeature;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.json.JsonMapper;
import org.springframework.core.env.Environment;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import win.clannad.mentorflow.identity.IdentityDirectory;
import win.clannad.mentorflow.identity.IdentityFailure;

/** Product administrator overview. Role assignment follows the account-ID allowlist, never token claims. */
@RestController
@RequestMapping("/api/enterprise/auth/admin")
public final class AdminOverviewController {
    private static final JsonMapper JSON = JsonMapper.builder().enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
        .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS).build();
    private static final Set<String> ASSERTION_HEADERS = Set.of(
        "x-mentorflow-role", "x-mentorflow-product-role", "x-mentorflow-account-role");
    private final Set<String> superAdminIds;
    private final AdminOverviewService overview;
    private final IdentityDirectory directory;

    public AdminOverviewController(Environment env, AdminOverviewService overview, IdentityDirectory directory) {
        this.overview = overview;
        this.directory = directory;
        String raw = env.getProperty("MENTORFLOW_APPLICATION_SUPER_ADMIN_ACCOUNT_IDS", "[]");
        if (raw.length() > 4096) throw new IllegalArgumentException("Invalid application-role configuration");
        try {
            JsonNode parsed=JSON.readTree(raw);
            if(parsed==null||!parsed.isArray()||parsed.size()>32)throw new IllegalArgumentException();
            List<String> ids=new java.util.ArrayList<>();
            for(JsonNode item:parsed) { if(!item.isTextual())throw new IllegalArgumentException();ids.add(item.textValue()); }
            if (ids.stream().anyMatch(id -> !id.matches("mfa1_[0-9a-f]{64}")) || ids.size() != ids.stream().distinct().count())
                throw new IllegalArgumentException("Application-role configuration must be a unique account ID list");
            superAdminIds = Set.copyOf(ids);
        } catch (Exception invalid) {
            throw new IllegalArgumentException("Invalid application-role configuration", invalid);
        }
    }

    @RequestMapping(value="/overview",method={RequestMethod.GET,RequestMethod.HEAD})
    public ResponseEntity<byte[]> get(HttpServletRequest request) {
        if(!"GET".equals(request.getMethod()))return methodNotAllowed();
        rejectClientAssertions(request);
        Object value = request.getAttribute("actor_context");
        if (!(value instanceof Map<?, ?> actor))
            throw new IdentityFailure("ENTERPRISE_AUTH_REQUIRED", "Authenticated workspace required");
        String issuer=String.valueOf(actor.get("issuer")),subject=String.valueOf(actor.get("subject_id"));
        String organization=String.valueOf(actor.get("organization_id"));Object rawWorkspace=actor.get("workspace_id");
        String workspace=rawWorkspace instanceof String s?s:null;
        Object tenantValue=request.getAttribute("tenant_context");
        if(!(tenantValue instanceof Map<?,?> tenant)||subject.isEmpty()||organization.isEmpty()||workspace==null||workspace.isEmpty()
            ||!(actor.get("membership_id") instanceof String membership)||membership.isEmpty()
            ||!organization.equals(actor.get("organization_id"))||!workspace.equals(actor.get("workspace_id"))
            ||!organization.equals(tenant.get("organization_id"))||!workspace.equals(tenant.get("workspace_id")))
            throw new IdentityFailure(403,"ENTERPRISE_MEMBERSHIP_REQUIRED","An active workspace membership is required for Enterprise product routes");
        directory.ensureOpen(issuer,subject);
        IdentityDirectory.Membership refreshed=directory.resolve(issuer,subject,organization,workspace);
        if(refreshed==null)throw new IdentityFailure(403,"MEMBERSHIP_NOT_ACTIVE","Membership is not active in the selected tenant scope");
        if(!membership.equals(refreshed.id()))
            throw new IdentityFailure(409,"ENTERPRISE_IDENTITY_CONTEXT_CHANGED","Enterprise identity context changed during authorization");
        if (!"HUMAN".equals(actor.get("actor_kind")) || !superAdminIds.contains(accountId(
                issuer, subject)))
            throw new IdentityFailure(403, "APPLICATION_SUPER_ADMIN_REQUIRED", "Product administrator access is required");
        byte[] body = overview.collect();
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).contentType(MediaType.APPLICATION_JSON)
            .contentLength(body.length).body(body);
    }

    @RequestMapping(value="/overview",method={RequestMethod.POST,RequestMethod.PUT,RequestMethod.PATCH,
        RequestMethod.DELETE,RequestMethod.OPTIONS,RequestMethod.TRACE})
    public ResponseEntity<byte[]> unsupportedMethod(){return methodNotAllowed();}

    private static ResponseEntity<byte[]> methodNotAllowed(){
        byte[] body="{\"detail\":\"Method Not Allowed\"}".getBytes(StandardCharsets.UTF_8);
        return ResponseEntity.status(405).header(HttpHeaders.ALLOW,"GET").contentType(MediaType.APPLICATION_JSON)
            .contentLength(body.length).body(body);
    }

    private static void rejectClientAssertions(HttpServletRequest request) {
        for (String name : ASSERTION_HEADERS) if (request.getHeader(name) != null)
            throw new IdentityFailure(403, "APPLICATION_ROLE_CLIENT_ASSERTION_REJECTED", "Application roles are assigned by the server");
        for (String name : List.of("role", "product_role", "account_role")) if (request.getParameterMap().containsKey(name))
            throw new IdentityFailure(403, "APPLICATION_ROLE_CLIENT_ASSERTION_REJECTED", "Application roles are assigned by the server");
    }

    private static String accountId(String issuer, String subject) {
        String value = "mentorflow.account.v1:" + sha256Hex(issuer.getBytes(StandardCharsets.UTF_8)) + ":"
            + sha256Hex(subject.getBytes(StandardCharsets.UTF_8));
        return "mfa1_" + sha256Hex(value.getBytes(StandardCharsets.US_ASCII));
    }
    private static String sha256Hex(byte[] bytes) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)); }
        catch (Exception impossible) { throw new IllegalStateException(impossible); }
    }
}
