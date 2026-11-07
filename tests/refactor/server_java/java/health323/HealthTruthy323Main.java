package health323;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.BooleanNode;
import com.fasterxml.jackson.databind.node.DecimalNode;
import com.fasterxml.jackson.databind.node.DoubleNode;
import com.fasterxml.jackson.databind.node.IntNode;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.fasterxml.jackson.databind.node.MissingNode;
import com.fasterxml.jackson.databind.node.NullNode;
import com.fasterxml.jackson.databind.node.TextNode;
import java.lang.reflect.Method;
import java.math.BigDecimal;
import win.clannad.mentorflow.health.DefaultHealthProbe;

/** Offline regression for JSON truthiness used by the Java readiness probe. */
public final class HealthTruthy323Main {
    private static final Method PYTHON_TRUTHY = truthyMethod();

    private HealthTruthy323Main() {}

    public static void main(String[] args) throws Exception {
        check(null, false, "Java null");
        check(MissingNode.getInstance(), false, "missing node");
        check(NullNode.getInstance(), false, "JSON null");
        check(TextNode.valueOf(""), false, "empty string");
        check(TextNode.valueOf(" "), true, "whitespace string");
        check(TextNode.valueOf("issuer"), true, "nonempty string");
        check(BooleanNode.FALSE, false, "false");
        check(BooleanNode.TRUE, true, "true");
        check(IntNode.valueOf(0), false, "integer zero");
        check(IntNode.valueOf(3), true, "positive integer");
        check(IntNode.valueOf(-3), true, "negative integer");
        check(DecimalNode.valueOf(BigDecimal.ZERO), false, "decimal zero");
        check(DecimalNode.valueOf(new BigDecimal("0.25")), true, "nonzero decimal");
        check(DoubleNode.valueOf(0.0), false, "double positive zero");
        check(DoubleNode.valueOf(-0.0), false, "double negative zero");
        check(DoubleNode.valueOf(-0.25), true, "negative nonzero double");

        JsonNodeFactory nodes = JsonNodeFactory.instance;
        check(nodes.arrayNode(), false, "empty array");
        check(nodes.objectNode(), false, "empty object");
        check(nodes.arrayNode().addNull(), true, "array containing null");
        check(nodes.arrayNode().add(false), true, "array containing false");
        check(nodes.objectNode().putNull("value"), true, "object containing null");
        check(nodes.objectNode().put("value", false), true, "object containing false");

        System.out.println("PASS: 22 Python-compatible health truthiness cases");
    }

    private static Method truthyMethod() {
        try {
            Method method = DefaultHealthProbe.class.getDeclaredMethod("pythonTruthy", JsonNode.class);
            method.setAccessible(true);
            return method;
        } catch (ReflectiveOperationException error) {
            throw new ExceptionInInitializerError(error);
        }
    }

    private static void check(JsonNode value, boolean expected, String label) throws Exception {
        boolean actual = (boolean) PYTHON_TRUTHY.invoke(null, new Object[] {value});
        if (actual != expected) {
            throw new AssertionError(label + ": expected " + expected + ", got " + actual);
        }
    }
}
