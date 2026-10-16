package win.clannad.mentorflow.account;

import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.Map;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.core.JsonProcessingException;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;
import win.clannad.mentorflow.identity.IdentityDirectory;
import win.clannad.mentorflow.identity.IdentityFailure;
import win.clannad.mentorflow.identity.IdentitySettings;

/** First-use account workspace endpoints; only the authenticated account is addressable. */
@RestController
@RequestMapping("/api/enterprise/account/workspaces")
public final class WorkspaceController146 {
    private static final ObjectMapper JSON=new ObjectMapper().enable(com.fasterxml.jackson.databind.DeserializationFeature.FAIL_ON_TRAILING_TOKENS);
    private final WorkspaceBootstrap146 bootstrap;
    private final IdentityDirectory directory;
    private final IdentitySettings settings;

    public WorkspaceController146(WorkspaceBootstrap146 bootstrap,IdentityDirectory directory,IdentitySettings settings) {
        this.bootstrap=bootstrap;this.directory=directory;this.settings=settings;
    }

    @GetMapping
    public ResponseEntity<byte[]> discover(HttpServletRequest request) {
        var principal=accountPrincipal(request);return success(bootstrap.discover(principal.getToken().getClaimAsString("iss"),principal.getToken().getSubject()));
    }

    @PostMapping("/initialize")
    public Object initialize(HttpServletRequest request) throws IOException {
        byte[] raw=request.getInputStream().readAllBytes();
        JsonNode body;
        String input=new String(raw,StandardCharsets.UTF_8);
        try{body=raw.length==0?null:JSON.readTree(input);}
        catch(JsonProcessingException failure){
            long offset=failure.getLocation()==null?-1:failure.getLocation().getCharOffset();
              int charOffset=offset<0?0:(int)Math.min(offset,input.length());
              Integer trailingPosition=jsonTrailingDataPosition(input);
              int position=trailingPosition==null?jsonErrorPosition(input,failure.getOriginalMessage(),charOffset):trailingPosition;
              var issue=issue("json_invalid",java.util.List.of("body",position),"JSON decode error",Map.of());
              var context=new LinkedHashMap<String,Object>();context.put("error",trailingPosition==null?jsonErrorMessage(input,failure.getOriginalMessage()):"Extra data");issue.put("ctx",context);
            return validationError(java.util.List.of(issue));
        }
        catch(Exception failure){return validationError(java.util.List.of(issue("json_invalid",java.util.List.of("body",0),"JSON decode error",input)));}
        if(body==null&&raw.length==0||body!=null&&body.isNull())return validationError(java.util.List.of(issue("missing",java.util.List.of("body"),"Field required",null)));
        if(body==null||body.isMissingNode()) {
            var issue=issue("json_invalid",java.util.List.of("body",input.codePointCount(0,input.length())),"JSON decode error",Map.of());
            var context=new LinkedHashMap<String,Object>();context.put("error","Expecting value");issue.put("ctx",context);
            return validationError(java.util.List.of(issue));
        }
        if(!body.isObject())return validationError(java.util.List.of(issue("model_attributes_type",java.util.List.of("body"),"Input should be a valid dictionary or object to extract fields from",JSON.convertValue(body,Object.class))));
        if(body.size()!=0) {
            var issues=new ArrayList<Map<String,Object>>();
            body.fields().forEachRemaining(field->issues.add(issue("extra_forbidden",java.util.List.of("body",field.getKey()),"Extra inputs are not permitted",JSON.convertValue(field.getValue(),Object.class))));
            return validationError(issues);
        }
        var principal=accountPrincipal(request);return success(bootstrap.initialize(principal.getToken().getClaimAsString("iss"),principal.getToken().getSubject()));
    }

    @RequestMapping(value="",method={RequestMethod.HEAD,RequestMethod.POST,RequestMethod.PUT,RequestMethod.PATCH,
        RequestMethod.DELETE,RequestMethod.OPTIONS,RequestMethod.TRACE})
    public ResponseEntity<byte[]> unsupportedDiscoveryMethod() { return methodNotAllowed("GET"); }

    @RequestMapping(value="/initialize",method={RequestMethod.HEAD,RequestMethod.GET,RequestMethod.PUT,
        RequestMethod.PATCH,RequestMethod.DELETE,RequestMethod.OPTIONS,RequestMethod.TRACE})
    public ResponseEntity<byte[]> unsupportedInitializeMethod() { return methodNotAllowed("POST"); }

    private JwtAuthenticationToken accountPrincipal(HttpServletRequest request) {
        String authorization=request.getHeader(HttpHeaders.AUTHORIZATION);
        if(authorization==null||!authorization.strip().startsWith("Bearer ")||authorization.strip().indexOf(' ',7)>=0)
            throw new IdentityFailure("ENTERPRISE_AUTH_REQUIRED","A verified account is required");
        if(request.getCookies()!=null)for(var cookie:request.getCookies())
            if(cookie.getName().equals(settings.cookieName)&&!cookie.getValue().isEmpty())
                throw new IdentityFailure(403,"AUTH_BOUNDARY_CONFLICT","Account bootstrap requires bearer authentication");
        if(nonEmpty(request.getHeader("X-MentorFlow-Organization-ID"))||nonEmpty(request.getHeader("X-MentorFlow-Workspace-ID")))
            throw new IdentityFailure(422,"ACCOUNT_BOOTSTRAP_CONTEXT_NOT_ALLOWED","Discover account workspaces without tenant headers");
        Authentication authentication=SecurityContextHolder.getContext().getAuthentication();
        if(!(authentication instanceof JwtAuthenticationToken jwt))throw new IdentityFailure("ENTERPRISE_AUTH_REQUIRED","A verified account is required");
        String issuer=jwt.getToken().getClaimAsString("iss"),subject=jwt.getToken().getSubject();
        String client=jwt.getToken().getClaimAsString("azp");if(client==null)client=jwt.getToken().getClaimAsString("client_id");
        boolean service=client!=null&&("service-account-"+client).equals(jwt.getToken().getClaimAsString("preferred_username"));
        if(service||issuer==null||subject==null||!issuer.equals(settings.canonicalIssuer))
            throw new IdentityFailure(403,"ACCOUNT_BOOTSTRAP_HUMAN_REQUIRED","A human account is required");
        directory.ensureOpen(issuer,subject);
        return jwt;
    }
    private static boolean nonEmpty(String value){return value!=null&&!value.isEmpty();}
    private static String jsonErrorMessage(String input,String jacksonMessage) {
        // The HTTP error is FastAPI's JSONDecodeError projection, not Jackson's
        // implementation-specific diagnostic. Parsing remains Jackson-owned.
        if(jacksonMessage.contains("double-quote to start field name"))return "Expecting property name enclosed in double quotes";
        if(jacksonMessage.contains("colon to separate"))return "Expecting ':' delimiter";
        if(jacksonMessage.contains("comma to separate"))return "Expecting ',' delimiter";
        if(jacksonMessage.contains("Unrecognized character escape"))return "Invalid \\escape";
        if(jacksonMessage.contains("expected a hex-digit for character escape sequence"))return "Invalid \\uXXXX escape";
        if(jacksonMessage.contains("Illegal unquoted character"))return "Invalid control character at";
        if(jacksonMessage.contains("Decimal point not followed by a digit"))return "Expecting ',' delimiter";
        if(jacksonMessage.contains("Leading zeroes not allowed"))return "Expecting ',' delimiter";
        if(jacksonMessage.contains("Trailing token"))return "Extra data";
        if(jacksonMessage.contains("Unexpected end-of-input"))return eofJsonError(input);
        if(jacksonMessage.contains("Unrecognized token"))return "Expecting value";
        if(jacksonMessage.contains("Unexpected close marker")) {
            int open=arrayOpenBeforeMismatchedClose(input);
            if(open>=0&&input.substring(open+1).strip().length()>1)return "Expecting ',' delimiter";
            return "Expecting value";
        }
        if(jacksonMessage.contains("numeric value")) {
            if(input.matches("(?s).*:[\\s]*0[0-9].*"))return "Expecting ',' delimiter";
            if(input.matches("(?s).*[0-9]\\.[\\s}\\]].*" )||input.matches("(?s).*[0-9][eE][+-]?[\\s}\\]].*"))return "Expecting ',' delimiter";
            return "Expecting value";
        }
        return jacksonMessage;
    }
    private static String eofJsonError(String input) {
        String text=input.stripTrailing();
        int quote=-1;boolean inString=false,escaped=false;
        for(int i=0;i<text.length();i++) {
            char c=text.charAt(i);
            if(inString) {if(escaped)escaped=false;else if(c=='\\')escaped=true;else if(c=='"'){inString=false;quote=-1;}}
            else if(c=='"'){inString=true;quote=i;}
        }
        if(inString)return "Unterminated string starting at";
        if(text.endsWith("{" ))return "Expecting property name enclosed in double quotes";
        if(text.endsWith(","))return lastUnclosedContainer(text)=='['?"Expecting value":"Expecting property name enclosed in double quotes";
        if(text.endsWith("\""))return lastClosedStringIsObjectKey(text)?"Expecting ':' delimiter":"Expecting ',' delimiter";
        if(text.endsWith(":" )||text.endsWith("["))return "Expecting value";
        if(text.matches("(?s).*:[\\s]*-?[0-9]+[eE][+-]?$" )||text.matches("(?s).*:[\\s]*-?[0-9]+\\.$"))return "Expecting ',' delimiter";
        if(text.matches("(?s).*(true|false|null)$"))return "Expecting ',' delimiter";
        if(!text.isEmpty()) {
            char last=text.charAt(text.length()-1);
            if(last=='}'||last==']'||Character.isDigit(last))return "Expecting ',' delimiter";
        }
        return "Expecting value";
    }
    private static char lastUnclosedContainer(String text) {
        java.util.ArrayDeque<Character> stack=new java.util.ArrayDeque<>();
        boolean inString=false,escaped=false;
        for(int i=0;i<text.length();i++) {
            char c=text.charAt(i);
            if(inString) {
                if(escaped)escaped=false;
                else if(c=='\\')escaped=true;
                else if(c=='"')inString=false;
                continue;
            }
            if(c=='"'){inString=true;continue;}
            if(c=='['||c=='{')stack.push(c);
            else if((c==']'||c=='}')&&!stack.isEmpty())stack.pop();
        }
        return stack.isEmpty()?0:stack.peek();
    }
    private static boolean lastClosedStringIsObjectKey(String text) {
        int opening=text.lastIndexOf('"',text.length()-2);if(opening<0)return false;
        int previous=opening-1;while(previous>=0&&Character.isWhitespace(text.charAt(previous)))previous--;
        return previous>=0&&(text.charAt(previous)=='{'||text.charAt(previous)==',')&&lastUnclosedContainer(text)=='{';
    }
    private static int jsonErrorPosition(String input,String jacksonMessage,int charOffset) {
        if(jacksonMessage.contains("Unexpected end-of-input")) {
            String text=input.stripTrailing();
            int quote=-1;boolean inString=false,escaped=false;
            for(int i=0;i<text.length();i++) {
                char c=text.charAt(i);
                if(inString) {
                    if(escaped)escaped=false;
                    else if(c=='\\')escaped=true;
                    else if(c=='"'){inString=false;quote=-1;}
                } else if(c=='"'){inString=true;quote=i;}
            }
            if(inString&&quote>=0)return input.codePointCount(0,quote);
            if(text.matches("(?s).*:[\\s]*-?[0-9]+[eE][+-]?$" )||text.matches("(?s).*:[\\s]*-?[0-9]+\\.$")) {
                int end=input.length();if(text.matches("(?s).*[eE][+-]$"))end=Math.max(0,end-2);else end=Math.max(0,end-1);
                return input.codePointCount(0,end);
            }
            return input.codePointCount(0,input.length());
        }
        if(jacksonMessage.contains("Unrecognized character escape"))return input.codePointCount(0,Math.max(0,charOffset-1));
        if(jacksonMessage.contains("expected a hex-digit for character escape sequence")) {
            int escape=input.lastIndexOf("\\u",Math.min(charOffset,input.length()));if(escape>=0)return input.codePointCount(0,escape+1);
        }
        if(jacksonMessage.contains("Leading zeroes not allowed")) {
            int colon=input.lastIndexOf(':',Math.min(charOffset,input.length()));
            int number=colon+1;while(number<input.length()&&Character.isWhitespace(input.charAt(number)))number++;
            return input.codePointCount(0,Math.min(input.length(),number+1));
        }
        if(jacksonMessage.contains("Unexpected close marker"))return input.codePointCount(0,charOffset);
        if(jacksonMessage.contains("numeric value")) {
            if(input.stripTrailing().matches("(?s).*:[\\s]*-?[0-9]+\\.$"))return input.codePointCount(0,input.stripTrailing().length()-1);
            return input.codePointCount(0,Math.max(0,charOffset-1));
        }
        return input.codePointCount(0,charOffset);
    }
    private static Integer jsonTrailingDataPosition(String input) {
        try(var parser=JSON.createParser(input)) {
            var first=JSON.reader().without(com.fasterxml.jackson.databind.DeserializationFeature.FAIL_ON_TRAILING_TOKENS).readTree(parser);
            if(first==null)return null;
            long offset=parser.getCurrentLocation().getCharOffset();
            int end=(int)Math.max(0,Math.min(offset,input.length()));
            while(end<input.length()&&Character.isWhitespace(input.charAt(end)))end++;
            return end<input.length()?input.codePointCount(0,end):null;
        } catch(Exception ignored) {
            return null;
        }
    }
    private static int arrayOpenBeforeMismatchedClose(String input) {
        java.util.ArrayDeque<Integer> stack=new java.util.ArrayDeque<>();boolean inString=false,escaped=false;
        for(int i=0;i<input.length();i++) {
            char c=input.charAt(i);
            if(inString){if(escaped)escaped=false;else if(c=='\\')escaped=true;else if(c=='"')inString=false;continue;}
            if(c=='"'){inString=true;continue;}
            if(c=='['||c=='{')stack.push(i);
            else if(c==']'||c=='}') {
                if(stack.isEmpty())return -1;
                int open=stack.peek();char opener=input.charAt(open);
                if((c==']'&&opener=='[')||(c=='}'&&opener=='{'))stack.pop();
                else return opener=='['?open:-1;
            }
        }
        return -1;
    }
    private static Map<String,Object> issue(String type,java.util.List<?> location,String message,Object input) {
        var value=new LinkedHashMap<String,Object>();value.put("type",type);value.put("loc",location);value.put("msg",message);value.put("input",input);return value;
    }
    private static ResponseEntity<byte[]> validationError(Object detail) {
        try {byte[] body=JSON.writeValueAsBytes(Map.of("detail",detail));return ResponseEntity.unprocessableEntity()
            .contentType(MediaType.APPLICATION_JSON).contentLength(body.length).body(body);}
        catch(Exception failure){throw new IllegalStateException(failure);}
    }
    private static ResponseEntity<byte[]> success(Map<String,Object> value) {
        try {byte[] body=JSON.writeValueAsBytes(value);return ResponseEntity.ok().contentType(MediaType.APPLICATION_JSON)
            .contentLength(body.length).body(body);}
        catch(Exception failure){throw new IllegalStateException(failure);}
    }
    private static ResponseEntity<byte[]> methodNotAllowed(String allowed) {
        byte[] body="{\"detail\":\"Method Not Allowed\"}".getBytes(StandardCharsets.UTF_8);
        return ResponseEntity.status(405).header("Allow",allowed).contentType(MediaType.APPLICATION_JSON)
            .contentLength(body.length).body(body);
    }
}
