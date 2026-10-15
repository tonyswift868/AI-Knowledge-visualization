FROM maven:3.9.16-eclipse-temurin-21 AS build
WORKDIR /workspace
COPY refactor/server-java/pom.xml ./pom.xml
COPY refactor/server-java/src ./src
RUN mvn -B -DskipTests package

FROM eclipse-temurin:21-jre
WORKDIR /app
COPY --from=build /workspace/target/mentorflow-server-0.1.0.jar /app/mentorflow-server.jar
ENV MENTORFLOW_BIND_ADDRESS=0.0.0.0
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/mentorflow-server.jar"]
