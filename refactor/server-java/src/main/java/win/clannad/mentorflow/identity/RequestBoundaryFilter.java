package win.clannad.mentorflow.identity;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.security.SecureRandom;
import java.util.HexFormat;
import org.springframework.web.filter.OncePerRequestFilter;

public final class RequestBoundaryFilter extends OncePerRequestFilter {
    private static final SecureRandom RANDOM = new SecureRandom();
    private final IdentitySettings settings;
    public RequestBoundaryFilter(IdentitySettings settings) { this.settings=settings; }
    @Override protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain) throws ServletException,IOException {
        String id=request.getHeader("X-Request-ID"); id=id==null?"":id.strip();
        if (!id.matches("[A-Za-z0-9._:-]{1,128}")) { byte[] bytes=new byte[16]; RANDOM.nextBytes(bytes); id=HexFormat.of().formatHex(bytes); }
        request.setAttribute("request_id",id); response.setHeader("X-Request-ID",id);
        response.setHeader("X-MentorFlow-Runtime-Profile","ACCOUNT_CORE_MVP");
        try {
            if (request.getRequestURI().startsWith("/api") && !settings.mode.equals("ENTERPRISE_SERVER"))
                throw new IdentityFailure(503,"ENTERPRISE_IDENTITY_DISABLED","Enterprise identity is not enabled");
            chain.doFilter(request,response);
        } catch (IdentityFailure failure) { ErrorResponses.write(request,response,failure); }
        catch (CompatibleJwtDecoder.PrincipalValidationFailure failure) {
            // The Python outer error middleware does not retain identity headers.
            response.reset();
            byte[] body="Internal Server Error".getBytes(java.nio.charset.StandardCharsets.UTF_8);
            request.setAttribute("python_principal_failure",Boolean.TRUE);
            response.setStatus(500);response.setContentType("text/plain");
            response.setContentLength(body.length);response.getOutputStream().write(body);
        }
    }
}
