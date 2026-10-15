package win.clannad.mentorflow.closure;

import java.util.Map;

/** Durable operations backed by the already-installed PostgreSQL 0041 functions. */
public interface ClosureStore {
    String issuer();
    void ensureOpen(String issuer, String subject);
    Map<String,Object> prepare(String issuer, String subject, String accountId, String operationId, String digest);
    Map<String,Object> status(String operationId, String digest);
    Map<String,Object> acquire(String operationId, String digest, String claim);
    Map<String,Object> clean(String operationId, String digest, String claim);
    Map<String,Object> providerDone(String operationId, String digest, String claim);
    Map<String,Object> complete(String operationId, String digest, String claim);
    Map<String,Object> fail(String operationId, String digest, String claim, String code);
}
