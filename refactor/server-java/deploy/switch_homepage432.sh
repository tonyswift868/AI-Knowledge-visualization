#!/bin/sh
set -eu

edge_conf=${EDGE_CONF:-/opt/mentorflow/artifacts/r1-a-functional-20260917/edge.conf}
state_dir=${STATE_DIR:-/opt/mentorflow/state/homepage432}
container=${EDGE_CONTAINER:-mentorflow-e1-preview-edge-1}
docker_bin=${DOCKER_BIN:-docker}
sha256sum_bin=${SHA256SUM_BIN:-sha256sum}
stat_bin=${STAT_BIN:-stat}
container_conf=${CONTAINER_EDGE_CONF:-/etc/nginx/nginx.conf}
static_root=${STATIC_ROOT:-/opt/mentorflow/update-static/e108/site}
backup=$state_dir/edge.conf.before
candidate=${CANDIDATE:-$state_dir/edge.conf.candidate}
index_src=${INDEX_SRC:-$state_dir/index.html}
image_src=${IMAGE_SRC:-$state_dir/plan-calendar.png}
index_dst=$static_root/index.html
image_dst=$static_root/images/plan-calendar.png
internal_base=http://mentorflow-e108-update-static:8081/mentorflow/updates/e108/site

hash_file() { "$sha256sum_bin" "$1" | awk '{print $1}'; }
inode_file() { "$stat_bin" -c '%i' "$1"; }
same_inode() {
  [ "$(inode_file "$edge_conf")" = "$before_inode" ] || {
    printf '%s\n' 'HOMEPAGE432_ERROR active_config_inode_changed' >&2
    return 1
  }
}
test_nginx() { "$docker_bin" exec "$container" nginx -t; }
reload_nginx() { "$docker_bin" exec "$container" nginx -s reload; }
check_route() {
  "$docker_bin" exec "$container" sh -c \
    "grep -Fq 'rewrite ^/\$ /mentorflow/updates/e108/site/index.html break;' '$container_conf' && grep -Fq 'rewrite ^/images/(.*)\$ /mentorflow/updates/e108/site/images/\$1 break;' '$container_conf'"
}
check_placeholder() {
  "$docker_bin" exec "$container" sh -c \
    "grep -Fq '# Placeholder until the product homepage is built.' '$container_conf' && grep -Fq 'return 200' '$container_conf'"
}
write_backup_in_place() {
  cat "$backup" > "$edge_conf" || return 1
  same_inode
}
rollback_after_failure() {
  printf '%s\n' 'HOMEPAGE432_ROLLBACK starting' >&2
  write_backup_in_place || return 1
  test_nginx || return 1
  reload_nginx || return 1
  printf '%s\n' 'HOMEPAGE432_ROLLBACK_OK' >&2
}
verify_internal_file() {
  expected=$1
  url=$2
  actual=$("$docker_bin" exec "$container" sh -c "wget -qO- '$url' | sha256sum | awk '{print \$1}'")
  [ "$actual" = "$expected" ] || {
    printf 'HOMEPAGE432_ERROR internal_hash_mismatch url=%s\n' "$url" >&2
    return 1
  }
}
cleanup_unverified_site() {
  rm -rf "$static_root"
}

case "${1:-}" in
  apply)
    [ "$#" -eq 4 ] || { printf 'usage: %s apply EXPECTED_EDGE_SHA256 EXPECTED_INDEX_SHA256 EXPECTED_IMAGE_SHA256\n' "$0" >&2; exit 2; }
    expected_edge_sha=$2
    expected_index_sha=$3
    expected_image_sha=$4
    [ -f "$edge_conf" ] && [ -f "$candidate" ] && [ -f "$index_src" ] && [ -f "$image_src" ] || {
      printf '%s\n' 'HOMEPAGE432_ERROR missing_config_candidate_or_site_file' >&2; exit 2;
    }
    actual_edge_sha=$(hash_file "$edge_conf")
    [ "$actual_edge_sha" = "$expected_edge_sha" ] || {
      printf '%s\n' 'HOMEPAGE432_REFUSED active_sha256_mismatch' >&2; exit 3;
    }
    [ "$(hash_file "$index_src")" = "$expected_index_sha" ] || {
      printf '%s\n' 'HOMEPAGE432_REFUSED index_sha256_mismatch' >&2; exit 3;
    }
    [ "$(hash_file "$image_src")" = "$expected_image_sha" ] || {
      printf '%s\n' 'HOMEPAGE432_REFUSED image_sha256_mismatch' >&2; exit 3;
    }
    [ ! -e "$static_root" ] && [ ! -L "$static_root" ] || {
      printf '%s\n' 'HOMEPAGE432_REFUSED site_directory_already_exists' >&2; exit 3;
    }
    [ ! -e "$backup" ] && [ ! -L "$backup" ] || {
      printf '%s\n' 'HOMEPAGE432_REFUSED backup_already_exists' >&2; exit 3;
    }
    mkdir -p "$static_root/images"
    ref_file=${REFERENCE_FILE:-/opt/mentorflow/update-static/e108/stable/stable.json}
    [ -f "$ref_file" ] || { printf '%s\n' 'HOMEPAGE432_ERROR reference_file_missing' >&2; exit 1; }
    cp "$index_src" "$index_dst"
    cp "$image_src" "$image_dst"
    trap cleanup_unverified_site EXIT
    chown --reference="$ref_file" "$index_dst" "$image_dst"
    chmod --reference="$ref_file" "$index_dst" "$image_dst"
    if ! verify_internal_file "$expected_index_sha" "$internal_base/index.html"; then
      exit 1
    fi
    if ! verify_internal_file "$expected_image_sha" "$internal_base/images/plan-calendar.png"; then
      exit 1
    fi
    trap - EXIT
    before_inode=$(inode_file "$edge_conf")
    mkdir -p "$state_dir"
    cat "$edge_conf" > "$backup"
    [ "$(hash_file "$backup")" = "$expected_edge_sha" ] || { printf '%s\n' 'HOMEPAGE432_ERROR backup_sha256_mismatch' >&2; exit 1; }
    printf 'HOMEPAGE432_SITE=%s\nHOMEPAGE432_BACKUP=%s\nHOMEPAGE432_INODE_BEFORE=%s\n' "$static_root" "$backup" "$before_inode"
    if ! cat "$candidate" > "$edge_conf" || ! same_inode; then
      rollback_after_failure || printf '%s\n' 'HOMEPAGE432_ROLLBACK_FAILED inspect_server_immediately' >&2
      exit 1
    fi
    printf 'HOMEPAGE432_INODE_AFTER=%s\n' "$(inode_file "$edge_conf")"
    if ! test_nginx; then
      write_backup_in_place
      printf '%s\n' 'HOMEPAGE432_RESTORED_NO_RELOAD' >&2
      exit 1
    fi
    if ! reload_nginx || ! check_route; then
      rollback_after_failure || printf '%s\n' 'HOMEPAGE432_ROLLBACK_FAILED inspect_server_immediately' >&2
      exit 1
    fi
    printf '%s\n' 'HOMEPAGE432_APPLY_OK'
    ;;
  rollback)
    [ "$#" -eq 1 ] || { printf 'usage: %s rollback\n' "$0" >&2; exit 2; }
    [ -f "$edge_conf" ] && [ -f "$backup" ] || { printf '%s\n' 'HOMEPAGE432_ERROR missing_active_or_backup' >&2; exit 2; }
    before_inode=$(inode_file "$edge_conf")
    write_backup_in_place
    printf 'HOMEPAGE432_ROLLBACK_INODE_BEFORE=%s\nHOMEPAGE432_ROLLBACK_INODE_AFTER=%s\n' "$before_inode" "$(inode_file "$edge_conf")"
    test_nginx
    reload_nginx
    check_placeholder
    printf '%s\n' 'HOMEPAGE432_MANUAL_ROLLBACK_OK'
    ;;
  *)
    printf 'usage: %s apply EXPECTED_EDGE_SHA256 EXPECTED_INDEX_SHA256 EXPECTED_IMAGE_SHA256 | rollback\n' "$0" >&2
    exit 2
    ;;
esac
