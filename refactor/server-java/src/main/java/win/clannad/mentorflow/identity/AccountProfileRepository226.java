package win.clannad.mentorflow.identity;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.HexFormat;
import javax.sql.DataSource;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.stereotype.Component;
import org.springframework.transaction.TransactionException;
import org.springframework.transaction.support.TransactionTemplate;

/** RLS-scoped account-profile storage; account ownership is derived from authenticated JWT claims. */
@Component
public final class AccountProfileRepository226 {
    private final JdbcTemplate jdbc;
    private final TransactionTemplate transaction;
    private final boolean ready;

    public AccountProfileRepository226(ObjectProvider<DataSource> provider) {
        DataSource source = provider.getIfAvailable();
        if (source == null) {
            jdbc = null; transaction = null; ready = false;
        } else {
            jdbc = new JdbcTemplate(source);
            transaction = new TransactionTemplate(new DataSourceTransactionManager(source));
            ready = true;
        }
    }

    Profile profile(String issuer, String subject) {
        String scope = scope(issuer, subject);
        return run(issuer, subject, scope, false, () -> jdbc.query(
            "SELECT nickname,signature,avatar_bytes,avatar_etag,avatar_width,avatar_height,version "
                + "FROM public.account_profiles WHERE account_scope=?",
            rs -> rs.next() ? row(rs) : Profile.empty(), scope));
    }

    Profile updateText(String issuer, String subject, long expectedVersion, String nickname, String signature) {
        String scope = scope(issuer, subject);
        return run(issuer, subject, scope, true, () -> {
            Profile updated = jdbc.query(
                "UPDATE public.account_profiles SET nickname=?,signature=?,version=version+1,updated_at=clock_timestamp() "
                    + "WHERE account_scope=? AND version=? RETURNING nickname,signature,avatar_bytes,avatar_etag,avatar_width,avatar_height,version",
                rs -> rs.next() ? row(rs) : null, nickname, signature, scope, expectedVersion);
            if (updated != null) return updated;
            if (expectedVersion == 1) {
                updated = jdbc.query(
                    "INSERT INTO public.account_profiles(account_scope,nickname,signature,version) VALUES(?,?,?,?) "
                        + "ON CONFLICT(account_scope) DO NOTHING RETURNING nickname,signature,avatar_bytes,avatar_etag,avatar_width,avatar_height,version",
                    rs -> rs.next() ? row(rs) : null, scope, nickname, signature, expectedVersion + 1);
                if (updated != null) return updated;
            }
            throw conflict(scope);
        });
    }

    Profile updateAvatar(String issuer, String subject, long expectedVersion, byte[] bytes, String etag, int width, int height) {
        String scope = scope(issuer, subject);
        return run(issuer, subject, scope, true, () -> {
            Profile updated = jdbc.query(
                "UPDATE public.account_profiles SET avatar_bytes=?,avatar_etag=?,avatar_width=?,avatar_height=?,version=version+1,updated_at=clock_timestamp() "
                    + "WHERE account_scope=? AND version=? RETURNING nickname,signature,avatar_bytes,avatar_etag,avatar_width,avatar_height,version",
                rs -> rs.next() ? row(rs) : null, bytes, etag, width, height, scope, expectedVersion);
            if (updated != null) return updated;
            if (expectedVersion == 1) {
                updated = jdbc.query(
                    "INSERT INTO public.account_profiles(account_scope,avatar_bytes,avatar_etag,avatar_width,avatar_height,version) VALUES(?,?,?,?,?,?) "
                        + "ON CONFLICT(account_scope) DO NOTHING RETURNING nickname,signature,avatar_bytes,avatar_etag,avatar_width,avatar_height,version",
                    rs -> rs.next() ? row(rs) : null, scope, bytes, etag, width, height, expectedVersion + 1);
                if (updated != null) return updated;
            }
            throw conflict(scope);
        });
    }

    Profile deleteAvatar(String issuer, String subject, long expectedVersion) {
        String scope = scope(issuer, subject);
        return run(issuer, subject, scope, true, () -> {
            Profile current = jdbc.query(
                "SELECT nickname,signature,avatar_bytes,avatar_etag,avatar_width,avatar_height,version "
                    + "FROM public.account_profiles WHERE account_scope=? AND version=? FOR UPDATE",
                rs -> rs.next() ? row(rs) : null, scope, expectedVersion);
            if (current == null) {
                if (expectedVersion == 1 && !exists(scope)) return Profile.empty();
                throw conflict(scope);
            }
            if (current.avatarBytes == null) return current;
            return jdbc.query(
                "UPDATE public.account_profiles SET avatar_bytes=NULL,avatar_etag=NULL,avatar_width=NULL,avatar_height=NULL,version=version+1,updated_at=clock_timestamp() "
                    + "WHERE account_scope=? AND version=? RETURNING nickname,signature,avatar_bytes,avatar_etag,avatar_width,avatar_height,version",
                rs -> rs.next() ? row(rs) : null, scope, expectedVersion);
        });
    }

    private <T> T run(String issuer, String subject, String scope, boolean writing, java.util.concurrent.Callable<T> action) {
        if (!ready) throw new IdentityFailure(503, "ACCOUNT_PROFILE_UNAVAILABLE", "账号资料服务暂不可用");
        try {
            return transaction.execute(status -> {
                jdbc.query("SELECT set_config('mentorflow.rag_account_scope', ?, true), "
                        + "set_config('mentorflow.actor_issuer', ?, true), set_config('mentorflow.actor_subject', ?, true)",
                    rs -> null, scope, issuer, subject);
                if (writing) {
                    // Match account closure's operations-row -> profile-advisory -> profile-row lock order.
                    // guard_row checks the closure fence before skipping these null row values.
                    jdbc.query("SELECT account_closure.guard_row('public','account_profiles',NULL::jsonb,NULL::jsonb)", rs -> null);
                }
                if (writing) jdbc.query("SELECT pg_advisory_xact_lock(hashtextextended('mentorflow.rag:' || ?, 0))", rs -> null, scope);
                try { return action.call(); }
                catch (IdentityFailure failure) { throw failure; }
                catch (DataAccessException failure) { throw failure; }
                catch (Exception failure) { throw new IllegalStateException(failure); }
            });
        } catch (IdentityFailure failure) {
            throw failure;
        } catch (DataAccessException | TransactionException failure) {
            throw new IdentityFailure("ACCOUNT_PROFILE_STORAGE_UNAVAILABLE", "账号资料暂时无法读取或保存", 503, failure);
        }
    }

    private static Profile row(ResultSet rs) throws SQLException {
        return new Profile(rs.getString("nickname"), rs.getString("signature"), rs.getBytes("avatar_bytes"),
            rs.getString("avatar_etag"), (Integer) rs.getObject("avatar_width"), (Integer) rs.getObject("avatar_height"), rs.getLong("version"));
    }

    private boolean exists(String scope) {
        return Boolean.TRUE.equals(jdbc.queryForObject(
            "SELECT EXISTS(SELECT 1 FROM public.account_profiles WHERE account_scope=?)",
            Boolean.class, scope));
    }

    private static IdentityFailure conflict(String scope) {
        return new IdentityFailure(412, "ACCOUNT_PROFILE_VERSION_CONFLICT", "账号资料已在另一台设备更新，请重新加载后再保存");
    }

    static String scope(String issuer, String subject) {
        try {
            byte[] input = (issuer + "\u0000" + subject).getBytes(StandardCharsets.UTF_8);
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(input));
        } catch (Exception failure) { throw new IllegalStateException(failure); }
    }

    static final class Profile {
        final String nickname, signature, avatarEtag;
        final byte[] avatarBytes;
        final Integer avatarWidth, avatarHeight;
        final long version;
        Profile(String nickname, String signature, byte[] avatarBytes, String avatarEtag, Integer avatarWidth, Integer avatarHeight, long version) {
            this.nickname = nickname; this.signature = signature; this.avatarBytes = avatarBytes; this.avatarEtag = avatarEtag;
            this.avatarWidth = avatarWidth; this.avatarHeight = avatarHeight; this.version = version;
        }
        static Profile empty() { return new Profile("", "", null, null, null, null, 1); }
    }
}
