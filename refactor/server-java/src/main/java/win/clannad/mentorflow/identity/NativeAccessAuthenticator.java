package win.clannad.mentorflow.identity;

import org.springframework.security.oauth2.jwt.Jwt;

/** Account-owned validation for opaque first-party access credentials. */
public interface NativeAccessAuthenticator {
    String LOGIN_PATH="/api/enterprise/auth/native/login";
    String REFRESH_PATH="/api/enterprise/auth/native/refresh";
    String LOGOUT_PATH="/api/enterprise/auth/native/logout";
    String SESSION_PATH="/api/enterprise/auth/native/session";

    static boolean isSessionEndpoint(String path) {
        return LOGIN_PATH.equals(path)||REFRESH_PATH.equals(path)||LOGOUT_PATH.equals(path)||SESSION_PATH.equals(path);
    }

    /** Validate a native access token and expose its normalized, canonical identity claims. */
    Jwt authenticateAccess(String accessToken);
}
