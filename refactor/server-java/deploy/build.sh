#!/bin/sh
set -eu

usage() { echo "usage: $0 VERSION" >&2; exit 2; }
[ "$#" -eq 1 ] || usage
version=$1
case "$version" in *[!A-Za-z0-9_.-]*|'') usage ;; esac
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/../../.." && pwd)
dockerfile=$repo/refactor/server-java/Dockerfile
builder="mentorflow-java-build:${version}"
image="mentorflow-java-server:${version}"
temp=$(mktemp -d "${TMPDIR:-/tmp}/mf-java-build.XXXXXX")
container=
cleanup() {
  if [ -n "$container" ]; then docker rm "$container" >/dev/null 2>&1 || true; fi
  docker image rm "$builder" >/dev/null 2>&1 || true
  rm -rf "$temp"
}
trap cleanup EXIT HUP INT TERM

validate_sha256() {
  value=$1
  [ "${#value}" -eq 64 ] || { echo "invalid SHA-256 output" >&2; return 1; }
  case "$value" in *[!0123456789abcdef]*) echo "invalid SHA-256 output" >&2; return 1 ;; esac
}

# Build from source in the pinned Maven/Temurin builder, then obtain the exact
# packaged JAR hash for the production image label.
docker build --target build -f "$dockerfile" -t "$builder" "$repo"
container=$(docker create "$builder")
docker cp "$container:/workspace/target/mentorflow-server-0.1.0.jar" "$temp/mentorflow-server.jar"
jar_digest=$(sha256sum "$temp/mentorflow-server.jar")
jar_sha=${jar_digest%% *}
validate_sha256 "$jar_sha"
docker rm "$container" >/dev/null
container=
docker build -f "$dockerfile" \
  --build-arg "MF_VERSION=$version" \
  --build-arg "JAR_SHA256=$jar_sha" \
  --label "org.opencontainers.image.version=$version" \
  --label "org.mentorflow.jar.sha256=$jar_sha" \
  -t "$image" "$repo"

# Verify the final image, rather than trusting that its second Maven build
# reused the same artifact as the hash-producing build.
container=$(docker create "$image")
docker cp "$container:/app/mentorflow-server.jar" "$temp/final-mentorflow-server.jar"
final_digest=$(sha256sum "$temp/final-mentorflow-server.jar")
final_sha=${final_digest%% *}
validate_sha256 "$final_sha"
docker rm "$container" >/dev/null
container=
if [ "$final_sha" != "$jar_sha" ]; then
  docker image rm "$image" >/dev/null 2>&1 || true
  echo "final image JAR hash mismatch: expected $jar_sha, got $final_sha" >&2
  exit 1
fi

env_file=$here/.env
touch "$env_file"
awk '!/^MF_JAVA_VERSION=/' "$env_file" > "$temp/env"
printf 'MF_JAVA_VERSION=%s\n' "$version" >> "$temp/env"
cat "$temp/env" > "$env_file"
printf 'Built %s (JAR SHA-256 %s)\n' "$image" "$jar_sha"
