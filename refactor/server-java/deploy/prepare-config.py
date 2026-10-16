#!/usr/bin/env python3
"""Create local, ignored Compose inputs from the currently running backend."""
import json
import os
from pathlib import Path
import subprocess
import sys


def fail(message):
    raise SystemExit(message)


def yaml_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def dotenv_quote(value):
    # Compose env_file single-quoted values preserve whitespace and # literally.
    return "'" + value.replace("'", "\\'") + "'"


def inspect(name):
    docker_bin = os.environ.get("DOCKER_BIN", "docker")
    raw = subprocess.check_output([docker_bin, "inspect", name], text=True)
    data = json.loads(raw)
    if not data:
        fail(f"container not found: {name}")
    return data[0]


def main():
    os.umask(0o077)
    if len(sys.argv) != 3:
        fail(f"usage: {sys.argv[0]} BACKEND_CONTAINER TRUSTSTORE_DIR")
    backend_name, trust_dir = sys.argv[1:]
    backend = inspect(backend_name)
    proxy_name = os.environ.get("MF_OIDC_PROXY_CONTAINER", "mentorflow-e1-preview-oidc-loopback-proxy-1")
    proxy = inspect(proxy_name)

    env = {}
    for row in backend.get("Config", {}).get("Env", []) or []:
        key, sep, value = row.partition("=")
        if not sep:
            continue
        if (key.startswith("MENTORFLOW_") or key == "WEB_CONCURRENCY") and key not in {
            "MENTORFLOW_DATABASE_URL", "MENTORFLOW_MIGRATION_DATABASE_URL", "MENTORFLOW_OTEL_ENABLED"
        }:
            env[key] = value
    env.setdefault("MENTORFLOW_DEPLOYMENT_MODE", "ENTERPRISE_SERVER")
    env.setdefault("MENTORFLOW_IDENTITY_SESSION_TTL_SECONDS", "0")
    env.setdefault("MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS", "1")
    env["MENTORFLOW_CATALOG_DIR"] = "/run/mentorflow/catalog"
    env["PORT"] = "8080"
    env["JAVA_TOOL_OPTIONS"] = (
        "-Djavax.net.ssl.trustStore=/run/mentorflow/trust/trust.p12 "
        "-Djavax.net.ssl.trustStorePassword=changeit -Djavax.net.ssl.trustStoreType=PKCS12"
    )

    mounts = []
    config_source = None
    for mount in backend.get("Mounts", []) or []:
        dest = mount.get("Destination", "")
        if not dest or dest == "/var/lib/mentorflow" or dest == "/opt/mentorflow/rp05/runtime-entrypoint.py":
            continue
        source = mount.get("Source")
        if not source:
            fail(f"mount source missing for {dest}")
        mounts.append((source, dest))
    for mount in proxy.get("Mounts", []) or []:
        if mount.get("Destination") == "/etc/nginx/nginx.conf":
            config_source = mount.get("Source")
            break
    if not config_source:
        fail(f"{proxy_name} has no /etc/nginx/nginx.conf bind mount")
    proxy_image = proxy.get("Config", {}).get("Image")
    if not proxy_image:
        fail(f"cannot determine image for {proxy_name}")

    out = Path(__file__).resolve().parent
    with (out / ".java.env").open("w", encoding="utf-8", newline="\n") as stream:
        for key in sorted(env):
            stream.write(f"{key}={dotenv_quote(env[key])}\n")
    env_file = out / ".env"
    values = {
        "MF_JAVA_DEPLOY_DIR": str(out),
        "MF_JAVA_TRUSTSTORE_DIR": str(Path(trust_dir).resolve()),
        "MF_OIDC_PROXY_IMAGE": proxy_image,
        "MF_OIDC_PROXY_CONFIG": config_source,
    }
    previous = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep:
                previous[key] = value
    previous.update(values)
    env_file.write_text("".join(f"{k}={v}\n" for k, v in sorted(previous.items())), encoding="utf-8")

    lines = ["services:", "  java-backend:", "    volumes:"]
    for source, dest in mounts:
        lines.extend([
            "      - type: bind",
            f"        source: {yaml_quote(source)}",
            f"        target: {yaml_quote(dest)}",
            "        read_only: true",
        ])
    existing_user = backend.get("Config", {}).get("User", "")
    if existing_user:
        lines.extend(["    user: " + yaml_quote(existing_user)])
    (out / "compose.generated.yml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Prepared Compose config from {backend_name}; copied {len(mounts)} read-only mounts.")
    print(f"OIDC proxy image: {proxy_image}; config source: {config_source}")


if __name__ == "__main__":
    main()
