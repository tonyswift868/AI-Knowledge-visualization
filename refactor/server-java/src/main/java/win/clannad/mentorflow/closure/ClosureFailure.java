package win.clannad.mentorflow.closure;

/** Domain failure carrying the frozen Python closure code and HTTP status. */
public final class ClosureFailure extends RuntimeException {
    public final String code;
    public final int status;

    public ClosureFailure(String code) { this(code, 503); }
    public ClosureFailure(String code, int status) {
        this(code, status, null);
    }
    public ClosureFailure(String code, int status, Throwable cause) {
        super(code, cause);
        this.code = code;
        this.status = status;
    }
}
