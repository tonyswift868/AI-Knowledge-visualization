package win.clannad.mentorflow.sync;

import java.math.BigInteger;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** The strict Pydantic request seam (domain payload projection is separate). */
final class SyncValidation {
    static final List<String> KINDS=List.of("plan","progress","resource","task","calendar","review","learner_profile","learning_run","history","setting");
    static final String REQUEST_PATTERN="^[A-Za-z0-9._:-]+$";
    static final class Invalid extends RuntimeException {
        final List<Map<String,Object>> errors;
        Invalid(List<Map<String,Object>> errors) {this.errors=errors;}
    }
    static Map<String,Object> ordered(Object... pairs) {
        var result=new LinkedHashMap<String,Object>();for(int i=0;i<pairs.length;i+=2)result.put((String)pairs[i],pairs[i+1]);return result;
    }
    static Map<String,Object> issue(String type,List<?> loc,String message,Object input,Object... ctx) {
        var result=ordered("type",type,"loc",loc,"msg",message,"input",input);
        if(ctx.length>0)result.put("ctx",ordered(ctx));return result;
    }
    static void literal(List<Map<String,Object>> errors,String input,String location) {
        if(KINDS.contains(input))return;
        String expected=String.join(", ",KINDS.subList(0,KINDS.size()-1).stream().map(v->"'"+v+"'").toList())+" or '"+KINDS.getLast()+"'";
        errors.add(issue("literal_error",List.of(location,"kind"),"Input should be "+expected,input,"expected",expected));
    }
    static Long queryInteger(List<Map<String,Object>> errors,String name,String input,long defaultValue,long minimum,long maximum) {
        if(input==null)return defaultValue;
        long number;
        try {
            if(!input.strip().matches("[+-]?[0-9]+(?:_[0-9]+)*(?:\\.0+)?"))throw new NumberFormatException();
            var value=new java.math.BigDecimal(input.strip().replace("_",""));
            if(value.compareTo(java.math.BigDecimal.valueOf(minimum))<0) {
                errors.add(issue("greater_than_equal",List.of("query",name),"Input should be greater than or equal to "+minimum,input,"ge",minimum));return null;
            }
            if(value.compareTo(java.math.BigDecimal.valueOf(maximum))>0) {
                errors.add(issue("less_than_equal",List.of("query",name),"Input should be less than or equal to "+maximum,input,"le",maximum));return null;
            }
            number=value.longValueExact();
        } catch(ArithmeticException|NumberFormatException ex) {
            errors.add(issue("int_parsing",List.of("query",name),"Input should be a valid integer, unable to parse string as an integer",input));return null;
        }
        return number;
    }
    @SuppressWarnings("unchecked") static Map<String,Object> operation(Object input,boolean deleting,List<Map<String,Object>> errors) {
        if(!(input instanceof Map<?,?>)) {
            errors.add(issue("model_attributes_type",List.of("body"),"Input should be a valid dictionary or object to extract fields from",input));return null;
        }
        Map<String,Object> body=(Map<String,Object>)input;
        stringField(body,"request_id",errors);
        String key="expected_revision";
        if(!body.containsKey(key))errors.add(issue("missing",List.of("body",key),"Field required",input));
        else {
            Object value=body.get(key);
            if(!(value instanceof Byte||value instanceof Short||value instanceof Integer||value instanceof Long||value instanceof BigInteger))
                errors.add(issue("int_type",List.of("body",key),"Input should be a valid integer",value));
            else {
                BigInteger integer=new BigInteger(value.toString());int minimum=deleting?1:0;
                if(integer.compareTo(BigInteger.valueOf(minimum))<0)errors.add(issue("greater_than_equal",List.of("body",key),"Input should be greater than or equal to "+minimum,value,"ge",minimum));
                else if(integer.compareTo(BigInteger.valueOf(2147483646))>0)errors.add(issue("less_than_equal",List.of("body",key),"Input should be less than or equal to 2147483646",value,"le",2147483646));
            }
        }
        if(!deleting) {
            if(!body.containsKey("payload"))errors.add(issue("missing",List.of("body","payload"),"Field required",input));
            else if(!(body.get("payload") instanceof Map))errors.add(issue("dict_type",List.of("body","payload"),"Input should be a valid dictionary",body.get("payload")));
        }
        Set<String> fields=deleting?Set.of("request_id","expected_revision"):Set.of("request_id","expected_revision","payload");
        for(var entry:body.entrySet())if(!fields.contains(entry.getKey()))errors.add(issue("extra_forbidden",List.of("body",entry.getKey()),"Extra inputs are not permitted",entry.getValue()));
        return body;
    }
    private static void stringField(Map<String,Object> body,String key,List<Map<String,Object>> errors) {
        if(!body.containsKey(key)){errors.add(issue("missing",List.of("body",key),"Field required",body));return;}
        Object input=body.get(key);
        if(!(input instanceof String value)){errors.add(issue("string_type",List.of("body",key),"Input should be a valid string",input));return;}
        int size=value.codePointCount(0,value.length());
        if(size<1)errors.add(issue("string_too_short",List.of("body",key),"String should have at least 1 character",value,"min_length",1));
        else if(size>128)errors.add(issue("string_too_long",List.of("body",key),"String should have at most 128 characters",value,"max_length",128));
        else if(!value.matches("[A-Za-z0-9._:-]+"))errors.add(issue("string_pattern_mismatch",List.of("body",key),"String should match pattern '"+REQUEST_PATTERN+"'",value,"pattern",REQUEST_PATTERN));
    }
    @SuppressWarnings("unchecked") static List<Map<String,Object>> bulk(Object input,List<Map<String,Object>> errors) {
        if(!(input instanceof Map<?,?> body)){errors.add(issue("model_attributes_type",List.of("body"),"Input should be a valid dictionary or object to extract fields from",input));return null;}
        List<Map<String,Object>> rows=new ArrayList<>();
        if(!body.containsKey("records"))errors.add(issue("missing",List.of("body","records"),"Field required",input));
        else if(!(body.get("records") instanceof List<?> values))errors.add(issue("list_type",List.of("body","records"),"Input should be a valid list",body.get("records")));
        else {
            for(int i=0;i<values.size();i++) {
                if(!(values.get(i) instanceof Map))errors.add(issue("dict_type",List.of("body","records",i),"Input should be a valid dictionary",values.get(i)));
                else rows.add((Map<String,Object>)values.get(i));
            }
            if(values.size()<1)errors.add(issue("too_short",List.of("body","records"),"List should have at least 1 item after validation, not 0",values,"field_type","List","min_length",1,"actual_length",0));
            else if(values.size()>500)errors.add(issue("too_long",List.of("body","records"),"List should have at most 500 items after validation, not "+values.size(),values,"field_type","List","max_length",500,"actual_length",values.size()));
        }
        for(var entry:body.entrySet())if(!entry.getKey().equals("records"))errors.add(issue("extra_forbidden",List.of("body",entry.getKey()),"Extra inputs are not permitted",entry.getValue()));
        return rows;
    }
    @SuppressWarnings("unchecked") static SyncRepository.Operation row(Map<String,Object> row) {
        if(!Set.of("kind","entity_id","request_id","expected_revision","payload","deleted").containsAll(row.keySet()))throw new SyncFailure("SYNC_RECORD_INVALID",422);
        Object deleted=row.getOrDefault("deleted",false);
        if(!(deleted instanceof Boolean))throw new SyncFailure("SYNC_RECORD_INVALID",422);
        boolean deleting=(Boolean)deleted;
        var data=new LinkedHashMap<>(row);data.remove("kind");data.remove("entity_id");data.remove("deleted");
        var errors=new ArrayList<Map<String,Object>>();operation(data,deleting,errors);
        if(!errors.isEmpty())throw new SyncFailure("SYNC_RECORD_INVALID",422);
        if(!(row.get("kind") instanceof String kind)||!(row.get("entity_id") instanceof String entityId))throw new SyncFailure("SYNC_ENTITY_INVALID",422);
        var projected=SyncPayload.project(kind,entityId,(Map<String,Object>)data.get("payload"),deleting);
        return new SyncRepository.Operation(kind,entityId,(String)data.get("request_id"),((Number)data.get("expected_revision")).intValue(),projected,deleting);
    }
}
