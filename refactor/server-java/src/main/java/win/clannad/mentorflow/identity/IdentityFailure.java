package win.clannad.mentorflow.identity;

import org.springframework.security.oauth2.jwt.BadJwtException;

/** Preserve Python's code, message and status, rather than Spring's default body. */
public final class IdentityFailure extends BadJwtException {
    public IdentityFailure(int status, String code, String message) { this(code, message, status); }
    public final String code;
    public final int status;
    public IdentityFailure(String code, String message) { this(code, message, 401); }
    public IdentityFailure(String code, String message, int status) {
        super(message); this.code=code; this.status=status;
    }
}
