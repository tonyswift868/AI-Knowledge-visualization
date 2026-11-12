package win.clannad.mentorflow.sync;

/** A structured-sync domain failure, distinct from identity's error envelope. */
public final class SyncFailure extends RuntimeException {
    public final String code;
    public final int status;
    public SyncFailure(String code, int status) { this(code, status, null); }
    public SyncFailure(String code, int status, Throwable cause) { super(code, cause); this.code=code; this.status=status; }
}
