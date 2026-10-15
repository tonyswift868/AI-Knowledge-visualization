package win.clannad.mentorflow.sync;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/** Python-compatible payload validation and projection for structured sync.
 * Static allowlists come from product_sync_contract.py, product_sync_extensions.py,
 * sync_registry.py and structured_sync.py at the synchronized Python baseline.
 */
public final class SyncPayload {
    private static final Map<String,Object> DEFINITIONS = loadDefinitions();
    private static final Map<String,Object> REFACTOR_KIND = map(DEFINITIONS.get("refactor_kind_by_record"));
    private static final Map<String,Object> REFACTOR_FIELDS = map(DEFINITIONS.get("refactor_fields"));
    private static final Map<String,Object> FIELD_VARIANTS = map(DEFINITIONS.get("refactor_field_variants"));
    private static final Map<String,Object> PRODUCT_RECORDS = map(DEFINITIONS.get("product_records"));
    private static final Set<String> SYNC_KINDS = Set.copyOf(strings(DEFINITIONS.get("sync_kinds")));
    private static final Set<String> SECRET_KEYS = Set.copyOf(strings(DEFINITIONS.get("secret_keys")));
    private static final Set<String> LARGE_TYPES = Set.copyOf(strings(DEFINITIONS.get("large_aggregate_types")));
    private static final List<String> PRIVATE_SUBSTRINGS = strings(DEFINITIONS.get("product_private_substrings"));
    private static final Pattern ID = Pattern.compile((String) DEFINITIONS.get("id_pattern"));
    private static final Pattern COURSE_SCHEMA = Pattern.compile("mentorflow\\.course\\.([a-z_]{1,40})\\.v[0-9]{1,3}");
    private static final int LIMIT = 256 * 1024;

    private SyncPayload() {}

    /**
     * Validate and canonicalize a request payload exactly as StructuredSyncService._payload.
     * `input` is the HTTP body's payload object. Delete operations intentionally bypass
     * payload validation and store an empty JSON object.
     */
    public static Map<String,Object> project(String kind, String entityId,
                                              Map<String,Object> input, boolean deleting) {
        if (kind == null || !SYNC_KINDS.contains(kind) || entityId == null || !ID.matcher(entityId).matches()) {
            throw new SyncFailure("SYNC_ENTITY_INVALID", 422);
        }
        if (deleting) return new LinkedHashMap<>();
        if (input == null) throw fail("SYNC_PAYLOAD_INVALID");

        Map<String,Object> refactorPayload = null;
        boolean courseRecord = "mentorflow.refactor-sync.v1".equals(input.get("schema"))
            && "course_record".equals(input.get("record_type"));
        if (courseRecord) {
            refactorPayload = refactorSyncPayload(kind, entityId, input);
            rejectSecret(input);
        } else {
            rejectSecret(input);
            refactorPayload = refactorSyncPayload(kind, entityId, input);
        }
        if (refactorPayload != null) return refactorPayload;

        validateProduct(kind, entityId, input);
        Map<String,Object> payload = new LinkedHashMap<>(input);
        if ("progress".equals(kind) && payload.get("plan_id") == null && !pythonTruthy(payload.get("product_record"))) {
            throw fail("SYNC_PAYLOAD_INVALID");
        }
        switch (kind) {
            case "plan" -> projectPlan(payload);
            case "progress" -> projectProgress(payload);
            case "resource" -> projectResource(payload);
            default -> { /* SyncItemAdapter.project is a shallow dict copy. */ }
        }
        if (payload.get("product_record") == null) payload.remove("product_record");
        if (payload.get("refactor_record_id") == null) payload.remove("refactor_record_id");
        if ("resource".equals(kind) && payload.get("text") == null) payload.remove("text");
        // Python's pre-contract fingerprints omit the defaulted optional field.
        if ("resource".equals(kind) && refactorPayload == null
                && !input.containsKey("product_classification")) payload.remove("product_classification");
        if ("plan".equals(kind) && refactorPayload == null) {
            List<?> ids = (List<?>) payload.get("resource_ids");
            if (ids.stream().distinct().count() != ids.size()
                    || ids.stream().anyMatch(v -> !(v instanceof String s) || !ID.matcher(s).matches())) {
                throw fail("SYNC_RESOURCE_IDS_INVALID");
            }
        }
        return payload;
    }

    private static Map<String,Object> refactorSyncPayload(String kind, String entityId, Map<String,Object> payload) {
        if (!"mentorflow.refactor-sync.v1".equals(payload.get("schema"))) return null;
        if (!payload.keySet().equals(Set.of("schema", "version", "record_type", "record_id", "values")))
            throw fail("SYNC_PAYLOAD_INVALID");
        if (!isExactInteger(payload.get("version")) || compareNumber(payload.get("version"),1)!=0)
            throw fail("SYNC_PAYLOAD_INVALID");
        Object typeValue = payload.get("record_type");
        if (!(typeValue instanceof String recordType) || !REFACTOR_KIND.containsKey(recordType)
                || !kind.equals(REFACTOR_KIND.get(recordType))) throw fail("SYNC_PAYLOAD_INVALID");
        Object idValue = payload.get("record_id");
        if (!(idValue instanceof String recordId) || recordId.isEmpty() || codepoints(recordId) > 512
                || recordId.chars().anyMatch(c -> c < 32)) throw fail("SYNC_PAYLOAD_INVALID");
        if (!entityId.equals(refactorEntityId(recordType, recordId))) throw fail("SYNC_ENTITY_INVALID");
        if (!(payload.get("values") instanceof Map<?,?> rawValues)) throw fail("SYNC_PAYLOAD_INVALID");
        Map<String,Object> values = stringMap(rawValues);
        if ("course_record".equals(recordType)) validateCourseRecord(values);

        if (!"course_record".equals(recordType) && isBlobManifest(values)) {
            validateBlobManifest(recordType, entityId, values);
            LinkedHashMap<String,Object> result = new LinkedHashMap<>();
            result.put("schema", payload.get("schema")); result.put("version", payload.get("version"));
            result.put("record_type", recordType); result.put("record_id", values.get("record_id"));
            result.put("values", values);
            return result;
        }

        Set<String> allowed = set(REFACTOR_FIELDS.get(recordType));
        if ("resource".equals(recordType)) {
            Set<String> extended = new java.util.HashSet<>(allowed); extended.add("text");
            if (!values.keySet().equals(allowed) && !values.keySet().equals(extended)) throw fail("SYNC_PAYLOAD_INVALID");
        } else if (FIELD_VARIANTS.containsKey(recordType)) {
            boolean accepted = false;
            for (Object variant : list(FIELD_VARIANTS.get(recordType))) if (values.keySet().equals(set(variant))) accepted = true;
            if (!accepted) throw fail("SYNC_PAYLOAD_INVALID");
            if ("note".equals(recordType) && values.keySet().equals(Set.of("plan_key", "task_id", "text"))) {
                values = new LinkedHashMap<>(values); values.put("revision", 1);
            }
        } else if (!values.keySet().equals(allowed)) throw fail("SYNC_PAYLOAD_INVALID");

        if ("resource".equals(recordType)) return projectRefactorResource(entityId, payload, values);
        if ("draft_plan".equals(recordType)) validateDraftPlan(values);
        if (!"course_record".equals(recordType)) {
            int bytes = SyncJson.dump(payload, true).getBytes(StandardCharsets.UTF_8).length;
            if (bytes >= LIMIT) throw fail("SYNC_PAYLOAD_TOO_LARGE");
        }
        LinkedHashMap<String,Object> result = new LinkedHashMap<>();
        result.put("schema", payload.get("schema")); result.put("version", payload.get("version"));
        result.put("record_type", recordType); result.put("record_id", recordId); result.put("values", values);
        return result;
    }

    private static void validateCourseRecord(Map<String,Object> values) {
        if (!values.keySet().equals(Set.of("schema", "plan_key", "entity", "entity_id", "payload")))
            throw fail("SYNC_PAYLOAD_INVALID");
        Object schemaValue = values.get("schema");
        var matcher = schemaValue instanceof String s ? COURSE_SCHEMA.matcher(s) : null;
        if (matcher == null || !matcher.matches() || !matcher.group(1).equals(values.get("entity"))
                || !(values.get("entity_id") instanceof String eid) || !ID.matcher(eid).matches()
                || !(values.get("plan_key") instanceof String planKey)
                || !(planKey.isEmpty() || ID.matcher(planKey).matches())
                || !(values.get("payload") instanceof Map<?,?> rawPayload)) throw fail("SYNC_PAYLOAD_INVALID");
        Map<String,Object> coursePayload = stringMap(rawPayload);
        checkJsonTree(coursePayload);
        int size = SyncJson.dump(coursePayload, true).getBytes(StandardCharsets.UTF_8).length;
        if (size > LIMIT) throw fail("SYNC_PAYLOAD_TOO_LARGE");
    }

    private static void checkJsonTree(Object root) {
        record Node(Object value, int depth) {}
        var pending = new java.util.ArrayDeque<Node>();
        pending.push(new Node(root, 1));
        while (!pending.isEmpty()) {
            Node node = pending.pop(); Object value = node.value();
            if (value instanceof Map<?,?> map) {
                if (node.depth() > 32) throw fail("SYNC_PAYLOAD_INVALID");
                for (var entry : map.entrySet()) {
                    if (!(entry.getKey() instanceof String)) throw fail("SYNC_PAYLOAD_INVALID");
                    pending.push(new Node(entry.getValue(), node.depth()+1));
                }
            } else if (value instanceof List<?> list) {
                if (node.depth() > 32) throw fail("SYNC_PAYLOAD_INVALID");
                for (Object child : list) pending.push(new Node(child, node.depth()+1));
            } else if (value != null && !(value instanceof String || value instanceof Boolean || isJsonNumber(value))) {
                throw fail("SYNC_PAYLOAD_INVALID");
            } else if (value instanceof Double d && !Double.isFinite(d)
                    || value instanceof Float f && !Float.isFinite(f)) throw fail("SYNC_PAYLOAD_INVALID");
        }
    }

    private static void rejectSecret(Object value) {
        if (value instanceof Map<?,?> map) {
            for (var entry : map.entrySet()) {
                String key = String.valueOf(entry.getKey()).toLowerCase(java.util.Locale.ROOT);
                if (SECRET_KEYS.contains(key) || List.of("password", "token", "secret", "credential", "private_key")
                        .stream().anyMatch(key::contains)) throw fail("SYNC_SECRET_FIELD_REJECTED");
            }
            map.values().forEach(SyncPayload::rejectSecret);
        } else if (value instanceof List<?> list) list.forEach(SyncPayload::rejectSecret);
    }

    private static Map<String,Object> projectRefactorResource(String entityId, Map<String,Object> envelope,
                                                               Map<String,Object> values) {
        Set<String> required = Set.of("material_id", "filename", "media_type", "sha256", "size", "product_classification");
        Set<String> withText = new java.util.HashSet<>(required); withText.add("text");
        if (!values.keySet().equals(required) && !values.keySet().equals(withText)) throw fail("SYNC_PAYLOAD_INVALID");
        if (!(values.get("material_id") instanceof String recordId) || !recordId.equals(envelope.get("record_id"))
                || !isExactInteger(values.get("size")) || compareNumber(values.get("size"),0)<0) throw fail("SYNC_PAYLOAD_INVALID");
        String expected = "refactor.v1:resource:" + sha256(recordId.getBytes(StandardCharsets.UTF_8));
        if (!entityId.equals(expected)) throw fail("SYNC_ENTITY_INVALID");
        Map<String,Object> canonical = new LinkedHashMap<>();
        canonical.put("filename", values.get("filename")); canonical.put("media_type", values.get("media_type"));
        canonical.put("sha256", values.get("sha256")); canonical.put("size_bytes", values.get("size"));
        canonical.put("product_classification", values.get("product_classification"));
        canonical.put("text", values.getOrDefault("text", "")); canonical.put("refactor_record_id", recordId);
        return validateResource(canonical);
    }

    private static void validateDraftPlan(Map<String,Object> values) {
        Object status = values.get("status"), snapshot = values.get("snapshot");
        // Python isinstance(True, int) is true; preserve that legacy quirk.
        if (!(isExactInteger(values.get("revision")) || values.get("revision") instanceof Boolean)
                || compareNumber(values.get("revision"),1)<0
                || !("NEEDS_INPUT".equals(status) || "PLAN_READY".equals(status))
                || !(snapshot instanceof Map<?,?>) || !(values.get("confirmed") instanceof Boolean))
            throw fail("SYNC_PAYLOAD_INVALID");
        if (Boolean.TRUE.equals(values.get("confirmed"))) {
            Object confirm = ((Map<?,?>)snapshot).get("draft_confirmation");
            Set<String> keys=Set.of("schema","draft_id","revision","confirmed_at","confirmed_by","idempotency_key");
            if (!(confirm instanceof Map<?,?> c) || !c.keySet().equals(keys)
                    || !"mentorflow.draft-confirmation.v1".equals(c.get("schema"))
                    || !java.util.Objects.equals(c.get("draft_id"),values.get("draft_id"))
                    || !pyEquals(c.get("revision"),values.get("revision"))) throw fail("SYNC_PAYLOAD_INVALID");
        }
    }

    private static boolean isBlobManifest(Map<String,Object> values) {
        return values.keySet().equals(Set.of("blob_ref", "record_type", "record_id", "summary"));
    }

    private static void validateBlobManifest(String recordType,String entityId,Map<String,Object> values) {
        Object ref=values.get("blob_ref"), summary=values.get("summary"), recordId=values.get("record_id");
        String expected = "refactor.v1:"+recordType+":"+sha256(String.valueOf(recordId).getBytes(StandardCharsets.UTF_8));
        if (!recordType.equals(values.get("record_type")) || !(recordId instanceof String)
                || !expected.equals(entityId) || !(summary instanceof Map<?,?>) || !(ref instanceof Map<?,?> r)
                || !"mentorflow.history-blob.v1".equals(r.get("schema"))
                || !(r.get("sha256") instanceof String hash) || !hash.matches("[a-f0-9]{64}")
                || !isExactInteger(r.get("size")) || compareNumber(r.get("size"),0)<0 || compareNumber(r.get("size"),8*1024*1024)>0
                || !"application/json".equals(r.get("content_type"))) throw fail("SYNC_PAYLOAD_INVALID");
    }

    private static void validateProduct(String kind,String entityId,Map<String,Object> payload) {
        Object rec=payload.get("product_record");
        if (rec==null) {
            if (SyncJson.dumpAscii(payload).getBytes(StandardCharsets.UTF_8).length > LIMIT)
                throw fail("PRODUCT_SYNC_RECORD_TOO_LARGE");
            return;
        }
        if (!(rec instanceof Map<?,?> raw) || !raw.keySet().equals(Set.of("schema","record_type","values"))
                || !DEFINITIONS.get("product_schema").equals(raw.get("schema"))
                || !(raw.get("record_type") instanceof String table)) throw fail("PRODUCT_SYNC_RECORD_SCHEMA");
        Map<String,Object> spec=map(PRODUCT_RECORDS.get(table)); Object rawValues=raw.get("values");
        if (spec.isEmpty() || !kind.equals(spec.get("kind")) || !(rawValues instanceof Map<?,?> vraw))
            throw fail("PRODUCT_SYNC_RECORD_FIELDS");
        Map<String,Object> values=stringMap(vraw);
        List<String> columns=strings(spec.get("columns"));
        if (!values.keySet().equals(Set.copyOf(columns))) throw fail("PRODUCT_SYNC_RECORD_FIELDS");
        for (Object value:values.values()) if(value!=null && !(value instanceof String || isExactInteger(value)
                || value instanceof Double d && Double.isFinite(d) || value instanceof Float f && Float.isFinite(f)))
            throw fail("PRODUCT_SYNC_RECORD_VALUE");
        for(String key:strings(spec.get("keys"))) {
            Object value=values.get(key);
            if (!(value instanceof String || isExactInteger(value)) || "".equals(value)) throw fail("PRODUCT_SYNC_RECORD_KEY");
        }
        boolean auxiliary=Boolean.TRUE.equals(spec.get("auxiliary"));
        Object suppliedAuxiliary=payload.getOrDefault("product_auxiliary",Boolean.FALSE);
        if (!(suppliedAuxiliary instanceof Boolean flag) || flag!=auxiliary)
            throw fail("PRODUCT_SYNC_AUXILIARY_MISMATCH");
        if ("active_materials".equals(table)) {
            if (!pyEquals(payload.get("filename"),values.get("original_filename"))
                    || !pyEquals(payload.get("sha256"),values.get("sha256"))
                    || !pyEquals(payload.get("media_type"),values.get("media_type"))
                    || !pyEquals(payload.get("size_bytes"),values.get("size_bytes"))) throw fail("PRODUCT_SYNC_RESOURCE_MISMATCH");
        }
        if (!publicValueEquals(values)) throw fail("PRODUCT_SYNC_PRIVATE_FIELD");
        String expected=productRecordId(table,values);
        if (!expected.equals(entityId)) throw fail("PRODUCT_SYNC_RECORD_ID");
        int limit=LARGE_TYPES.contains(table)?8*1024*1024:LIMIT;
        if (SyncJson.dumpAscii(payload).getBytes(StandardCharsets.UTF_8).length > limit)
            throw fail("PRODUCT_SYNC_RECORD_TOO_LARGE");
        if (Set.of("product_plan_state","product_account_settings","product_review_state").contains(table)) {
            // The current slim Python tree references these validators but ships neither module.
            // Python therefore fails with an uncaught ModuleNotFoundError (HTTP 500).
            String missing="product_account_settings".equals(table)?"product_settings_api":"product_runtime_state";
            throw new IllegalStateException("No module named 'refactor.server."+missing+"'");
        }
    }

    private static boolean publicValueEquals(Object value) {
        if(value instanceof Map<?,?> map) {
            for(var e:map.entrySet()) {
                String key=String.valueOf(e.getKey()).toLowerCase(java.util.Locale.ROOT);
                if(PRIVATE_SUBSTRINGS.stream().anyMatch(key::contains)) return false;
                if(!publicValueEquals(e.getValue())) return false;
            }
            return true;
        }
        if(value instanceof List<?> list) return list.stream().allMatch(SyncPayload::publicValueEquals);
        if(value instanceof String s && (s.stripLeading().startsWith("{")||s.stripLeading().startsWith("["))) {
            try {
                Object decoded=SyncJson.read(s);
                return SyncJson.dump(publicValue(decoded),false).equals(s);
            } catch(Exception ignored) { return true; }
        }
        return true;
    }

    private static Object publicValue(Object value) {
        if(value instanceof Map<?,?> map) {
            Map<String,Object> out=new LinkedHashMap<>();
            for(var e:map.entrySet()) {String k=String.valueOf(e.getKey());String low=k.toLowerCase(java.util.Locale.ROOT);
                if(PRIVATE_SUBSTRINGS.stream().noneMatch(low::contains)) out.put(k,publicValue(e.getValue()));}
            return out;
        }
        if(value instanceof List<?> list) return list.stream().map(SyncPayload::publicValue).toList();
        if(value instanceof String s && (s.stripLeading().startsWith("{")||s.stripLeading().startsWith("["))) {
            try{return SyncJson.dump(publicValue(SyncJson.read(s)),false);}catch(Exception ignored){return s;}
        }
        return value;
    }

    private static String productRecordId(String table,Map<String,Object> values) {
        List<Object> keys=new ArrayList<>(); for(String k:strings(map(PRODUCT_RECORDS.get(table)).get("keys"))) keys.add(values.get(k));
        if("active_materials".equals(table)) return String.valueOf(keys.get(0));
        return table+":"+sha256(SyncJson.dump(keys,false).getBytes(StandardCharsets.UTF_8));
    }

    private static String refactorEntityId(String type,String id) {
        return "refactor.v1:"+type+":"+sha256(id.getBytes(StandardCharsets.UTF_8));
    }

    private static Map<String,Object> validateResource(Map<String,Object> source) {
        Object filename=source.get("filename"),media=source.get("media_type"),sha=source.get("sha256");
        Object size=source.get("size_bytes"),classification=source.getOrDefault("product_classification","UNKNOWN");
        Object text=source.get("text"),recordId=source.get("refactor_record_id");
        if (!(filename instanceof String f) || codepoints(f)<1 || codepoints(f)>300 || !f.matches("^[^/\\\\\\u0000-\\u001f]+$")
                || !(media instanceof String m) || codepoints(m)<1 || codepoints(m)>120 || !m.matches("^[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+$")
                || !(sha instanceof String h) || !h.matches("^[a-f0-9]{64}$")
                || !isExactInteger(size) || compareNumber(size,0)<0 || compareNumber(size,Long.MAX_VALUE)>0
                || !(classification instanceof String c) || !Set.of("USER_FILE","MODEL","OTHER","UNKNOWN").contains(c)
                || text!=null && (!(text instanceof String t)||codepoints(t)>8000)
                || recordId!=null && (!(recordId instanceof String r)||!ID.matcher(r).matches())) throw fail("SYNC_PAYLOAD_INVALID");
        Map<String,Object> out=new LinkedHashMap<>();
        // Pydantic model_dump follows Resource's declared field order.
        if(source.get("product_record")!=null)out.put("product_record",source.get("product_record"));
        out.put("product_classification",classification);out.put("filename",filename);
        out.put("media_type",media);out.put("sha256",sha);out.put("size_bytes",size);
        out.put("text",text);out.put("refactor_record_id",recordId);
        return out;
    }

    private static void projectPlan(Map<String,Object> payload) {
        Object title=payload.get("title"), ids=payload.getOrDefault("resource_ids",List.of());
        if (!keysWithin(payload,Set.of("title","resource_ids","product_record")) || !payload.containsKey("title")
                || !(title instanceof String s)||codepoints(s)<1||codepoints(s)>300 || !(ids instanceof List<?> list)||list.size()>100
                || list.stream().anyMatch(v -> !(v instanceof String))
                || payload.containsKey("product_record")&&payload.get("product_record")!=null&&!(payload.get("product_record") instanceof Map<?,?>))
            throw fail("SYNC_PAYLOAD_INVALID");
        Object product=payload.get("product_record");
        payload.clear();payload.put("title",title);payload.put("resource_ids",new ArrayList<>(list));
        payload.put("product_record",product);
    }

    private static void projectProgress(Map<String,Object> payload) {
        Object plan=payload.get("plan_id"), mastery=payload.get("mastery"), completed=payload.get("completed_tasks");
        if (!keysWithin(payload,Set.of("plan_id","mastery","completed_tasks","product_record")) || !payload.containsKey("plan_id")
                || !(plan==null || plan instanceof String p&&ID.matcher(p).matches())
                || !(mastery instanceof Number n) || !Double.isFinite(n.doubleValue()) || n.doubleValue()<0||n.doubleValue()>1
                || !isExactInteger(completed)||compareNumber(completed,0)<0
                || payload.containsKey("product_record")&&payload.get("product_record")!=null&&!(payload.get("product_record") instanceof Map<?,?>))
            throw fail("SYNC_PAYLOAD_INVALID");
        payload.put("mastery", n.doubleValue());
        Object record=payload.get("product_record");
        payload.clear();payload.put("plan_id",plan);payload.put("mastery",n.doubleValue());
        payload.put("completed_tasks",completed);payload.put("product_record",record);
    }

    private static void projectResource(Map<String,Object> payload) {
        if (!keysWithin(payload,Set.of("product_record","product_classification","filename","media_type","sha256","size_bytes","text","refactor_record_id")))
            throw fail("SYNC_PAYLOAD_INVALID");
        Object cls=payload.getOrDefault("product_classification","UNKNOWN");
        Map<String,Object> required=new LinkedHashMap<>();
        for(String key:List.of("filename","media_type","sha256","size_bytes")) {
            if(!payload.containsKey(key))throw fail("SYNC_PAYLOAD_INVALID");
            required.put(key,payload.get(key));
        }
        required.put("product_record",payload.get("product_record"));
        required.put("product_classification",cls);
        required.put("text",payload.get("text"));required.put("refactor_record_id",payload.get("refactor_record_id"));
        Map<String,Object> projected=validateResource(required);
        payload.clear();payload.putAll(projected);
    }

    private static String sha256(byte[] bytes){try{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));}catch(NoSuchAlgorithmException e){throw new IllegalStateException(e);}}
    private static boolean isJsonNumber(Object value){return value instanceof Number && !(value instanceof java.math.BigDecimal) ? true : value instanceof java.math.BigDecimal;}
    private static boolean isExactInteger(Object value){return value instanceof Byte||value instanceof Short||value instanceof Integer||value instanceof Long||value instanceof java.math.BigInteger;}
    private static int compareNumber(Object value,long other){
        java.math.BigDecimal number=value instanceof Boolean flag
            ?(flag?java.math.BigDecimal.ONE:java.math.BigDecimal.ZERO):new java.math.BigDecimal(value.toString());
        return number.compareTo(java.math.BigDecimal.valueOf(other));
    }
    private static SyncFailure fail(String code){return new SyncFailure(code,422);}
    private static Map<String,Object> loadDefinitions(){
        try(var in=SyncPayload.class.getResourceAsStream("/sync/SyncDefinitions.json")){
            if(in==null)throw new IllegalStateException("sync definitions missing");
            return map(SyncJson.read(new String(in.readAllBytes(),StandardCharsets.UTF_8)));
        }catch(IOException ex){throw new ExceptionInInitializerError(ex);}
    }
    @SuppressWarnings("unchecked") private static Map<String,Object> map(Object v){return v instanceof Map<?,?> m?(Map<String,Object>)m:Collections.emptyMap();}
    private static List<?> list(Object v){return v instanceof List<?> l?l:List.of();}
    private static List<String> strings(Object v){return list(v).stream().map(String::valueOf).toList();}
    private static Set<String> set(Object v){return Set.copyOf(strings(v));}
    private static boolean keysWithin(Map<String,Object> values,Set<String> allowed){return allowed.containsAll(values.keySet());}
    private static int codepoints(String value){return value.codePointCount(0,value.length());}
    private static boolean pyEquals(Object a,Object b){
        if(a instanceof Boolean flag && b instanceof Number number)
            return compareNumber(number,flag?1:0)==0;
        if(b instanceof Boolean flag && a instanceof Number number)
            return compareNumber(number,flag?1:0)==0;
        if(a instanceof Number x&&b instanceof Number y)return new java.math.BigDecimal(x.toString()).compareTo(new java.math.BigDecimal(y.toString()))==0;
        return java.util.Objects.equals(a,b);
    }
    private static boolean pythonTruthy(Object value){
        if(value==null||Boolean.FALSE.equals(value))return false;
        if(value instanceof Number number)return new java.math.BigDecimal(number.toString()).compareTo(java.math.BigDecimal.ZERO)!=0;
        if(value instanceof String s)return !s.isEmpty();
        if(value instanceof Map<?,?> m)return !m.isEmpty();
        if(value instanceof Iterable<?> i)return i.iterator().hasNext();
        return true;
    }
    private static Map<String,Object> stringMap(Map<?,?> input){Map<String,Object> result=new LinkedHashMap<>();for(var e:input.entrySet()){if(!(e.getKey() instanceof String k))throw fail("SYNC_PAYLOAD_INVALID");result.put(k,e.getValue());}return result;}
}
