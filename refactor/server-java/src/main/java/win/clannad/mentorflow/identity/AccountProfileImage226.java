package win.clannad.mentorflow.identity;

import java.awt.AlphaComposite;
import java.awt.Color;
import java.awt.Graphics2D;
import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.util.Iterator;
import javax.imageio.ImageIO;
import javax.imageio.ImageReader;
import javax.imageio.stream.ImageInputStream;

/** Decode a bounded upload and encode a fresh metadata-free square JPEG. */
final class AccountProfileImage226 {
    static final int MAX_UPLOAD_BYTES = 262_144;
    private AccountProfileImage226() {}

    static Normalized normalize(byte[] source, String contentType) {
        if (source == null || source.length == 0 || source.length > MAX_UPLOAD_BYTES)
            throw new IdentityFailure(413, "ACCOUNT_PROFILE_AVATAR_TOO_LARGE", "头像文件不得超过 256 KiB");
        String declared = contentType == null ? "" : contentType.split(";", 2)[0].strip().toLowerCase(java.util.Locale.ROOT);
        if (!declared.equals("image/jpeg") && !declared.equals("image/png"))
            throw invalidImage();
        try (ImageInputStream input = ImageIO.createImageInputStream(new ByteArrayInputStream(source))) {
            if (input == null) throw invalidImage();
            Iterator<ImageReader> readers = ImageIO.getImageReaders(input);
            if (!readers.hasNext()) throw invalidImage();
            ImageReader reader = readers.next();
            try {
                reader.setInput(input, true, true);
                String format = reader.getFormatName().toLowerCase(java.util.Locale.ROOT);
                if (!(format.equals("jpeg") || format.equals("jpg") || format.equals("png"))) throw invalidImage();
                if ((declared.equals("image/jpeg") && !(format.equals("jpeg") || format.equals("jpg")))
                        || (declared.equals("image/png") && !format.equals("png"))) throw invalidImage();
                int width = reader.getWidth(0), height = reader.getHeight(0);
                if (width < 1 || height < 1 || width > 512 || height > 512 || width != height)
                    throw new IdentityFailure(422, "ACCOUNT_PROFILE_AVATAR_DIMENSIONS", "头像必须为不超过 512×512 的正方形图片");
                BufferedImage decoded = reader.read(0);
                if (decoded == null || decoded.getWidth() != width || decoded.getHeight() != height) throw invalidImage();
                BufferedImage clean = new BufferedImage(width, height, BufferedImage.TYPE_INT_RGB);
                Graphics2D graphics = clean.createGraphics();
                try {
                    graphics.setComposite(AlphaComposite.SrcOver);
                    graphics.setColor(Color.WHITE);
                    graphics.fillRect(0, 0, width, height);
                    graphics.drawImage(decoded, 0, 0, null);
                } finally {
                    graphics.dispose();
                    decoded.flush();
                }
                byte[] encoded = encode(clean);
                clean.flush();
                return new Normalized(encoded, width, height);
            } finally {
                reader.dispose();
            }
        } catch (IdentityFailure failure) {
            throw failure;
        } catch (Exception failure) {
            throw invalidImage();
        }
    }

    private static byte[] encode(BufferedImage image) throws Exception {
        Iterator<javax.imageio.ImageWriter> writers = ImageIO.getImageWritersByFormatName("jpeg");
        if (!writers.hasNext()) throw new IdentityFailure(503, "ACCOUNT_PROFILE_IMAGE_UNAVAILABLE", "头像处理暂不可用");
        javax.imageio.ImageWriter writer = writers.next();
        try {
            for (float quality = 0.84f; quality >= 0.40f; quality -= 0.08f) {
                ByteArrayOutputStream bytes = new ByteArrayOutputStream();
                try (javax.imageio.stream.ImageOutputStream output = ImageIO.createImageOutputStream(bytes)) {
                    writer.setOutput(output);
                    javax.imageio.ImageWriteParam params = writer.getDefaultWriteParam();
                    if (params.canWriteCompressed()) {
                        params.setCompressionMode(javax.imageio.ImageWriteParam.MODE_EXPLICIT);
                        params.setCompressionQuality(quality);
                    }
                    writer.write(null, new javax.imageio.IIOImage(image, null, null), params);
                    output.flush();
                }
                byte[] result = bytes.toByteArray();
                writer.reset();
                if (result.length <= MAX_UPLOAD_BYTES) return result;
            }
        } finally {
            writer.dispose();
        }
        throw new IdentityFailure(413, "ACCOUNT_PROFILE_AVATAR_TOO_LARGE", "处理后的头像仍超过 256 KiB");
    }

    private static IdentityFailure invalidImage() {
        return new IdentityFailure(415, "ACCOUNT_PROFILE_AVATAR_FORMAT", "头像只支持有效的 JPEG 或 PNG 图片");
    }

    record Normalized(byte[] bytes, int width, int height) {}
}
