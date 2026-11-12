package win.clannad.mentorflow.account;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import javax.sql.DataSource;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Component;
import win.clannad.mentorflow.identity.IdentityFailure;
import win.clannad.mentorflow.identity.IdentitySecurity;
import win.clannad.mentorflow.identity.IdentitySettings;

/** Java projection of Python AccountWorkspaceBootstrap; it uses only existing Alembic tables. */
@Component
public final class WorkspaceBootstrap146 {
    private static final String SCOPE_VERSION="SUPPORTED_REHYDRATION_SCOPE_V1";
    private static final String TITLE="我的学习空间";
    private final ObjectProvider<DataSource> dataSources;
    private final String issuer;

    public WorkspaceBootstrap146(ObjectProvider<DataSource> dataSources,IdentitySettings settings) {
        this.dataSources=dataSources;this.issuer=settings.canonicalIssuer.replaceAll("/+$","");
    }

    public Map<String,Object> discover(String signedIssuer,String subject) {
        validate(signedIssuer,subject);
        DataSource source=requireDataSource();
        try(Connection connection=source.getConnection()) {
            connection.setAutoCommit(false);
            try {
                try(var statement=connection.createStatement()){statement.execute("SET TRANSACTION READ ONLY");}
                setContext(connection,signedIssuer,subject,"","");
                List<Map<String,Object>> rows=memberships(connection,signedIssuer,subject);
                Map<String,Object> result=result(connection,signedIssuer,subject,rows);
                connection.commit();return result;
            } catch(Exception failure){rollback(connection);throw failure;}
        } catch(IdentityFailure failure){throw failure;}
        catch(Exception failure){throw unavailable(failure);}
    }

    public Map<String,Object> initialize(String signedIssuer,String subject) {
        validate(signedIssuer,subject);DataSource source=requireDataSource();
        try(Connection connection=source.getConnection()) {
            connection.setAutoCommit(false);
            try {
                setContext(connection,signedIssuer,subject,"","");
                advisoryLock(connection,sha256(signedIssuer+"\0"+subject));
                List<Map<String,Object>> rows=memberships(connection,signedIssuer,subject);
                if(rows.isEmpty()) {
                    String digest=sha256(subjectScope(signedIssuer,subject));
                    String organization="personal_org_"+digest,workspace="personal_ws_"+digest;
                    setContext(connection,signedIssuer,subject,organization,workspace);
                    try(PreparedStatement query=connection.prepareStatement("SELECT id FROM organizations WHERE id=?")) {
                        query.setString(1,organization);
                        try(ResultSet found=query.executeQuery()) {if(found.next())throw new IdentityFailure(403,"ACCOUNT_PERSONAL_WORKSPACE_UNAVAILABLE","Existing personal membership is unavailable; access cannot be restored by initialization");}
                    }
                    try(PreparedStatement insert=connection.prepareStatement("INSERT INTO organizations(id,slug,name,status) VALUES (?,?,?,'active')")) {
                        insert.setString(1,organization);insert.setString(2,organization);insert.setString(3,TITLE);insert.executeUpdate();
                    }
                    try(PreparedStatement insert=connection.prepareStatement("INSERT INTO organization_memberships(organization_id,subject_id,role,status) VALUES (?,?,'LEARNER','active')")) {
                        insert.setString(1,organization);insert.setString(2,subject);insert.executeUpdate();
                    }
                    try(PreparedStatement insert=connection.prepareStatement("INSERT INTO workspaces(id,organization_id,slug,name,status) VALUES (?,?,?,?,'active')")) {
                        insert.setString(1,workspace);insert.setString(2,organization);insert.setString(3,workspace);insert.setString(4,TITLE);insert.executeUpdate();
                    }
                    try(PreparedStatement insert=connection.prepareStatement("INSERT INTO workspace_memberships(organization_id,workspace_id,subject_id,status) VALUES (?,?,?,'active')")) {
                        insert.setString(1,organization);insert.setString(2,workspace);insert.setString(3,subject);insert.executeUpdate();
                    }
                    try(PreparedStatement insert=connection.prepareStatement("INSERT INTO workspace_role_bindings(organization_id,workspace_id,subject_id,role,status) VALUES (?,?,?,'LEARNER','active')")) {
                        insert.setString(1,organization);insert.setString(2,workspace);insert.setString(3,subject);insert.executeUpdate();
                    }
                    rows=memberships(connection,signedIssuer,subject);
                    if(rows.isEmpty())throw new IllegalStateException("Initialized workspace is not membership-visible");
                }
                for(Map<String,Object> row:rows) {
                    String organization=(String)row.get("organization_id"),workspace=(String)row.get("id");
                    setContext(connection,signedIssuer,subject,organization,workspace);
                    try(PreparedStatement insert=connection.prepareStatement("INSERT INTO workspace_profiles(workspace_id,organization_id) VALUES (?,?) ON CONFLICT (workspace_id) DO NOTHING")) {
                        insert.setString(1,workspace);insert.setString(2,organization);insert.executeUpdate();
                    }
                    try(var statement=connection.createStatement()){statement.execute("SET LOCAL synchronous_commit = on");}
                    initializeLearningState(connection,signedIssuer,subject,organization,workspace);
                }
                Map<String,Object> result=result(connection,signedIssuer,subject,rows);
                connection.commit();return result;
            } catch(Exception failure){rollback(connection);throw failure;}
        } catch(IdentityFailure failure){throw failure;}
        catch(Exception failure){throw unavailable(failure);}
    }

    private List<Map<String,Object>> memberships(Connection connection,String signedIssuer,String subject)throws Exception {
        setContext(connection,signedIssuer,subject,"","");
        String sql="SELECT w.id,w.organization_id,w.name,w.status,w.created_at FROM workspace_memberships wm "+
            "JOIN organization_memberships om ON om.organization_id=wm.organization_id AND om.subject_id=wm.subject_id "+
            "JOIN organizations o ON o.id=om.organization_id JOIN workspaces w ON w.id=wm.workspace_id AND w.organization_id=wm.organization_id "+
            "WHERE wm.subject_id=? AND wm.status='active' AND om.status='active' AND o.status='active' AND w.status='active' "+
            "AND upper(om.role) IN ('OWNER','ORGANIZATION_OWNER','ADMIN','CONTENT_MANAGER','INSTRUCTOR','LEARNER','AUDITOR') ORDER BY w.created_at,w.id";
        var result=new ArrayList<Map<String,Object>>();
        try(PreparedStatement query=connection.prepareStatement(sql)) {
            query.setString(1,subject);
            try(ResultSet rows=query.executeQuery()) {while(rows.next()) {
                var row=new LinkedHashMap<String,Object>();row.put("id",rows.getString(1));row.put("organization_id",rows.getString(2));
                row.put("name",rows.getString(3));row.put("status",rows.getString(4));row.put("created_at",rows.getObject(5));result.add(row);
            }}
        }
        return result;
    }

    private Map<String,Object> result(Connection connection,String signedIssuer,String subject,List<Map<String,Object>> rows)throws Exception {
        var workspaces=new ArrayList<Map<String,Object>>();boolean learningRequired=false;
        for(Map<String,Object> row:rows) {
            String organization=(String)row.get("organization_id"),workspace=(String)row.get("id");
            setContext(connection,signedIssuer,subject,organization,workspace);
            boolean profile;
            try(PreparedStatement query=connection.prepareStatement("SELECT workspace_id FROM workspace_profiles WHERE workspace_id=? AND organization_id=?")) {
                query.setString(1,workspace);query.setString(2,organization);
                try(ResultSet found=query.executeQuery()){profile=found.next();}
            }
            learningRequired|=!learningStateInitialized(connection,signedIssuer,subject,organization,workspace);
            var value=new LinkedHashMap<String,Object>();value.put("id",workspace);value.put("organization_id",organization);
            value.put("title",row.get("name"));value.put("status",row.get("status"));value.put("initialization_status",profile?"READY":"PROFILE_REQUIRED");
            workspaces.add(value);
        }
        boolean anyProfileRequired=workspaces.stream().anyMatch(item->"PROFILE_REQUIRED".equals(item.get("initialization_status")));
        var result=new LinkedHashMap<String,Object>();result.put("workspaces",workspaces);
        result.put("default_workspace_id",workspaces.isEmpty()?null:workspaces.get(0).get("id"));
        result.put("initialization_required",learningRequired||workspaces.isEmpty()||anyProfileRequired);return result;
    }

    private static void setContext(Connection connection,String issuer,String subject,String organization,String workspace)throws Exception {
        try(PreparedStatement statement=connection.prepareStatement("SELECT set_config('mentorflow.actor_issuer',?,true),set_config('mentorflow.actor_subject',?,true),set_config('mentorflow.organization_id',?,true),set_config('mentorflow.workspace_id',?,true)")) {
            statement.setString(1,issuer);statement.setString(2,subject);statement.setString(3,organization);statement.setString(4,workspace);statement.execute();
        }
    }
    private static void initializeLearningState(Connection connection,String issuer,String subject,String organization,String workspace)throws Exception {
        Map<String,String> scope=Map.of("issuer",issuer,"subject_id",subject,"organization_id",organization,"workspace_id",workspace);
        advisoryLock(connection,sha256(jsonSorted(scope)));
        try(PreparedStatement upsert=connection.prepareStatement("INSERT INTO rehydration_manifests(issuer,subject_id,organization_id,workspace_id,scope_version,initialized,generation) VALUES (?,?,?,?,?,true,1) ON CONFLICT (issuer,subject_id,organization_id,workspace_id) DO UPDATE SET initialized=true,generation=rehydration_manifests.generation+1,updated_at=CURRENT_TIMESTAMP WHERE NOT rehydration_manifests.initialized AND rehydration_manifests.scope_version=?")) {
            upsert.setString(1,issuer);upsert.setString(2,subject);upsert.setString(3,organization);upsert.setString(4,workspace);upsert.setString(5,SCOPE_VERSION);upsert.setString(6,SCOPE_VERSION);upsert.executeUpdate();
        }
    }
    private static boolean learningStateInitialized(Connection connection,String issuer,String subject,String organization,String workspace)throws Exception {
        try(PreparedStatement query=connection.prepareStatement("SELECT initialized FROM rehydration_manifests WHERE issuer=? AND subject_id=? AND organization_id=? AND workspace_id=? AND scope_version=?")) {
            query.setString(1,issuer);query.setString(2,subject);query.setString(3,organization);query.setString(4,workspace);query.setString(5,SCOPE_VERSION);
            try(ResultSet row=query.executeQuery()){return row.next()&&row.getBoolean(1);}
        }
    }
    private static String subjectScope(String issuer,String subject){return issuer.codePointCount(0,issuer.length())+":"+issuer+subject;}
    private static String jsonSorted(Map<String,String> scope) {
        return "{\"issuer\": "+quote(scope.get("issuer"))+", \"organization_id\": "+quote(scope.get("organization_id"))+
            ", \"subject_id\": "+quote(scope.get("subject_id"))+", \"workspace_id\": "+quote(scope.get("workspace_id"))+"}";
    }
    private static String quote(String value){
        StringBuilder out=new StringBuilder("\"");
        for(int offset=0;offset<value.length();) {
            int code=value.codePointAt(offset);offset+=Character.charCount(code);
            switch(code) {
                case '"' -> out.append("\\\"");case '\\' -> out.append("\\\\");case '\b' -> out.append("\\b");
                case '\f' -> out.append("\\f");case '\n' -> out.append("\\n");case '\r' -> out.append("\\r");case '\t' -> out.append("\\t");
                default -> {
                    if(code<0x20||code>0x7e) {
                        if(code<=0xffff)out.append(String.format("\\u%04x",code));
                        else {int valuePoint=code-0x10000;out.append(String.format("\\u%04x\\u%04x",0xd800+(valuePoint>>10),0xdc00+(valuePoint&0x3ff)));}
                    } else out.appendCodePoint(code);
                }
            }
        }
        return out.append('"').toString();
    }
    private static void advisoryLock(Connection connection,String digest)throws Exception {
        long key=Long.parseUnsignedLong(digest.substring(0,16),16);
        try(PreparedStatement statement=connection.prepareStatement("SELECT pg_advisory_xact_lock(?)")){statement.setLong(1,key);statement.execute();}
    }
    private static String sha256(String value) {
        try{return java.util.HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));}
        catch(Exception failure){throw new IllegalStateException(failure);}
    }
    private void validate(String signedIssuer,String subject) {
        if(!issuer.equals(signedIssuer==null?"":signedIssuer.replaceAll("/+$",""))||subject==null||subject.isEmpty()||subject.length()>128||!subject.equals(subject.strip()))
            throw new IdentityFailure(403,"ACCOUNT_BOOTSTRAP_IDENTITY_INVALID","A verified human account is required");
    }
    private DataSource requireDataSource() {
        DataSource source=dataSources.getIfAvailable();if(source==null)throw new IdentityFailure(503,"ENTERPRISE_PERSISTENCE_NOT_READY","Account workspace persistence is not ready");return source;
    }
    private static IdentityFailure unavailable(){return new IdentityFailure(503,"ENTERPRISE_PERSISTENCE_UNAVAILABLE","Account workspace persistence is not available");}
    private static IdentityFailure unavailable(Throwable cause){return new IdentityFailure("ENTERPRISE_PERSISTENCE_UNAVAILABLE","Account workspace persistence is not available",503,cause);}
    private static void rollback(Connection connection){try{connection.rollback();}catch(Exception ignored){}}
}
