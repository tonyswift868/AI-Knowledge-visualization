package win.clannad.mentorflow.persistence;

import com.zaxxer.hikari.HikariDataSource;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Proxy;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicBoolean;
import javax.sql.DataSource;
import org.springframework.core.env.MapPropertySource;
import org.springframework.core.env.StandardEnvironment;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DataSourceUtils;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.ConnectionCallback;
import org.springframework.transaction.support.TransactionTemplate;

/** Offline checks for the shared business pool; all physical JDBC calls are local proxies. */
public final class BusinessDatabase323Main {
    private BusinessDatabase323Main() {}

    public static void main(String[] args) throws Exception {
        missingAndPresentUrl();
        reuseTransactionsAndResetState();
        borrowTimeout();
        brokenConnectionIsEvicted();
        shutdownIsIdempotent();
        System.out.println("BUSINESS_DATABASE_323_OFFLINE_OK");
    }

    private static void missingAndPresentUrl() throws Exception {
        BusinessDatabase missing = new BusinessDatabase(environment(Map.of()));
        check(missing.dataSource() == null, "missing URL must not create a pool");
        missing.close();

        StandardEnvironment environment = environment(Map.of("MENTORFLOW_DATABASE_URL",
                "jdbc:postgresql://localhost:5432/mentorflow"));
        try (BusinessDatabase configured = new BusinessDatabase(environment)) {
            check(configured.dataSource() != null, "JDBC PostgreSQL URL must create the pool");
        }
        try (BusinessDatabase alias = new BusinessDatabase(environment(Map.of("MENTORFLOW_DATABASE_URL",
                "postgres://fixture:fixture@localhost:5432/mentorflow?socketTimeout=0")))) {
            HikariDataSource pool = (HikariDataSource) alias.dataSource();
            check("jdbc:postgresql://localhost:5432/mentorflow?socketTimeout=0".equals(pool.getJdbcUrl()),
                    "postgres:// alias and an explicit disabled socket timeout must remain accepted");
            check("10".equals(pool.getDataSourceProperties().getProperty("connectTimeout")),
                    "business pool must default the PostgreSQL connect timeout to 10 seconds");
            check("10".equals(pool.getDataSourceProperties().getProperty("socketTimeout")),
                    "business pool must default the PostgreSQL socket timeout to 10 seconds");
        }
        try (BusinessDatabase legacyUrl = new BusinessDatabase(environment(Map.of("MENTORFLOW_DATABASE_URL",
                "postgresql+psycopg://alice:s%40cret@db.invalid:5433/mentorflow?sslmode=require&connectTimeout=0&socketTimeout=120")))) {
            check(legacyUrl.dataSource() instanceof HikariDataSource, "existing PostgreSQL URL form must create the pool");
            HikariDataSource pool = (HikariDataSource) legacyUrl.dataSource();
            check("jdbc:postgresql://db.invalid:5433/mentorflow?sslmode=require&connectTimeout=0&socketTimeout=120"
                    .equals(pool.getJdbcUrl()), "normalized URL parameters must remain unchanged");
            check("alice".equals(pool.getUsername()), "URL username was not decoded/retained");
            check("s@cret".equals(pool.getPassword()), "URL password was not decoded/retained");
            check(pool.getJdbcUrl().contains("sslmode=require"), "URL sslmode was not retained");
            check("10".equals(pool.getDataSourceProperties().getProperty("connectTimeout")),
                    "explicit URL timeout must not remove the connect timeout default");
            check("10".equals(pool.getDataSourceProperties().getProperty("socketTimeout")),
                    "explicit URL timeout must not remove the socket timeout default");
        }
    }

    private static void reuseTransactionsAndResetState() throws Exception {
        FakeDataSource physical = new FakeDataSource();
        try (HikariDataSource pool = BusinessDatabase.createPool(physical)) {
            DataSourceTransactionManager manager = new DataSourceTransactionManager(pool);
            TransactionTemplate tx = new TransactionTemplate(manager);
            JdbcTemplate jdbc = new JdbcTemplate(pool);
            int[] ids = new int[2];
            tx.execute(status -> {
                Connection bound = DataSourceUtils.getConnection(pool);
                try {
                    ids[0] = physical.id(bound);
                } catch (SQLException exception) {
                    throw new AssertionError("cannot identify transaction-bound connection", exception);
                }
                jdbc.execute((ConnectionCallback<Void>) connection -> {
                    try {
                        check(physical.id(connection) == ids[0], "JdbcTemplate must share transaction connection");
                    } catch (SQLException exception) {
                        throw new AssertionError("cannot identify JdbcTemplate connection", exception);
                    }
                    return null;
                });
                try {
                    check(physical.id(DataSourceUtils.getConnection(pool)) == ids[0], "same transaction must reuse connection");
                } catch (SQLException exception) {
                    throw new AssertionError("cannot identify transaction-bound connection", exception);
                }
                return null;
            });
            check(physical.connectionsCreated() == 1, "pool should reuse one physical connection");
            check(physical.commits() == 1, "successful TransactionTemplate must commit exactly once");
            check(physical.rollbacks() == 0, "successful TransactionTemplate must not roll back");

            try (Connection connection = pool.getConnection()) {
                ids[1] = physical.id(connection);
                check(ids[0] == ids[1], "returned physical connection must be reused");
                check(connection.getAutoCommit(), "autoCommit reset after transaction");
                check(!connection.isReadOnly(), "readOnly reset after transaction");
                check(connection.getTransactionIsolation() == Connection.TRANSACTION_READ_COMMITTED,
                        "isolation reset after transaction");
            }

            try {
                tx.execute(status -> {
                    Connection connection = DataSourceUtils.getConnection(pool);
                    try {
                        connection.setReadOnly(true);
                        connection.setTransactionIsolation(Connection.TRANSACTION_SERIALIZABLE);
                    } catch (SQLException exception) {
                        throw new AssertionError("cannot mutate JDBC state for rollback fixture", exception);
                    }
                    throw new IllegalStateException("rollback fixture");
                });
                throw new AssertionError("transaction fixture should fail");
            } catch (IllegalStateException expected) {
                check("rollback fixture".equals(expected.getMessage()), "unexpected transaction failure");
            }
            check(physical.commits() == 1, "failed TransactionTemplate must not commit");
            check(physical.rollbacks() == 1, "failed TransactionTemplate must roll back exactly once");
            try (Connection connection = pool.getConnection()) {
                check(physical.id(connection) == ids[0], "rollback must return connection to pool");
                check(connection.getAutoCommit(), "autoCommit reset after rollback");
                check(!connection.isReadOnly(), "readOnly reset after rollback");
                check(connection.getTransactionIsolation() == Connection.TRANSACTION_READ_COMMITTED,
                        "isolation reset after rollback");
            }
        }
    }

    private static void borrowTimeout() throws Exception {
        FakeDataSource physical = new FakeDataSource();
        try (HikariDataSource pool = BusinessDatabase.createPool(physical)) {
            // Configure the pool before first checkout so this is deterministic and entirely local.
            pool.setMaximumPoolSize(1);
            pool.setMinimumIdle(0);
            pool.setConnectionTimeout(250);
            try (Connection held = pool.getConnection(); var executor = Executors.newSingleThreadExecutor()) {
                CountDownLatch started = new CountDownLatch(1);
                var waiting = executor.submit(() -> {
                    started.countDown();
                    return pool.getConnection();
                });
                check(started.await(2, TimeUnit.SECONDS), "borrow worker did not start");
                try {
                    waiting.get(2, TimeUnit.SECONDS).close();
                    throw new AssertionError("borrow should time out while max-size connection is held");
                } catch (ExecutionException expected) {
                    check(expected.getCause() instanceof SQLException, "timeout must surface as SQLException");
                }
            }
        }
    }

    private static void brokenConnectionIsEvicted() throws Exception {
        FakeDataSource physical = new FakeDataSource();
        physical.breakNextStatement();
        try (HikariDataSource pool = BusinessDatabase.createPool(physical)) {
            pool.setMaximumPoolSize(1);
            pool.setMinimumIdle(0);
            try (Connection broken = pool.getConnection(); Statement statement = broken.createStatement()) {
                try {
                    statement.execute("synthetic broken connection");
                    throw new AssertionError("synthetic SQLState 08 failure was accepted");
                } catch (SQLException expected) {
                    check(expected.getSQLState().startsWith("08"), "wrong injected SQLState");
                }
            }
            try (Connection replacement = pool.getConnection()) {
                check(physical.id(replacement) != 1, "broken physical connection must be discarded");
            }
        }
    }

    private static void shutdownIsIdempotent() throws Exception {
        FakeDataSource physical = new FakeDataSource();
        HikariDataSource pool = BusinessDatabase.createPool(physical);
        try (Connection ignored = pool.getConnection()) { }
        pool.close();
        pool.close();
        try {
            pool.getConnection().close();
            throw new AssertionError("closed pool handed out a connection");
        } catch (SQLException expected) {
            check(expected.getMessage().toLowerCase().contains("closed"), "unexpected closed-pool error");
        }
    }

    private static StandardEnvironment environment(Map<String, Object> values) {
        StandardEnvironment environment = new StandardEnvironment();
        environment.getPropertySources().remove(StandardEnvironment.SYSTEM_PROPERTIES_PROPERTY_SOURCE_NAME);
        environment.getPropertySources().remove(StandardEnvironment.SYSTEM_ENVIRONMENT_PROPERTY_SOURCE_NAME);
        environment.getPropertySources().addFirst(new MapPropertySource("fixture", values));
        return environment;
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static final class FakeDataSource implements DataSource {
        private final AtomicInteger created = new AtomicInteger();
        private final AtomicInteger commits = new AtomicInteger();
        private final AtomicInteger rollbacks = new AtomicInteger();
        private final AtomicBoolean breakNext = new AtomicBoolean();

        int id(Connection connection) throws SQLException { return connection.unwrap(FakeId.class).fakeId(); }
        int connectionsCreated() { return created.get(); }
        int commits() { return commits.get(); }
        int rollbacks() { return rollbacks.get(); }
        void breakNextStatement() { breakNext.set(true); }

        @Override public Connection getConnection() {
            int id = created.incrementAndGet();
            return FakeConnection.proxy(id, () -> breakNext.getAndSet(false), commits, rollbacks);
        }
        @Override public Connection getConnection(String username, String password) { return getConnection(); }
        @Override public java.io.PrintWriter getLogWriter() { return null; }
        @Override public void setLogWriter(java.io.PrintWriter out) { }
        @Override public void setLoginTimeout(int seconds) { }
        @Override public int getLoginTimeout() { return 0; }
        @Override public java.util.logging.Logger getParentLogger() { return java.util.logging.Logger.getGlobal(); }
        @Override public <T> T unwrap(Class<T> iface) throws SQLException {
            if (iface.isInstance(this)) return iface.cast(this);
            throw new SQLException("not a wrapper");
        }
        @Override public boolean isWrapperFor(Class<?> iface) { return iface.isInstance(this); }
    }

    @FunctionalInterface private interface BreakFlag { boolean consume(); }

    private static final class FakeConnection implements InvocationHandler {
        private final int id;
        private final BreakFlag breakFlag;
        private final AtomicInteger commits;
        private final AtomicInteger rollbacks;
        private boolean closed;
        private boolean autoCommit = true;
        private boolean readOnly;
        private int isolation = Connection.TRANSACTION_READ_COMMITTED;
        private String catalog;
        private String schema;

        private FakeConnection(int id, BreakFlag breakFlag, AtomicInteger commits, AtomicInteger rollbacks) {
            this.id = id;
            this.breakFlag = breakFlag;
            this.commits = commits;
            this.rollbacks = rollbacks;
        }
        static Connection proxy(int id, BreakFlag breakFlag, AtomicInteger commits, AtomicInteger rollbacks) {
            return (Connection) Proxy.newProxyInstance(BusinessDatabase323Main.class.getClassLoader(),
                    new Class<?>[]{Connection.class, FakeId.class}, new FakeConnection(id, breakFlag, commits, rollbacks));
        }

        @Override public Object invoke(Object proxy, java.lang.reflect.Method method, Object[] args) throws Throwable {
            String name = method.getName();
            if (name.equals("fakeId")) return id;
            return switch (name) {
                case "toString" -> "FakeConnection-" + id;
                case "hashCode" -> System.identityHashCode(proxy);
                case "equals" -> proxy == args[0];
                case "isClosed" -> closed;
                case "close" -> { closed = true; yield null; }
                case "isValid" -> !closed;
                case "getAutoCommit" -> autoCommit;
                case "setAutoCommit" -> { autoCommit = (boolean) args[0]; yield null; }
                case "isReadOnly" -> readOnly;
                case "setReadOnly" -> { readOnly = (boolean) args[0]; yield null; }
                case "getTransactionIsolation" -> isolation;
                case "setTransactionIsolation" -> { isolation = (int) args[0]; yield null; }
                case "getCatalog" -> catalog;
                case "setCatalog" -> { catalog = (String) args[0]; yield null; }
                case "getSchema" -> schema;
                case "setSchema" -> { schema = (String) args[0]; yield null; }
                case "getNetworkTimeout" -> 0;
                case "setNetworkTimeout", "clearWarnings", "abort", "releaseSavepoint" -> null;
                case "commit" -> { if (autoCommit) throw new SQLException("commit while autoCommit"); commits.incrementAndGet(); yield null; }
                case "rollback" -> { if (autoCommit) throw new SQLException("rollback while autoCommit"); rollbacks.incrementAndGet(); yield null; }
                case "getWarnings" -> null;
                case "createStatement", "prepareStatement" -> statementProxy(args != null && args.length > 0 ? String.valueOf(args[0]) : null);
                case "nativeSQL" -> args[0];
                case "getMetaData" -> defaultValue(method.getReturnType());
                case "unwrap" -> { Class<?> type = (Class<?>) args[0]; if (type.isInstance(proxy)) yield type.cast(proxy); throw new SQLException("not a wrapper"); }
                case "isWrapperFor" -> ((Class<?>) args[0]).isInstance(proxy);
                default -> defaultValue(method.getReturnType());
            };
        }

        private Object statementProxy(String sql) {
            Class<?> type = sql == null ? Statement.class : PreparedStatement.class;
            return Proxy.newProxyInstance(type.getClassLoader(), new Class<?>[]{type}, (proxy, method, args) -> {
                String name = method.getName();
                if (name.equals("toString")) return "FakeStatement-" + id;
                if (name.equals("close") || name.equals("clearParameters") || name.equals("clearWarnings")) return null;
                if (name.equals("isClosed")) return false;
                if (name.equals("getConnection")) return FakeConnection.proxy(id, breakFlag, commits, rollbacks);
                if (name.startsWith("execute") && breakFlag.consume())
                    throw new SQLException("synthetic broken connection", "08006");
                return defaultValue(method.getReturnType());
            });
        }
    }

    private interface FakeId { int fakeId(); }

    private static Object defaultValue(Class<?> type) {
        if (!type.isPrimitive() || type == void.class) return null;
        if (type == boolean.class) return false;
        if (type == byte.class) return (byte) 0;
        if (type == short.class) return (short) 0;
        if (type == int.class) return 0;
        if (type == long.class) return 0L;
        if (type == float.class) return 0f;
        if (type == double.class) return 0d;
        if (type == char.class) return '\0';
        return null;
    }
}
