FROM mentorflow-r2-keycloak:37f9507
ENV KC_DB=postgres KC_HEALTH_ENABLED=true
COPY --chown=1000:0 mentorflow-native-verifier.jar /opt/keycloak/providers/mentorflow-native-verifier.jar
RUN /opt/keycloak/bin/kc.sh build
RUN test -f /opt/keycloak/conf/mentorflow-optimized-v1
