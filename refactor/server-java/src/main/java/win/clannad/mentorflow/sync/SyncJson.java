package win.clannad.mentorflow.sync;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;

/** Python json.dumps compatibility for HTTP bytes, size checks and receipts. */
public final class SyncJson {
    private static final ObjectMapper JSON=new ObjectMapper()
        .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
        .enable(JsonParser.Feature.ALLOW_NON_NUMERIC_NUMBERS);
    private SyncJson() {}
    public static Object read(String input) {
        try { return JSON.readValue(input,Object.class); }
        catch (Exception error) { throw new IllegalArgumentException("Invalid JSON",error); }
    }
    public static String dump(Object value, boolean sorted) { return encode(value,sorted,false,false); }
    public static String dumpAsciiSorted(Object value) { return encode(value,true,true,false); }
    public static String dumpAscii(Object value) { return encode(value,false,true,true); }
    private static String encode(Object value,boolean sorted,boolean ascii,boolean spaced) {
        StringBuilder text=new StringBuilder(); append(text,value,sorted,ascii,spaced); return text.toString();
    }
    private static final Comparator<String> CODEPOINTS=(a,b)-> {
        int ai=0,bi=0;
        while(ai<a.length()&&bi<b.length()) {
            int ac=a.codePointAt(ai),bc=b.codePointAt(bi);
            if(ac!=bc)return Integer.compare(ac,bc);
            ai+=Character.charCount(ac);bi+=Character.charCount(bc);
        }
        return Integer.compare(a.length()-ai,b.length()-bi);
    };
    private static void append(StringBuilder out,Object value,boolean sorted,boolean ascii,boolean spaced) {
        if(value==null)out.append("null");
        else if(value instanceof String text) quote(out,text,ascii);
        else if(value instanceof Boolean flag)out.append(flag?"true":"false");
        else if(value instanceof Float||value instanceof Double)out.append(pythonFloat(((Number)value).doubleValue()));
        else if(value instanceof Number)out.append(value);
        else if(value instanceof Map<?,?> map) {
            out.append('{'); var keys=new ArrayList<String>(); for(Object k:map.keySet())keys.add((String)k);
            if(sorted)keys.sort(CODEPOINTS);boolean first=true;
            for(String key:keys) {if(!first)out.append(spaced?", ":",");first=false;quote(out,key,ascii);out.append(spaced?": ":":");append(out,map.get(key),sorted,ascii,spaced);}
            out.append('}');
        } else if(value instanceof Iterable<?> values) {
            out.append('[');boolean first=true;for(Object child:values){if(!first)out.append(spaced?", ":",");first=false;append(out,child,sorted,ascii,spaced);}out.append(']');
        } else throw new IllegalArgumentException("Not JSON");
    }
    private static String pythonFloat(double value) {
        if(!Double.isFinite(value))throw new IllegalArgumentException("Non-finite JSON number");
        if(value==0)return Double.doubleToRawLongBits(value)<0?"-0.0":"0.0";
        // Java's shortest spelling differs for a few values (notably the
        // smallest subnormal: 4.9e-324 vs Python's 5e-324). Choose the nearest
        // shortest decimal that round-trips to the same IEEE-754 value.
        BigDecimal exact=new BigDecimal(value), decimal=null;
        for(int precision=1;precision<=17;precision++) {
            BigDecimal candidate=exact.round(new java.math.MathContext(precision,java.math.RoundingMode.HALF_EVEN)).stripTrailingZeros();
            if(Double.parseDouble(candidate.toString())==value){decimal=candidate;break;}
        }
        if(decimal==null)decimal=BigDecimal.valueOf(value).stripTrailingZeros();
        int exponent=decimal.precision()-decimal.scale()-1;
        if(exponent>=-4&&exponent<16) {
            String plain=decimal.toPlainString();return plain.contains(".")?plain:plain+".0";
        }
        String mantissa=decimal.movePointLeft(exponent).toPlainString();
        return mantissa+"e"+(exponent<0?"-":"+")+String.format(java.util.Locale.ROOT,"%02d",Math.abs(exponent));
    }
    private static void quote(StringBuilder out,String text,boolean ascii) {
        out.append('"');
        for(int i=0;i<text.length();i++) {
            char c=text.charAt(i);
            switch(c) {
                case '"' -> out.append("\\\""); case '\\' -> out.append("\\\\");
                case '\b' -> out.append("\\b");case '\f' -> out.append("\\f");
                case '\n' -> out.append("\\n");case '\r' -> out.append("\\r");case '\t' -> out.append("\\t");
                default -> {if(c<32||(ascii&&c>127))out.append(String.format(java.util.Locale.ROOT,"\\u%04x",(int)c));else out.append(c);}
            }
        }
        out.append('"');
    }
}
