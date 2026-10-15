package win.clannad.mentorflow.identity;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/** Python AppError's insertion order and UTF-8 bytes are part of the HTTP contract. */
@RestControllerAdvice
public final class ErrorResponses {
    private static final ObjectMapper JSON = new ObjectMapper();
    public static void write(HttpServletRequest request, HttpServletResponse response, IdentityFailure failure) throws IOException {
        var error = new LinkedHashMap<String,Object>();
        error.put("code", failure.code); error.put("message", failure.getMessage());
        error.put("details", Map.of()); error.put("request_id", request.getAttribute("request_id"));
        byte[] body = JSON.writeValueAsBytes(Map.of("error",error));
        response.setStatus(failure.status);
        response.setContentType("application/json");
        response.setContentLength(body.length);
        if (failure.code.startsWith("CATALOG_")) response.setHeader("Cache-Control","no-store");
        response.getOutputStream().write(body);
    }
    @ExceptionHandler(IdentityFailure.class)
    public void failure(IdentityFailure failure, HttpServletRequest request, HttpServletResponse response) throws IOException {
        write(request,response,failure);
    }
}
