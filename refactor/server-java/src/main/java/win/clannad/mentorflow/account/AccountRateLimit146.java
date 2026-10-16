package win.clannad.mentorflow.account;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import win.clannad.mentorflow.identity.IdentityFailure;
import win.clannad.mentorflow.identity.IdentitySecurity;

/** Same process-wide counters shared by lifecycle and reset observations in Python. */
public final class AccountRateLimit146 {
    private AccountRateLimit146() {}
    private record Attempt(int count,long end) {}
    private static final Map<String,Attempt> RATES=new HashMap<>();
    public static synchronized void check(String peer,String operation) {
        long now=System.nanoTime();
        RATES.entrySet().removeIf(row->row.getValue().end<=now);
        var keys=List.of("global",operation+":"+IdentitySecurity.sha256(peer));
        var limits=List.of(600,operation.equals("watch")?20:180);
        if(RATES.size()>4096)throw limited();
        for(int i=0;i<keys.size();i++) {
            Attempt attempt=RATES.get(keys.get(i));
            if(attempt!=null&&attempt.count>=limits.get(i))throw limited();
        }
        for(String key:keys) {
            Attempt attempt=RATES.getOrDefault(key,new Attempt(0,now+60_000_000_000L));
            RATES.put(key,new Attempt(attempt.count+1,attempt.end));
        }
    }

    /**
     * Keep closure reauthentication on the native account throttle.  The
     * shared operation counter above has different thresholds and does not
     * include the subject dimension used by Python's native-session throttle.
     */
    public static void throttleClosure(NativeSessionService sessions,String subject,String peer) {
        if(sessions==null)throw new IdentityFailure(503,"NATIVE_SESSION_UNAVAILABLE","账号会话暂不可用。");
        sessions.throttle(subject,peer);
    }
    private static IdentityFailure limited() {
        return new IdentityFailure(429,"RESET_RATE_LIMITED","请稍后重试。");
    }
}
