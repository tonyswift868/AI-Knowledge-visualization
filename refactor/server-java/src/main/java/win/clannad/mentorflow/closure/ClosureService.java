package win.clannad.mentorflow.closure;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.Clock;
import java.time.Duration;
import java.util.Base64;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.regex.Pattern;
import java.util.concurrent.CancellationException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledThreadPoolExecutor;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.LongSupplier;

/** Request-driven account closure coordinator; no worker or background deletion. */
public final class ClosureService {
    public record Result(int status, Map<String,Object> payload) {}
    private static final Pattern TOKEN = Pattern.compile("mfc1_[A-Za-z0-9_-]{43}");
    private static final Pattern OPERATION = Pattern.compile("[0-9a-f]{32}");
    private static final Pattern SUBJECT = Pattern.compile("[A-Za-z0-9_-]{1,128}");
    private static final ScheduledExecutorService DEADLINES=deadlineExecutor();
    private static final Map<String,String> MESSAGES = Map.of(
        "PREPARED", "注销已准备，账号尚未删除。", "IN_PROGRESS", "账号注销正在处理。",
        "RETRYABLE_FAILURE", "注销尚未完成，请使用原恢复凭证重试。", "COMPLETED", "账号注销已完成。");
    private final ClosureStore store;
    private final ClosureProvider provider;
    private final ClosureAuthority authority;
    private final SecureRandom random;
    private final long commitTimeoutNanos;
    private final LongSupplier monotonicNanos;

    private static ScheduledExecutorService deadlineExecutor() {
        ScheduledThreadPoolExecutor executor=new ScheduledThreadPoolExecutor(1,task->{
            Thread thread=new Thread(task,"mentorflow-closure-deadline");thread.setDaemon(true);return thread;
        });
        executor.setRemoveOnCancelPolicy(true);
        return executor;
    }

    public ClosureService(ClosureStore store, ClosureProvider provider, ClosureAuthority authority, Clock clock) {
        this(store,provider,authority,clock,new SecureRandom(),Duration.ofSeconds(75),System::nanoTime);
    }
    ClosureService(ClosureStore store, ClosureProvider provider, ClosureAuthority authority, Clock clock, SecureRandom random) {
        this(store,provider,authority,clock,random,Duration.ofSeconds(75),System::nanoTime);
    }
    ClosureService(ClosureStore store, ClosureProvider provider, ClosureAuthority authority, Clock clock,
            SecureRandom random,Duration commitTimeout,LongSupplier monotonicNanos) {
        this.store=store; this.provider=provider; this.authority=authority;
        this.random=random;
        if(commitTimeout==null||commitTimeout.isZero()||commitTimeout.isNegative())throw new IllegalArgumentException("positive commit timeout required");
        this.commitTimeoutNanos=commitTimeout.toNanos();
        this.monotonicNanos=monotonicNanos;
    }

    public Result prepare(String issuer, String subject, String actorKind, String password) {
        return prepare(issuer,subject,actorKind,password,"unknown");
    }
    public Result prepare(String issuer, String subject, String actorKind, String password, String peer) {
        if (password==null || password.isEmpty() || password.codePointCount(0,password.length())>1024)
            throw new ClosureFailure("CLOSURE_INPUT_INVALID",400);
        authority.throttle(subject,peer==null||peer.isBlank()?"unknown":peer);
        if (!authority.active()) throw new ClosureFailure("CLOSURE_UNAVAILABLE");
        if (!store.issuer().equals(issuer) || !"HUMAN".equals(actorKind) || subject==null || !SUBJECT.matcher(subject).matches())
            throw new ClosureFailure("CLOSURE_IDENTITY_INVALID",403);
        authority.verifyCurrentPassword(issuer,subject,password);
        byte[] opBytes=new byte[16], tokenBytes=new byte[32]; random.nextBytes(opBytes); random.nextBytes(tokenBytes);
        String operation=HexFormat.of().formatHex(opBytes);
        String token="mfc1_"+Base64.getUrlEncoder().withoutPadding().encodeToString(tokenBytes);
        String digest=tokenDigest(operation,token);
        Map<String,Object> record=store.prepare(issuer,subject,accountId(issuer,subject),operation,digest);
        LinkedHashMap<String,Object> payload=new LinkedHashMap<>(); payload.put("state","PREPARED");
        payload.put("operation_id",record.get("operation_id")); payload.put("account_id",record.get("account_id")); payload.put("recovery_token",token);
        java.util.Arrays.fill(tokenBytes,(byte)0); java.util.Arrays.fill(opBytes,(byte)0);
        return new Result(200,payload);
    }

    public Result status(String operation, String token) {
        Map<String,Object> record=store.status(operation,tokenDigest(operation,token));
        return new Result(200,publicResult(record));
    }

    public Result commit(String operation, String token) {
        String digest=tokenDigest(operation,token), claim=randomHex(16);
        Map<String,Object> record=store.acquire(operation,digest,claim);
        if (!Boolean.TRUE.equals(record.get("acquired"))) {
            return new Result("COMPLETED".equals(record.get("state"))?200:202,publicResult(record));
        }
        Thread requestThread=Thread.currentThread();
        AtomicBoolean timedOut=new AtomicBoolean();
        Object timerGate=new Object();
        boolean[] timerClosed={false};
        ScheduledFuture<?> interrupt=DEADLINES.schedule(()->{
            synchronized(timerGate) {
                if(timerClosed[0])return;
                timedOut.set(true);
                requestThread.interrupt();
            }
        },commitTimeoutNanos,TimeUnit.NANOSECONDS);
        try {
            long deadline=monotonicNanos.getAsLong()+commitTimeoutNanos;
            String subject=requiredString(record,"subject_id");
            ensureBefore(deadline,monotonicNanos);
            if (!authority.revokeLocal(subject).complete()) throw new ClosureFailure("CLOSURE_SESSION_REVOCATION_PENDING");
            ensureBefore(deadline,monotonicNanos);
            if ((provider==null||!provider.active()) && !Boolean.TRUE.equals(record.get("identity_deleted"))) throw new ClosureFailure("CLOSURE_PROVIDER_UNAVAILABLE");
            if (!Boolean.TRUE.equals(record.get("data_cleaned"))) {
                ensureBefore(deadline,monotonicNanos); record=store.clean(operation,digest,claim); ensureBefore(deadline,monotonicNanos);
            }
            if (!Boolean.TRUE.equals(record.get("identity_deleted"))) {
                ensureBefore(deadline,monotonicNanos); provider.ensureDeleted(subject); ensureBefore(deadline,monotonicNanos);
                record=store.providerDone(operation,digest,claim); ensureBefore(deadline,monotonicNanos);
            }
            ensureBefore(deadline,monotonicNanos);
            if (!authority.revokeLocal(subject).complete()) throw new ClosureFailure("CLOSURE_SESSION_REVOCATION_PENDING");
            ensureBefore(deadline,monotonicNanos);
            ensureBefore(deadline,monotonicNanos);
            record=store.complete(operation,digest,claim);
            ensureBefore(deadline,monotonicNanos);
            Result completed=new Result(200,publicResult(record));
            synchronized(timerGate) {
                if(timedOut.get())throw new ClosureFailure("CLOSURE_RETRY_REQUIRED");
                timerClosed[0]=true;
                interrupt.cancel(false);
                return completed;
            }
        } catch (CancellationException cancelled) {
            // Match asyncio.CancelledError: leave the operation lease untouched
            // for request cancellation; timeout is a retryable operation result.
            if(timedOut.get()) {
                Thread.interrupted();
                return failure(operation,digest,claim,record,"CLOSURE_RETRY_REQUIRED");
            }
            throw cancelled;
        } catch (Exception e) {
            if(timedOut.get()) {
                Thread.interrupted();
                return failure(operation,digest,claim,record,"CLOSURE_RETRY_REQUIRED");
            }
            String code=e instanceof ClosureFailure failure?failure.code:"CLOSURE_RETRY_REQUIRED";
            return failure(operation,digest,claim,record,code);
        } finally {
            synchronized(timerGate) {
                timerClosed[0]=true;
                interrupt.cancel(false);
                if(timedOut.get())Thread.interrupted();
            }
        }
    }

    private static void ensureBefore(long deadline,LongSupplier monotonicNanos) {
        if(Thread.currentThread().isInterrupted())throw new CancellationException("closure request cancelled");
        if (monotonicNanos.getAsLong() >= deadline) throw new ClosureFailure("CLOSURE_RETRY_REQUIRED");
    }

    private Result failure(String operation,String digest,String claim,Map<String,Object> fallback,String code) {
        try { fallback=store.fail(operation,digest,claim,code); }
        catch (Exception ignored) {
            LinkedHashMap<String,Object> replacement=new LinkedHashMap<>(fallback);
            replacement.put("state","RETRYABLE_FAILURE"); replacement.put("code","CLOSURE_STORAGE_UNAVAILABLE"); fallback=replacement;
        }
        String state=String.valueOf(fallback.get("state"));
        return new Result("COMPLETED".equals(state)?200:"IN_PROGRESS".equals(state)?202:503,publicResult(fallback));
    }

    private static Map<String,Object> publicResult(Map<String,Object> record) {
        String state=String.valueOf(record.get("state")); String message=MESSAGES.get(state);
        if(message==null)throw new ClosureFailure("CLOSURE_STORAGE_INVALID");
        LinkedHashMap<String,Object> result=new LinkedHashMap<>(); result.put("state",state);
        result.put("operation_id",record.get("operation_id")); result.put("account_id",record.get("account_id"));
        Object code=record.get("code"); result.put("code",code==null||String.valueOf(code).isEmpty()?"CLOSURE_"+state:code);
        result.put("message",message); return result;
    }

    public static String tokenDigest(String operation,String token) {
        if(operation==null||!OPERATION.matcher(operation).matches()||token==null||!TOKEN.matcher(token).matches())
            throw new ClosureFailure("CLOSURE_CREDENTIAL_INVALID",403);
        return sha256("mentorflow.closure.v1\0"+operation+"\0"+token);
    }
    private static String accountId(String issuer,String subject) {
        if(issuer==null||issuer.isBlank()||!issuer.equals(issuer.strip())||subject==null||subject.isEmpty()||!subject.equals(subject.strip()))
            throw new IllegalArgumentException("canonical account anchor required");
        return "mfa1_"+sha256("mentorflow.account.v1:"+sha256(issuer)+":"+sha256(subject));
    }
    private static String sha256(String value) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))); }
        catch(Exception e){throw new IllegalStateException(e);}
    }
    private String randomHex(int bytes) { byte[] value=new byte[bytes]; random.nextBytes(value); String result=HexFormat.of().formatHex(value); java.util.Arrays.fill(value,(byte)0); return result; }
    private static String requiredString(Map<String,Object> record,String key) { Object v=record.get(key); if(!(v instanceof String s)||s.isEmpty()) throw new ClosureFailure("CLOSURE_STORAGE_INVALID"); return s; }
}
