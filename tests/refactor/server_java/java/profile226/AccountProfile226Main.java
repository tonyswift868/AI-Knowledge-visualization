package win.clannad.mentorflow.identity;

import java.awt.Color;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.util.Arrays;
import javax.imageio.ImageIO;

/** Local CPU-only check for the JDK image normalizer and exact account route boundary. */
public final class AccountProfile226Main {
    private AccountProfile226Main() {}

    public static void main(String[] args) throws Exception {
        BufferedImage input = new BufferedImage(256, 256, BufferedImage.TYPE_INT_ARGB);
        for (int y = 0; y < 256; y++) for (int x = 0; x < 256; x++)
            input.setRGB(x, y, ((x + y) % 3 == 0 ? 0x00 : 0xff) << 24 | (x << 16) | (y << 8) | 0x33);
        ByteArrayOutputStream source = new ByteArrayOutputStream();
        ImageIO.write(input, "png", source);
        byte[] png = source.toByteArray();
        AccountProfileImage226.Normalized normalized = AccountProfileImage226.normalize(png, "image/png");
        byte[] jpeg = normalized.bytes();
        if (normalized.width() != 256 || normalized.height() != 256) throw new AssertionError("dimensions");
        if (jpeg.length < 4 || (jpeg[0] & 0xff) != 0xff || (jpeg[1] & 0xff) != 0xd8
                || (jpeg[jpeg.length - 2] & 0xff) != 0xff || (jpeg[jpeg.length - 1] & 0xff) != 0xd9)
            throw new AssertionError("not a complete JPEG");
        BufferedImage decoded = ImageIO.read(new java.io.ByteArrayInputStream(jpeg));
        if (decoded == null || decoded.getWidth() != 256 || decoded.getHeight() != 256)
            throw new AssertionError("normalized JPEG decode");
        String segments = new String(jpeg, java.nio.charset.StandardCharsets.ISO_8859_1);
        if (segments.contains("Exif\u0000\u0000") || segments.contains("http://ns.adobe.com/xap/1.0/"))
            throw new AssertionError("metadata marker survived re-encoding");
        expectFailure(() -> AccountProfileImage226.normalize(png, "image/jpeg"));
        expectFailure(() -> AccountProfileImage226.normalize(new byte[AccountProfileImage226.MAX_UPLOAD_BYTES + 1], "image/png"));
        BufferedImage rectangle = new BufferedImage(200, 100, BufferedImage.TYPE_INT_RGB);
        ByteArrayOutputStream rectangleBytes = new ByteArrayOutputStream();
        ImageIO.write(rectangle, "png", rectangleBytes);
        expectFailure(() -> AccountProfileImage226.normalize(rectangleBytes.toByteArray(), "image/png"));
        BufferedImage tooLarge = new BufferedImage(513, 513, BufferedImage.TYPE_INT_RGB);
        ByteArrayOutputStream tooLargeBytes = new ByteArrayOutputStream();
        ImageIO.write(tooLarge, "png", tooLargeBytes);
        expectFailure(() -> AccountProfileImage226.normalize(tooLargeBytes.toByteArray(), "image/png"));
        if (!AccountRoutes146.isAccountProfile("/api/enterprise/account/profile")
                || !AccountRoutes146.isAccountProfile("/api/enterprise/account/profile/avatar")
                || AccountRoutes146.isAccountProfile("/api/enterprise/account/profile/other")
                || AccountRoutes146.isPublic("/api/enterprise/account/profile"))
            throw new AssertionError("account-only route boundary");
        if (AccountProfileRepository226.scope("https://profile226.invalid", "alice").length() != 64)
            throw new AssertionError("account scope digest shape");
        if (!Arrays.equals(png, png.clone())) throw new AssertionError("test fixture");
        System.out.println("PROFILE226_JDK_IMAGE_AND_ROUTE_OK bytes=" + jpeg.length);
    }

    private static void expectFailure(Runnable action) {
        try { action.run(); }
        catch (IdentityFailure expected) { return; }
        throw new AssertionError("invalid image input was accepted");
    }
}
