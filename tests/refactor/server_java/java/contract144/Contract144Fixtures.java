package contract144;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.LinkedHashMap;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@Configuration(proxyBeanMethods=false)
public class Contract144Fixtures {
    @Bean Contract144ContextController context144(){return new Contract144ContextController();}
}

@RestController
class Contract144ContextController {
    @GetMapping("/api/enterprise/contract144/context")
    ResponseEntity<byte[]> context(jakarta.servlet.http.HttpServletRequest request) throws Exception {
        var result=new LinkedHashMap<String,Object>();
        result.put("actor",request.getAttribute("actor_context"));
        result.put("tenant",request.getAttribute("tenant_context"));
        byte[] body=new ObjectMapper().writeValueAsBytes(result);
        return ResponseEntity.ok().contentType(MediaType.APPLICATION_JSON).contentLength(body.length).body(body);
    }
}
