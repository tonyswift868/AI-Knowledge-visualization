package win.clannad.mentorflow.account;

import com.fasterxml.jackson.core.StreamReadFeature;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.json.JsonMapper;
import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;
import win.clannad.mentorflow.identity.IdentityFailure;

/** Read-only reset status observations forwarded to the already configured mTLS authority. */
@RestController
@RequestMapping("/api/enterprise/auth/reset")
public final class ResetCompletionController146 {
    private static final String SCHEMA="mentorflow.reset-completion.v1";
    private static final Set<String> STATUSES=Set.of("PENDING","COMPLETED","EXPIRED","INVALID");
    private static final Pattern STATE=Pattern.compile("[A-Za-z0-9_-]{43,128}"),TRANSACTION=Pattern.compile("[A-Za-z0-9_-]{43}");
    private static final ObjectMapper JSON=JsonMapper.builder().enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
        .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS).build();
    private final ObjectProvider<NativeIdentityBridge> bridges;
    private final ObjectProvider<NativeSessionService> sessions;

    public ResetCompletionController146(ObjectProvider<NativeIdentityBridge> bridges,ObjectProvider<NativeSessionService> sessions) {
        this.bridges=bridges;this.sessions=sessions;
    }

    @PostMapping("/watch")
    public ResponseEntity<byte[]> watch(HttpServletRequest request) {return operate(request,"watch");}
    @PostMapping("/status")
    public ResponseEntity<byte[]> status(HttpServletRequest request) {return operate(request,"status");}

    @RequestMapping(value={"/watch","/status"},method={RequestMethod.GET,RequestMethod.HEAD,RequestMethod.PUT,
        RequestMethod.PATCH,RequestMethod.DELETE,RequestMethod.OPTIONS,RequestMethod.TRACE})
    public ResponseEntity<byte[]> unsupportedMethod() {return methodNotAllowed();}

    private ResponseEntity<byte[]> operate(HttpServletRequest request,String operation) {
        Map<String,Object> body=null;
        try {
            transport(request);
            NativeSessionService service=sessions.getIfAvailable();
            NativeIdentityBridge bridge=bridges.getIfAvailable();
            if(service==null||bridge==null)throw notReady();
            AccountRateLimit146.check(request.getRemoteAddr()==null?"unknown":request.getRemoteAddr(),operation);
            body=readBody(request);
            String field="watch".equals(operation)?"oidc_state":"transaction_id";
            String value=(String)body.get(field);
            if(!body.keySet().equals(Set.of(field))||!(("watch".equals(operation)?STATE:TRANSACTION).matcher(value).matches()))throw invalid();
            var payload=new LinkedHashMap<String,Object>();payload.put("operation","reset-"+operation);
            payload.put("watch".equals(operation)?"state":"transaction_id",value);
            JsonNode reply;
            try {reply=bridge.authority(payload,2048,"RESET_OBSERVATION_UNAVAILABLE","暂时无法确认密码重置状态，请稍后重试。");}
            finally {payload.clear();}
            if(!reply.isObject()||reply.size()!=3||!reply.has("schema")||!reply.has("transaction_id")||!reply.has("status")
                    ||!SCHEMA.equals(reply.path("schema").asText())||!reply.path("transaction_id").isTextual()
                    ||!TRANSACTION.matcher(reply.path("transaction_id").asText()).matches()
                    ||!reply.path("status").isTextual()||!STATUSES.contains(reply.path("status").asText())
                    ||"watch".equals(operation)&&!"PENDING".equals(reply.path("status").asText())
                    ||"status".equals(operation)&&!value.equals(reply.path("transaction_id").asText()))throw unavailable();
            var result=new LinkedHashMap<String,Object>();result.put("schema",SCHEMA);
            result.put("transaction_id",reply.path("transaction_id").asText());result.put("status",reply.path("status").asText());
            return success(result);
        } catch(IdentityFailure failure){return error(failure.code,failure.getMessage(),failure.status);}
        catch(Exception failure){return error("RESET_REQUEST_INVALID","重置状态请求无法处理。",400);}
        finally {if(body!=null)body.clear();}
    }

    private static void transport(HttpServletRequest request) {
        if(!"https".equalsIgnoreCase(request.getScheme())||nonempty(request.getHeader("Origin"))
                ||request.getCookies()!=null&&request.getCookies().length>0
                ||nonempty(request.getHeader(HttpHeaders.AUTHORIZATION)))
            throw new IdentityFailure(403,"RESET_TRANSPORT_REQUIRED","需要安全的原生账号连接。");
    }
    private static Map<String,Object> readBody(HttpServletRequest request)throws IOException {
        String type=request.getContentType();
        if(type==null||!type.split(";",2)[0].equals("application/json"))throw invalid();
        byte[] raw=request.getInputStream().readNBytes(1025);
        try {
            if(raw.length>1024)throw invalid();
            final String text;
            try{text=StandardCharsets.UTF_8.newDecoder().decode(ByteBuffer.wrap(raw)).toString();}
            catch(CharacterCodingException failure){throw invalid();}
            JsonNode node;
            try{node=JSON.readTree(text);}catch(Exception failure){throw invalid();}
            if(node==null||!node.isObject())throw invalid();
            var result=new LinkedHashMap<String,Object>();node.fields().forEachRemaining(entry->result.put(entry.getKey(),entry.getValue().isTextual()?entry.getValue().asText():entry.getValue()));
            return result;
        } finally {java.util.Arrays.fill(raw,(byte)0);}
    }
    private static boolean nonempty(String value){return value!=null&&!value.isEmpty();}
    private static IdentityFailure invalid(){return new IdentityFailure(400,"RESET_REQUEST_INVALID","重置状态请求无法处理。");}
    private static IdentityFailure unavailable(){return new IdentityFailure(503,"RESET_OBSERVATION_UNAVAILABLE","暂时无法确认密码重置状态，请稍后重试。");}
    private static IdentityFailure notReady(){return new IdentityFailure(503,"RESET_OBSERVATION_UNAVAILABLE","账号服务暂不可用。");}
    private static ResponseEntity<byte[]> methodNotAllowed() {
        byte[] body="{\"detail\":\"Method Not Allowed\"}".getBytes(StandardCharsets.UTF_8);
        return ResponseEntity.status(405).header("Allow","POST").contentType(MediaType.APPLICATION_JSON)
            .contentLength(body.length).body(body);
    }
    private static ResponseEntity<byte[]> success(Map<String,Object> value) {
        try {byte[] bytes=JSON.writeValueAsBytes(value);return ResponseEntity.ok().contentType(MediaType.APPLICATION_JSON)
            .header(HttpHeaders.CACHE_CONTROL,"no-store").header(HttpHeaders.PRAGMA,"no-cache").contentLength(bytes.length).body(bytes);}
        catch(Exception failure){return error("RESET_OBSERVATION_UNAVAILABLE","暂时无法确认密码重置状态，请稍后重试。",503);}
    }
    private static ResponseEntity<byte[]> error(String code,String message,int status) {
        try {var value=new LinkedHashMap<String,Object>();value.put("code",code);value.put("message",message);byte[] bytes=JSON.writeValueAsBytes(value);
            return ResponseEntity.status(status).contentType(MediaType.APPLICATION_JSON).header(HttpHeaders.CACHE_CONTROL,"no-store")
                .contentLength(bytes.length).body(bytes);}
        catch(Exception failure){return ResponseEntity.status(503).build();}
    }
}
