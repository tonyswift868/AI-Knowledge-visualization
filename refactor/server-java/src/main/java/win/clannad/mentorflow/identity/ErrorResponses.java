package win.clannad.mentorflow.identity;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;
import org.springframework.web.servlet.HandlerMapping;

/** Python AppError's insertion order and UTF-8 bytes are part of the HTTP contract. */
@RestControllerAdvice
public final class ErrorResponses {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final Logger LOG = LoggerFactory.getLogger(ErrorResponses.class);
    public static void write(HttpServletRequest request, HttpServletResponse response, IdentityFailure failure) throws IOException {
        if (failure.status >= 500) {
            log(request, failure.code, failure, false);
        }
        var error = new LinkedHashMap<String,Object>();
        error.put("code", failure.code); error.put("message", failure.getMessage());
        error.put("details", Map.of()); error.put("request_id", request.getAttribute("request_id"));
        byte[] body = JSON.writeValueAsBytes(Map.of("error",error));
        response.setStatus(failure.status);
        response.setContentType("application/json");
        response.setContentLength(body.length);
        if (failure.code.startsWith("CATALOG_")) response.setHeader("Cache-Control","no-store");
        if(java.util.Set.of("ENTERPRISE_SESSION_INVALID","ENTERPRISE_SESSION_REFRESH_EXPIRED",
                "IDENTITY_DISABLED_OR_REVOKED","OIDC_TOKEN_EXPIRED").contains(failure.code)
                && request.getAttribute("identity_settings") instanceof IdentitySettings settings
                && request.getCookies()!=null) {
            for(var cookie:request.getCookies())if(cookie.getName().equals(settings.cookieName)&&!cookie.getValue().isEmpty()) {
                String expires=java.time.format.DateTimeFormatter.ofPattern("EEE, dd MMM yyyy HH:mm:ss 'GMT'",java.util.Locale.US)
                    .format(java.time.ZonedDateTime.now(java.time.ZoneOffset.UTC));
                response.addHeader("Set-Cookie",settings.cookieName+"=\"\"; expires="+expires+"; HttpOnly; Max-Age=0; Path=/; SameSite=lax"+(settings.cookieSecure?"; Secure":""));break;
            }
        }
        response.getOutputStream().write(body);
    }
    /** Records a safe failure context for boundaries that serialize their own response. */
    public static void log(HttpServletRequest request, String code, Throwable failure, boolean unexpected) {
        String route = request.getAttribute(HandlerMapping.BEST_MATCHING_PATTERN_ATTRIBUTE) instanceof String pattern
                ? pattern : request.getRequestURI();
        String requestId = String.valueOf(request.getAttribute("request_id"));
        String exception = exceptionClass(failure);
        String sqlState = sqlState(failure);
        if (unexpected) {
            LOG.error("service_error code={} request_id={} route={} exception={} sqlState={}",
                    code, requestId, route, exception, sqlState, failure);
        } else {
            LOG.warn("service_error code={} request_id={} route={} exception={} sqlState={}",
                    code, requestId, route, exception, sqlState);
        }
    }
    public static void logCurrent(String code, Throwable failure, boolean unexpected) {
        if (RequestContextHolder.getRequestAttributes() instanceof ServletRequestAttributes attributes)
            log(attributes.getRequest(), code, failure, unexpected);
    }
    private static String sqlState(Throwable failure) {
        Throwable current=failure;
        while(current!=null) {
            if(current instanceof java.sql.SQLException sql && sql.getSQLState()!=null)return sql.getSQLState();
            current=current.getCause();
        }
        return "";
    }
    private static String exceptionClass(Throwable failure) {
        Throwable current=failure, deepest=failure;
        while(current!=null) { deepest=current; current=current.getCause(); }
        return deepest == null ? "" : deepest.getClass().getName();
    }
    @ExceptionHandler(IdentityFailure.class)
    public void failure(IdentityFailure failure, HttpServletRequest request, HttpServletResponse response) throws IOException {
        write(request,response,failure);
    }
}
