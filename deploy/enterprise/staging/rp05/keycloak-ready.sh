#!/bin/sh
set -eu

admin_password="$(cat /run/secrets/keycloak_admin_password)"
test -n "$admin_password" || { echo "Keycloak admin secret is empty" >&2; exit 30; }
export HOME=/tmp/keycloak-ready
mkdir -p "$HOME"

/opt/keycloak/bin/kcadm.sh config credentials \
  --config "$HOME/kcadm.config" \
  --server http://keycloak:8080 \
  --realm master \
  --user "$KC_BOOTSTRAP_ADMIN_USERNAME" \
  --password "$admin_password" >/dev/null

/opt/keycloak/bin/kcadm.sh get realms/mentorflow --config "$HOME/kcadm.config" --fields realm,enabled \
  | grep -q '"realm" : "mentorflow"'
for client_id in mentorflow-api mentorflow-web mentorflow-bff mentorflow-worker; do
  /opt/keycloak/bin/kcadm.sh get clients --config "$HOME/kcadm.config" -r mentorflow -q "clientId=$client_id" --fields clientId,enabled \
    | grep -q "\"clientId\" : \"$client_id\""
done
web_client="$(/opt/keycloak/bin/kcadm.sh get clients --config "$HOME/kcadm.config" -r mentorflow -q clientId=mentorflow-web)"
printf '%s' "$web_client" | grep -Fq '"publicClient" : true'
printf '%s' "$web_client" | grep -Fq "${MENTORFLOW_PUBLIC_ORIGIN}/api/enterprise/auth/callback"
printf '%s' "$web_client" | grep -Fq "${MENTORFLOW_PUBLIC_ORIGIN}"
echo '{"schema_version":"mentorflow.rp05-keycloak-ready.v1","status":"PASS"}'
