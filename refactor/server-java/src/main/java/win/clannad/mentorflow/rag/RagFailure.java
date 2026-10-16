package win.clannad.mentorflow.rag;

/** A stable client-facing RAG failure, converted to the shared identity envelope by the controller. */
public final class RagFailure extends RuntimeException {
    public final String code;
    public final int status;

    public RagFailure(String code, String message, int status) {
        super(message);
        this.code = code;
        this.status = status;
    }
}
