package win.clannad.mentorflow.closure;

import java.time.Clock;
import java.time.Duration;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.CancellationException;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Short-scale blocking and request-cancellation probes for the 75-second closure deadline. */
public final class ClosureCancellation148 {
    private static final String OP="0123456789abcdef0123456789abcdef";
    private static final String TOKEN="mfc1_"+"A".repeat(43);

    private static final class Store implements ClosureStore {
        int failed,completed,providerDone;
        private Map<String,Object> row(String state) {
            Map<String,Object> r=new HashMap<>();r.put("state",state);r.put("operation_id",OP);
            r.put("account_id","mfa1_test");r.put("subject_id","subject148");
            r.put("data_cleaned",false);r.put("identity_deleted",false);r.put("acquired",true);return r;
        }
        public String issuer(){return "https://issuer.test";}
        public void ensureOpen(String issuer,String subject){}
        public Map<String,Object> prepare(String i,String s,String a,String o,String d){return row("PREPARED");}
        public Map<String,Object> status(String o,String d){return row("IN_PROGRESS");}
        public Map<String,Object> acquire(String o,String d,String c){return row("IN_PROGRESS");}
        public Map<String,Object> clean(String o,String d,String c){Map<String,Object> r=row("IN_PROGRESS");r.put("data_cleaned",true);return r;}
        public Map<String,Object> providerDone(String o,String d,String c){providerDone++;Map<String,Object> r=row("IN_PROGRESS");r.put("identity_deleted",true);return r;}
        public Map<String,Object> complete(String o,String d,String c){completed++;return row("COMPLETED");}
        public Map<String,Object> fail(String o,String d,String c,String code){failed++;Map<String,Object> r=row("RETRYABLE_FAILURE");r.put("code",code);return r;}
    }

    private static ClosureAuthority authority() {
        return new ClosureAuthority() {
            public void verifyCurrentPassword(String i,String s,String p){}
            public Revocation revokeLocal(String s){return new Revocation(true,true);}
        };
    }

    public static void main(String[] args) throws Exception {
        String timeout=timeoutInterruptsBlockingProvider();
        String cancellation=externalCancellationLeavesLease();
        System.out.println("CLOSURE_TRACE_148={\"timeout\":"+timeout+",\"cancel\":"+cancellation+"}");
    }

    private static String timeoutInterruptsBlockingProvider() {
        Store store=new Store();
        ClosureProvider provider=new ClosureProvider() {
            public void ensureDeleted(String subject) {
                try { Thread.sleep(10_000); throw new AssertionError("deadline did not interrupt provider"); }
                catch(InterruptedException cancelled) { Thread.currentThread().interrupt(); throw new CancellationException(); }
            }
        };
        ClosureService service=new ClosureService(store,provider,authority(),Clock.systemUTC(),
            new java.security.SecureRandom(),Duration.ofMillis(80),System::nanoTime);
        long start=System.nanoTime();
        ClosureService.Result result=service.commit(OP,TOKEN);
        long elapsed=System.nanoTime()-start;
        require(result.status()==503,"timeout status");
        require("CLOSURE_RETRY_REQUIRED".equals(result.payload().get("code")),"timeout code");
        require(elapsed<TimeUnit.SECONDS.toNanos(2),"short deadline elapsed");
        require(store.failed==1&&store.providerDone==0&&store.completed==0,"timeout checkpoints");
        return "{\"status\":"+result.status()+",\"state\":\""+result.payload().get("state")
            +"\",\"code\":\""+result.payload().get("code")+"\",\"fail_calls\":"+store.failed
            +",\"provider_done\":"+store.providerDone+",\"complete_calls\":"+store.completed+"}";
    }

    private static String externalCancellationLeavesLease() throws Exception {
        Store store=new Store();CountDownLatch started=new CountDownLatch(1);
        ClosureProvider provider=new ClosureProvider() {
            public void ensureDeleted(String subject) {
                started.countDown();
                try { new CountDownLatch(1).await(); }
                catch(InterruptedException cancelled) { Thread.currentThread().interrupt(); throw new CancellationException(); }
                throw new AssertionError("cancelled provider continued");
            }
        };
        ClosureService service=new ClosureService(store,provider,authority(),Clock.systemUTC(),
            new java.security.SecureRandom(),Duration.ofSeconds(5),System::nanoTime);
        final Throwable[] outcome={null};
        Thread request=new Thread(()->{try{service.commit(OP,TOKEN);}catch(Throwable failure){outcome[0]=failure;}});
        request.start();require(started.await(2,TimeUnit.SECONDS),"provider entered");request.interrupt();request.join(2_000);
        require(!request.isAlive(),"cancelled request returned");
        require(outcome[0] instanceof CancellationException,"cancellation propagated");
        require(store.failed==0&&store.providerDone==0&&store.completed==0,"cancel kept lease and stopped provider");
        return "{\"cancelled\":"+(outcome[0] instanceof CancellationException)
            +",\"fail_calls\":"+store.failed+",\"provider_done\":"+store.providerDone
            +",\"complete_calls\":"+store.completed+"}";
    }

    private static void require(boolean ok,String message){if(!ok)throw new AssertionError(message);}
}
