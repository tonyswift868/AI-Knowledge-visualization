package win.clannad.mentorflow.rag;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.DeserializationFeature;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.Map;
import org.springframework.dao.DataAccessException;
import org.springframework.web.bind.annotation.*;
import win.clannad.mentorflow.identity.IdentityFailure;

/** Text-only RAG endpoints over the existing verified account context. */
@RestController
@RequestMapping("/api/enterprise/rag/v1")
public final class RagController {
    public static final int MAX_REQUEST_BYTES = 9 * 1024 * 1024;
    private final RagService service;
    private final ObjectMapper json;
    public RagController(RagService service, ObjectMapper json) {this.service=service; this.json=json;}

    @PostMapping("/documents/{doc_id}/chunks")
    public Object upload(HttpServletRequest request, HttpServletResponse response,
                         @PathVariable("doc_id") String documentId) throws IOException {
        return run(response, () -> service.upload(actor(request), documentId, body(request)));
    }
    @GetMapping("/documents")
    public Object documents(HttpServletRequest request, HttpServletResponse response) {
        return run(response, () -> Map.of("documents",service.documents(actor(request))));
    }
    @DeleteMapping("/documents/{doc_id}")
    public Object delete(HttpServletRequest request, HttpServletResponse response,
                         @PathVariable("doc_id") String documentId) {
        return run(response, () -> service.delete(actor(request),documentId));
    }
    @PostMapping("/search")
    public Object search(HttpServletRequest request, HttpServletResponse response) throws IOException {
        return run(response, () -> service.search(actor(request),body(request)));
    }
    @SuppressWarnings("unchecked")
    private static Map<String,Object> actor(HttpServletRequest request) {
        if (!(request.getAttribute("actor_context") instanceof Map<?,?> context)
                || !"HUMAN".equals(context.get("actor_kind")))
            throw new IdentityFailure("ENTERPRISE_AUTH_REQUIRED","Authenticated account required",401);
        return (Map<String,Object>)context;
    }
    @SuppressWarnings("unchecked")
    private Map<String,Object> body(HttpServletRequest request) {
        try {
            if (request.getContentLengthLong()>MAX_REQUEST_BYTES) throw tooLarge();
            byte[] bytes=request.getInputStream().readNBytes(MAX_REQUEST_BYTES+1);
            if(bytes.length>MAX_REQUEST_BYTES) throw tooLarge();
            Object value=json.readerFor(Object.class)
                .with(DeserializationFeature.FAIL_ON_TRAILING_TOKENS).readValue(bytes);
            if(!(value instanceof Map)) throw invalid();
            return (Map<String,Object>)value;
        } catch(JsonProcessingException failure) {throw invalid();}
        catch(IOException failure) {throw new IdentityFailure("RAG_BODY_UNAVAILABLE","Cannot read request body",400);}
    }
    private static IdentityFailure tooLarge() {return new IdentityFailure("RAG_REQUEST_TOO_LARGE","Request must not exceed 9 MiB (9437184 bytes)",413);}
    private static IdentityFailure invalid() {return new IdentityFailure("RAG_INVALID_REQUEST","Expected a valid JSON object",422);}
    @FunctionalInterface private interface Work {Object run();}
    private static Object run(HttpServletResponse response,Work work) {
        response.setHeader("Cache-Control","no-store");
        try {return work.run();}
        catch(RagFailure failure) {throw new IdentityFailure(failure.code,failure.getMessage(),failure.status);}
        catch(DataAccessException failure) {throw new IdentityFailure("RAG_UNAVAILABLE","Document storage temporarily unavailable",503);}
    }
}
