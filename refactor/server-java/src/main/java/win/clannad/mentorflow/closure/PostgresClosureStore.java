package win.clannad.mentorflow.closure;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.Map;
import javax.sql.DataSource;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.TransactionException;
import org.springframework.transaction.support.TransactionTemplate;

/** EXECUTE-only adapter to frozen account_closure PostgreSQL functions. */
public final class PostgresClosureStore implements ClosureStore {
    private static final ObjectMapper JSON = new ObjectMapper();
    private final DataSource dataSource;
    private final NamedParameterJdbcTemplate jdbc;
    private final DataSourceTransactionManager transactions;
    private final String issuer;

    public PostgresClosureStore(DataSource dataSource, String issuer) {
        if (dataSource == null || issuer == null || issuer.isBlank()) throw new IllegalArgumentException("closure store configuration required");
        this.dataSource = dataSource;
        this.jdbc = new NamedParameterJdbcTemplate(dataSource);
        this.transactions = new DataSourceTransactionManager(dataSource);
        this.issuer = issuer;
    }

    @Override public String issuer() { return issuer; }
    @Override public void ensureOpen(String issuer, String subject) {
        Map<String,Object> value = call("check_open", Map.of("p_issuer",issuer,"p_subject",subject));
        if (Boolean.TRUE.equals(value.get("closed"))) throw new ClosureFailure("ACCOUNT_CLOSURE_IN_PROGRESS",403);
    }
    @Override public Map<String,Object> prepare(String issuer, String subject, String account, String operation, String digest) {
        return call("prepare", Map.of("p_issuer",issuer,"p_subject",subject,"p_account",account,"p_operation",operation,"p_digest",digest));
    }
    @Override public Map<String,Object> status(String operation, String digest) {
        return call("status", Map.of("p_operation",operation,"p_digest",digest));
    }
    @Override public Map<String,Object> acquire(String operation, String digest, String claim) {
        return call("acquire", Map.of("p_operation",operation,"p_digest",digest,"p_claim",claim));
    }
    @Override public Map<String,Object> clean(String operation, String digest, String claim) {
        return call("clean", Map.of("p_operation",operation,"p_digest",digest,"p_claim",claim));
    }
    @Override public Map<String,Object> providerDone(String operation, String digest, String claim) {
        return call("provider_done", Map.of("p_operation",operation,"p_digest",digest,"p_claim",claim));
    }
    @Override public Map<String,Object> complete(String operation, String digest, String claim) {
        return call("complete", Map.of("p_operation",operation,"p_digest",digest,"p_claim",claim));
    }
    @Override public Map<String,Object> fail(String operation, String digest, String claim, String code) {
        return call("fail", Map.of("p_operation",operation,"p_digest",digest,"p_claim",claim,"p_code",code));
    }

    private Map<String,Object> call(String function, Map<String,Object> params) {
        // PostgreSQL named-argument notation keeps the function contract stable
        // even when the input map is an unordered Map.of instance.
        String sql = "SELECT account_closure." + function + "(" + String.join(",", params.keySet().stream()
            .map(k -> k + " => :" + k).toList()) + ")";
        TransactionTemplate tx = new TransactionTemplate(transactions);
        try {
            Object value = tx.execute(status -> {
                jdbc.getJdbcTemplate().execute("SET LOCAL lock_timeout = '3s'");
                jdbc.getJdbcTemplate().execute("SET LOCAL statement_timeout = '10s'");
                return jdbc.queryForObject(sql, params, Object.class);
            });
            Map<String,Object> result = asObject(value);
            Object error = result.get("error");
            if (error != null) {
                String code = String.valueOf(error);
                int http = code.equals("CLOSURE_CREDENTIAL_INVALID") ? 403
                    : code.equals("CLOSURE_SCOPE_UNRESOLVED") || code.equals("CLOSURE_ALREADY_COMMITTED") ? 409 : 503;
                throw new ClosureFailure(code,http);
            }
            return result;
        } catch (ClosureFailure e) { throw e; }
        catch (DataAccessException | TransactionException e) { throw new ClosureFailure("CLOSURE_STORAGE_UNAVAILABLE", 503, e); }
        catch (RuntimeException e) { throw new ClosureFailure("CLOSURE_STORAGE_INVALID", 503, e); }
    }

    @SuppressWarnings("unchecked") private static Map<String,Object> asObject(Object value) {
        try {
            Object parsed = value instanceof Map<?,?> ? value : JSON.readValue(String.valueOf(value), Object.class);
            if (!(parsed instanceof Map<?,?> map)) throw new IllegalArgumentException();
            return (Map<String,Object>)map;
        } catch (Exception e) { throw new IllegalArgumentException("closure storage response invalid",e); }
    }
}
