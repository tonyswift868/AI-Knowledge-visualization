package win.clannad.mentorflow.identity;

import jakarta.servlet.http.HttpServletRequest;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.LinkedHashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;
import org.springframework.core.env.Environment;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.http.ResponseEntity;
import org.springframework.http.MediaType;

/** Account identity response matching the Python account-core /auth/me projection. */
@RestController
@RequestMapping("/api/enterprise/auth")
public final class IdentityController146 {
    private static final Pattern ACCOUNT_ID=Pattern.compile("mfa1_[0-9a-f]{64}");
    private static final com.fasterxml.jackson.databind.ObjectMapper JSON=new com.fasterxml.jackson.databind.ObjectMapper();
    private final Set<String> superAdmins;

    public IdentityController146(Environment environment) {
        String raw=environment.getProperty("MENTORFLOW_APPLICATION_SUPER_ADMIN_ACCOUNT_IDS","[]");
        if(raw==null||raw.codePointCount(0,raw.length())>4096)throw new IllegalArgumentException("Invalid application-role configuration");
        try {
            var node=new com.fasterxml.jackson.databind.ObjectMapper()
                .enable(com.fasterxml.jackson.databind.DeserializationFeature.FAIL_ON_TRAILING_TOKENS).readTree(raw);
            if(node==null||!node.isArray()||node.size()>32)throw new IllegalArgumentException("Application-role configuration must be a unique account ID list");
            var values=new HashSet<String>();
            for(var item:node)if(!item.isTextual()||!ACCOUNT_ID.matcher(item.asText()).matches()||!values.add(item.asText()))
                throw new IllegalArgumentException("Application-role configuration must be a unique account ID list");
            superAdmins=Set.copyOf(values);
        } catch(IllegalArgumentException failure){throw failure;}
        catch(Exception failure){throw new IllegalArgumentException("Invalid application-role configuration",failure);}
    }

    @GetMapping("/me")
    public ResponseEntity<byte[]> me(HttpServletRequest request) {
        for(String name:List.of("X-MentorFlow-Role","X-MentorFlow-Product-Role","X-MentorFlow-Account-Role"))
            if(request.getHeader(name)!=null)throw new IdentityFailure(403,"APPLICATION_ROLE_CLIENT_ASSERTION_REJECTED","Application roles are assigned by the server");
        for(String name:List.of("role","product_role","account_role"))
            if(request.getParameterMap().containsKey(name))throw new IdentityFailure(403,"APPLICATION_ROLE_CLIENT_ASSERTION_REJECTED","Application roles are assigned by the server");
        Object actorObject=request.getAttribute("actor_context"),tenantObject=request.getAttribute("tenant_context");
        if(!(actorObject instanceof Map<?,?> actor)||!(tenantObject instanceof Map<?,?> tenant))
            throw new IdentityFailure("ENTERPRISE_AUTH_REQUIRED","Enterprise authentication is required");
        Authentication authentication=SecurityContextHolder.getContext().getAuthentication();
        if(!(authentication instanceof JwtAuthenticationToken jwtAuth))
            throw new IdentityFailure("ENTERPRISE_AUTH_REQUIRED","Enterprise authentication is required");
        String subject=string(actor.get("subject_id")),issuer=string(actor.get("issuer"));
        if(subject==null||issuer==null||subject.isEmpty()||issuer.isEmpty())throw new IdentityFailure("ENTERPRISE_AUTH_REQUIRED","Enterprise authentication is required");
        String accountId=accountId(issuer,subject);
        String actorKind=string(actor.get("actor_kind"));
        String role="HUMAN".equals(actorKind)&&superAdmins.contains(accountId)?"SUPER_ADMIN":"LEARNER";
        var account=new LinkedHashMap<String,Object>();
        account.put("account_id",accountId);account.put("subject_id",subject);
        // Python's current /me is reached through a bearer binding, whose
        // IdentityBinding.session is None; profile fields therefore stay null.
        account.put("preferred_username",null);account.put("email",null);account.put("display_name",null);
        var context=new LinkedHashMap<String,Object>();
        context.put("contract_version","enterprise-context.v1");context.put("organization_id",tenant.get("organization_id"));
        context.put("workspace_id",tenant.get("workspace_id"));context.put("roles",list(actor.get("roles")));
        context.put("service_permissions",list(actor.get("service_permissions")));context.put("context_version",0);
        var result=new LinkedHashMap<String,Object>();result.put("schema_version","mentorflow.enterprise-identity-session.v1");
        result.put("authenticated",true);result.put("role",role);result.put("authentication_mode","BEARER");
        result.put("account",account);result.put("context",context);return success(result);
    }

    @RequestMapping(value="/me",method={RequestMethod.HEAD,RequestMethod.POST,RequestMethod.PUT,RequestMethod.PATCH,
        RequestMethod.DELETE,RequestMethod.OPTIONS,RequestMethod.TRACE})
    public ResponseEntity<byte[]> unsupportedMeMethod() { return methodNotAllowed("GET"); }

    private static ResponseEntity<byte[]> methodNotAllowed(String allowed) {
        byte[] body="{\"detail\":\"Method Not Allowed\"}".getBytes(StandardCharsets.UTF_8);
        return ResponseEntity.status(405).header("Allow",allowed).contentType(MediaType.APPLICATION_JSON)
            .contentLength(body.length).body(body);
    }

    private static ResponseEntity<byte[]> success(Map<String,Object> value) {
        try {byte[] body=JSON.writeValueAsBytes(value);return ResponseEntity.ok().contentType(MediaType.APPLICATION_JSON)
            .contentLength(body.length).body(body);}
        catch(Exception failure){throw new IllegalStateException(failure);}
    }

    private static String string(Object value){return value instanceof String text?text:null;}
    private static List<?> list(Object value){return value instanceof List<?> values?values:List.of();}
    private static String accountId(String issuer,String subject) {
        if(!issuer.equals(issuer.strip())||!subject.equals(subject.strip()))throw new IdentityFailure(503,"ENTERPRISE_IDENTITY_NOT_READY","Enterprise identity runtime is not ready");
        return "mfa1_"+sha256("mentorflow.account.v1:"+sha256(issuer)+":"+sha256(subject));
    }
    private static String sha256(String value) {
        try{return java.util.HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));}
        catch(Exception failure){throw new IllegalStateException(failure);}
    }
}
