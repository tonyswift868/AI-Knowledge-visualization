package rag154;

import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.core.env.StandardEnvironment;
import win.clannad.mentorflow.identity.IdentitySecurity;
import win.clannad.mentorflow.persistence.BusinessDatabase;
import win.clannad.mentorflow.rag.*;

/** Run against an explicitly disposable migrated pgvector database, never an online account. */
public final class RagPgvectorVerify154 {
    public static void main(String[] args) {
        try (BusinessDatabase business = new BusinessDatabase(new StandardEnvironment())) {
        RagRepository repository=new RagRepository(business);
        RagService service=new RagService(repository,new RagEmbedding(),new PgVectorStore());
        String tag="verify154-"+UUID.randomUUID();
        Map<String,Object> alice=Map.of("actor_kind","HUMAN","issuer","https://rag154.invalid","subject_id",tag+"-alice");
        Map<String,Object> bob=Map.of("actor_kind","HUMAN","issuer","https://rag154.invalid","subject_id",tag+"-bob");
        String scope=IdentitySecurity.sha256("https://rag154.invalid\u0000"+tag+"-alice");
        repository.transaction(scope,false,jdbc -> {
            check("vector(512)".equals(jdbc.queryForObject("SELECT format_type(atttypid,atttypmod) FROM pg_attribute WHERE attrelid='rag_chunks'::regclass AND attname='embedding'",String.class)),"vector(512) column");
            check(jdbc.queryForObject("SELECT count(*) FROM pg_indexes WHERE tablename='rag_chunks' AND indexdef LIKE '%USING hnsw%' AND indexdef LIKE '%vector_cosine_ops%'",Integer.class)>0,"HNSW cosine index");
            System.out.println("pgvector="+jdbc.queryForObject("SELECT extversion FROM pg_extension WHERE extname='vector'",String.class));
            return null;
        });
        Map<String,Object> batch=Map.of("title","事务测试","source","https://rag154.invalid/source","total_chunks",2,
            "chunks",List.of(Map.of("seq",0,"text","数据库事务的原子性意味着多个操作作为一个整体执行，要么全部成功，要么全部回滚。","page",1,"section","原子性"),
                             Map.of("seq",1,"text","数据库索引能加速查询，同时会增加写入维护成本和存储空间。","page",2,"section","索引")));
        try {
            check("ready".equals(service.upload(alice,tag+"-a",batch).get("status")),"upload ready");
            check(service.upload(alice,tag+"-a",batch).get("document_id").equals(tag+"-a"),"idempotent upload");
            check(Boolean.TRUE.equals(service.upload(alice,tag+"-alias",batch).get("deduplicated")),"content deduplication");
            check(service.documents(alice).size()==1,"one canonical document");
            check(service.documents(bob).isEmpty(),"cross-account list isolation");
            Map<String,Object> query=Map.of("document_ids",List.of(tag+"-alias"),"query","事务的原子性是什么？","k",5);
            @SuppressWarnings("unchecked") List<Map<String,Object>> passages=(List<Map<String,Object>>)service.search(alice,query).get("passages");
            check(passages.size()==2 && ((Number)passages.get(0).get("seq")).intValue()==0,"filtered cosine and keyword ranking");
            check(((List<?>)service.search(bob,query).get("passages")).isEmpty(),"cross-account search isolation");
            check(((List<?>)service.search(alice,Map.of("document_ids",List.of("absent"),"query","事务","k",5)).get("passages")).isEmpty(),"document filtering");
            service.delete(alice,tag+"-a");
            check(service.documents(alice).size()==1,"alias survives canonical deletion");
            service.delete(alice,tag+"-alias");
            check(service.documents(alice).isEmpty(),"final logical document deletion");
            System.out.println("RAG_PGVECTOR154_OK");
        } finally {
            for(String id:List.of(tag+"-alias",tag+"-a")) service.delete(alice,id);
        }
        }
    }
    private static void check(boolean passed,String label) {
        if(!passed) throw new IllegalStateException("FAIL: "+label);
        System.out.println("PASS: "+label);
    }
}
