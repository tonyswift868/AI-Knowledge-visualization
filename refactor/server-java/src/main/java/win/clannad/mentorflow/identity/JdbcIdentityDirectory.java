package win.clannad.mentorflow.identity;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import javax.sql.DataSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;
import win.clannad.mentorflow.persistence.BusinessDatabase;

/** Read-only membership projection over the existing Alembic schema; owns no DDL. */
@Component
public final class JdbcIdentityDirectory implements IdentityDirectory {
    private final String issuer;
    private final DataSource dataSource;
    private final BusinessDatabase ownedBusiness;
    private final List<Map<String,Object>> services;
    private final AtomicBoolean verified=new AtomicBoolean(false);
    private final ObjectMapper json=new ObjectMapper();
    private static final String[] TABLES={"organizations","organization_memberships","workspaces","workspace_memberships","workspace_role_bindings","workspace_profiles","workspace_goals","asset_manifests","asset_revisions","account_import_ledger","structured_sync_entities","structured_sync_requests","rehydration_manifests","resource_scopes","documents","evidence","knowledge_maps","learning_plans","teacher_sessions","review_campaigns","student_events","cost_events"};
    @Autowired
    public JdbcIdentityDirectory(Environment env,IdentitySettings settings,BusinessDatabase business) {
        this(env,settings,business,false);
    }
    /** Compatibility constructor for the standalone identity Java gates. */
    public JdbcIdentityDirectory(Environment env,IdentitySettings settings) {
        this(env,settings,new BusinessDatabase(env),true);
    }
    private JdbcIdentityDirectory(Environment env,IdentitySettings settings,BusinessDatabase business,boolean ownBusiness) {
        issuer=settings.canonicalIssuer.replaceAll("/+$","");
        dataSource=business.dataSource();
        ownedBusiness=ownBusiness?business:null;
        String rawServices=env.getProperty("MENTORFLOW_SERVICE_IDENTITY_GRANTS_JSON","").strip();
        try {
            if(rawServices.getBytes(java.nio.charset.StandardCharsets.UTF_8).length>65536) throw new IllegalArgumentException("service identity grant configuration is too large");
            services=rawServices.isEmpty()?List.of():json.readValue(rawServices,new TypeReference<List<Map<String,Object>>>(){});
            if(services.size()>128) throw new IllegalArgumentException("service identity grant configuration must be a bounded list");
            var seen=new java.util.HashSet<String>();
            for (var grant:services) {
                var allowed=java.util.Set.of("issuer","subject","client_id","service_id","organization_id","workspace_id","permissions","status");
                if(!allowed.containsAll(grant.keySet()))throw new IllegalArgumentException("service identity grant configuration is invalid");
                for(String key:List.of("issuer","subject","client_id","service_id","organization_id")) {
                    int min=key.equals("issuer")?8:1;
                    int max=key.equals("issuer")?500:(key.equals("subject")||key.equals("client_id"))?300:240;
                    if(!(grant.get(key) instanceof String text)||text.length()<min||text.length()>max)throw new IllegalArgumentException("service identity grant configuration is invalid");
                }
                if(grant.get("workspace_id")!=null && (!(grant.get("workspace_id") instanceof String text)||text.length()>240))throw new IllegalArgumentException("service identity grant configuration is invalid");
                if (!issuer.equals(((String)grant.get("issuer")).replaceAll("/+$",""))) throw new IllegalArgumentException("service identity grants must use the configured issuer");
                if(!(grant.get("permissions") instanceof List<?> permissions)||permissions.isEmpty()||permissions.size()>256||new java.util.HashSet<>(permissions).size()!=permissions.size()||!java.util.Set.of("DOCUMENT_READ","DOCUMENT_PROCESS","JOB_READ","JOB_EXECUTE").containsAll(permissions))throw new IllegalArgumentException("service identity grant configuration is invalid");
                if(!java.util.Set.of("ACTIVE","SUSPENDED","REVOKED").contains(grant.get("status")))throw new IllegalArgumentException("service identity grant configuration is invalid");
                if(!seen.add(grant.get("issuer")+"\u001f"+grant.get("subject")+"\u001f"+grant.get("client_id"))) throw new IllegalArgumentException("service identity grants must be unique");
                grant.put("issuer",issuer);grant.putIfAbsent("workspace_id",null);
            }
        } catch (java.io.IOException ex) {throw new IllegalArgumentException("service identity grant configuration must be valid JSON",ex);}
    }
    private Connection connect() throws SQLException {
        if(dataSource==null) throw new SQLException("database URL is missing");
        return dataSource.getConnection();
    }
    @jakarta.annotation.PreDestroy
    public void close() {if(ownedBusiness!=null)ownedBusiness.close();}
    /** Preserve the Python application-role invariant, without creating or migrating tables. */
    private void verifyRole(Connection connection) throws SQLException {
        if(verified.get())return;
        String query="SELECT current_user,r.rolsuper,r.rolbypassrls,(SELECT count(*) FROM pg_class c JOIN pg_roles o ON o.oid=c.relowner WHERE c.relname=ANY(?) AND o.rolname='mentorflow_app'),(SELECT count(*) FROM pg_class c WHERE c.relname=ANY(?) AND c.relrowsecurity AND c.relforcerowsecurity) FROM pg_roles r WHERE r.rolname=current_user";
        try(var statement=connection.prepareStatement(query)) {
            var names=connection.createArrayOf("text",TABLES); statement.setArray(1,names);statement.setArray(2,names);
            try(var row=statement.executeQuery()) {
                if(!row.next() || !"mentorflow_app".equals(row.getString(1)) || row.getBoolean(2) || row.getBoolean(3) || row.getInt(4)!=0 || row.getInt(5)!=TABLES.length)
                    throw new SQLException("enterprise application role must be mentorflow_app, non-owner, non-superuser, NOBYPASSRLS, with FORCE RLS on every tenant table");
            } finally {names.free();}
        }
        verified.set(true);
    }
    @Override public void ensureOpen(String issuer,String subject) {
        try(var connection=connect()) {
            connection.setAutoCommit(false);verifyRole(connection);
            try(var setting=connection.createStatement()) {setting.execute("SET LOCAL lock_timeout = '3s'");setting.execute("SET LOCAL statement_timeout = '10s'");}
            try(var statement=connection.prepareStatement("SELECT account_closure.check_open(?,?)")) {
                statement.setString(1,issuer);statement.setString(2,subject);
                try(var row=statement.executeQuery()) {
                    if(!row.next())throw new SQLException("account state missing");
                    var state=json.readTree(row.getString(1));
                    if(!state.isObject())throw new IdentityFailure(503,"CLOSURE_STORAGE_INVALID","账号状态暂不可用。");
                    if(state.hasNonNull("error"))throw new IdentityFailure(503,state.get("error").asText(),"账号状态暂不可用。");
                    if(state.path("closed").isBoolean()&&state.path("closed").booleanValue())throw new IdentityFailure(403,"ACCOUNT_CLOSURE_IN_PROGRESS","账号注销处理中或身份已关闭。");
                }
            }
            connection.commit();
        } catch (IdentityFailure failure) {throw failure;}
        catch(Exception unavailable) {throw new IdentityFailure(503,"CLOSURE_STORAGE_UNAVAILABLE","账号状态暂不可用。");}
    }
    @Override public Membership resolve(String signedIssuer,String subject,String org,String workspace) {
        if(!signedIssuer.replaceAll("/+$","").equals(issuer))return null;
        try(var connection=connect()) {
            connection.setAutoCommit(false);verifyRole(connection);
            try(var context=connection.prepareStatement("SELECT set_config('mentorflow.organization_id',?,true),set_config('mentorflow.workspace_id',?,true),set_config('mentorflow.actor_subject',?,true)")) {
                context.setString(1,org);context.setString(2,workspace==null?"":workspace);context.setString(3,subject);context.execute();
            }
            var roles=new ArrayList<String>();
            try(var statement=connection.prepareStatement("SELECT m.role,m.status,o.status FROM organization_memberships m JOIN organizations o ON o.id=m.organization_id WHERE m.organization_id=? AND m.subject_id=?")) {
                statement.setString(1,org);statement.setString(2,subject);
                try(var row=statement.executeQuery()) {if(!row.next()||!active(row.getString(2))||!active(row.getString(3)))return null;addRole(roles,row.getString(1));}
            }
            if(workspace!=null) {
                try(var statement=connection.prepareStatement("SELECT m.status,w.status FROM workspace_memberships m JOIN workspaces w ON w.organization_id=m.organization_id AND w.id=m.workspace_id WHERE m.organization_id=? AND m.workspace_id=? AND m.subject_id=?")) {
                    statement.setString(1,org);statement.setString(2,workspace);statement.setString(3,subject);
                    try(var row=statement.executeQuery()){if(!row.next()||!active(row.getString(1))||!active(row.getString(2)))return null;}
                }
                try(var statement=connection.prepareStatement("SELECT role,status FROM workspace_role_bindings WHERE organization_id=? AND workspace_id=? AND subject_id=? ORDER BY role")) {
                    statement.setString(1,org);statement.setString(2,workspace);statement.setString(3,subject);
                    try(var rows=statement.executeQuery()){while(rows.next())if(active(rows.getString(2)))addRole(roles,rows.getString(1));}
                }
            }
            connection.commit();
            return roles.isEmpty()?null:new Membership("membership:"+IdentitySecurity.sha256(issuer+"\u001f"+subject+"\u001f"+org),List.copyOf(roles));
        } catch(SQLException failure) {
            IdentityFailure unavailable = new IdentityFailure(503,"CLOSURE_STORAGE_UNAVAILABLE","账号身份目录暂不可用。");
            unavailable.initCause(failure);
            throw unavailable;
        }
    }
    private static boolean active(String value){return value!=null&&value.strip().equalsIgnoreCase("ACTIVE");}
    private static void addRole(List<String> roles,String value) {
        if(value==null)return;String role=value.strip().toUpperCase(java.util.Locale.ROOT);
        if(role.equals("ORGANIZATION_OWNER"))role="OWNER";
        if(List.of("OWNER","ADMIN","CONTENT_MANAGER","INSTRUCTOR","LEARNER","AUDITOR").contains(role)&&!roles.contains(role))roles.add(role);
    }
    @Override public Map<String,Object> service(String signedIssuer,String subject,String client) {
        return services.stream().filter(grant->grant.get("issuer").equals(signedIssuer)&&grant.get("subject").equals(subject)&&grant.get("client_id").equals(client)).findFirst().orElse(null);
    }
    @Override public boolean reachable() {
        try(var connection=connect();var statement=connection.createStatement();var row=statement.executeQuery("SELECT 1")){return row.next()&&row.getInt(1)==1;}
        catch(SQLException unavailable){return false;}
    }
}
