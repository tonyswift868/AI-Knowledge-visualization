FROM maven:3.9.16-eclipse-temurin-21 AS build
WORKDIR /workspace
COPY refactor/server-java/pom.xml ./pom.xml
COPY refactor/server-java/src ./src
RUN mvn -B -DskipTests package

FROM eclipse-temurin:21-jre AS runtime
ARG MF_VERSION=dev
ARG JAR_SHA256=unknown
WORKDIR /app
COPY --from=build /workspace/target/mentorflow-server-0.1.0.jar /app/mentorflow-server.jar
COPY refactor/server-java/deploy/entrypoint.sh /app/entrypoint.sh
RUN chmod 0555 /app/entrypoint.sh
ENV MENTORFLOW_BIND_ADDRESS=0.0.0.0
EXPOSE 8080
LABEL org.opencontainers.image.version=$MF_VERSION \
      org.mentorflow.jar.sha256=$JAR_SHA256
ENTRYPOINT ["/app/entrypoint.sh"]
