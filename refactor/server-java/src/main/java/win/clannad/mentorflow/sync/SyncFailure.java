package win.clannad.mentorflow.sync;

/** A structured-sync domain failure, distinct from identity's error envelope. */
public final class SyncFailure extends RuntimeException {
    public final String code;
    public final int status;
    public SyncFailure(String code, int status) { super(code); this.code=code; this.status=status; }
}
