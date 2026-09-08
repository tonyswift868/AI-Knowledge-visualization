"""Build a static signed file-release feed; never deploy or alter the input package.

The consumer is desktop.update_protocol / MatureUpdateService. There is no new
delta format: changed files and full fallback reference the same SHA-256 objects.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key, Encoding, PublicFormat
from cryptography.exceptions import InvalidSignature

from desktop.update_protocol import (
    APP_ID, MANIFEST_PATH, canonical, digest, safe_path, strict_json,
    verify_release, version_key, witness_version,
)


def regular(path: Path, *, directory=False):
    """Refuse aliases including Windows junctions before touching release paths."""
    for item in (path, *path.parents):
        if not item.exists() and not item.is_symlink():
            continue
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('reparse/symlink path refused')
    if path.exists() and not (path.is_dir() if directory else path.is_file()):
        raise ValueError('wrong filesystem entry type')


def write_new(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def atomic(path: Path, data: bytes):
    regular(path)
    fd, name = tempfile.mkstemp(prefix='.publish-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def publishing_lock(root: Path):
    lock = root / '.release-build.lock'
    regular(lock)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError('release build lock exists; do not remove until its owner is confirmed stopped') from exc
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        lock.unlink()


def file_hash(path: Path):
    regular(path)
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(package: Path, version: str, objects: Path | None = None):
    regular(package, directory=True)
    if not package.is_dir():
        raise ValueError('packaged target directory missing')
    regular(package / 'mentorflow-version.json')
    version_doc = strict_json((package / 'mentorflow-version.json').read_bytes())
    if (version_doc.get('schema') != 'mentorflow.version.v1' or
            version_doc.get('product') != 'MentorFlow' or version_doc.get('version') != version):
        raise ValueError('packaged version must match requested version; rebuild the package first')
    rows = []
    seen = set()
    for folder, directories, files in os.walk(package, followlinks=False):
        for name in directories + files:
            item = Path(folder) / name
            relative = item.relative_to(package).as_posix()
            regular(item, directory=name in directories)
            safe_path(relative)
            bundled_ca = relative in ('_internal/certifi/cacert.pem', 'UserRuntime/_internal/certifi/cacert.pem')
            if (name.casefold() in {'.git', '.env', '__pycache__'} or
                    item.suffix.lower() in {'.key', '.pfx', '.p12'} or
                    item.suffix.lower() == '.pem' and (not bundled_ca or name in directories)):
                raise ValueError('source/cache/private-key material in packaged target')
        for name in sorted(files):
            path = Path(folder) / name
            relative = path.relative_to(package).as_posix()
            if relative.casefold() == MANIFEST_PATH.casefold():
                if relative != MANIFEST_PATH:
                    raise ValueError('noncanonical package manifest path')
                continue  # regenerated from the exact output bytes, never self-referential
            if relative.casefold() in seen:
                raise ValueError('case-colliding target paths')
            seen.add(relative.casefold())
            if len(seen) > 50000:
                raise ValueError('package has too many files')
            # Windows source leases block in-place writes while hashing/copying.
            if os.name == 'nt':
                from scripts.update_source_reader import open_source
                source = open_source(path)
            else:
                source = path.open('rb')
            pending = objects / 'incoming' if objects else None
            hasher, size = hashlib.sha256(), 0
            with source as stream:
                if path.suffix.lower() == '.pem':
                    # Public certifi CA bundles are required runtime assets,
                    # not signing keys. Validate while holding the SAME source
                    # lease used to hash/copy, preventing a validation/hash race.
                    validate_public_ca_bundle(stream.read(1024 * 1024 + 1))
                    stream.seek(0)
                destination = pending.open('xb') if pending else None
                try:
                    while block := stream.read(1024 * 1024):
                        hasher.update(block)
                        size += len(block)
                        if destination:
                            destination.write(block)
                finally:
                    if destination:
                        destination.flush()
                        os.fsync(destination.fileno())
                        destination.close()
            sha = hasher.hexdigest()
            if pending:
                final = objects / sha
                if final.exists():
                    pending.unlink()
                else:
                    pending.rename(final)
            rows.append(dict(path=relative, size=size, sha256=sha))
    if not any(row['path'] == 'MentorFlow.exe' for row in rows):
        raise ValueError('MentorFlow.exe missing or incorrectly cased')
    manifest = dict(schema='mentorflow.windows-package-manifest.v1', product='MentorFlow',
                    version=version, executable='MentorFlow.exe', files=sorted(rows, key=lambda r: r['path']))
    raw = canonical(manifest)
    if objects and not (objects / digest(raw)).exists():
        write_new(objects / digest(raw), raw)
    return manifest, {r['path']: r for r in rows} | {
        MANIFEST_PATH: dict(path=MANIFEST_PATH, size=len(raw), sha256=digest(raw))}


def validate_public_ca_bundle(data):
    from cryptography import x509
    if len(data) > 1024 * 1024 or b'PRIVATE KEY' in data.upper():
        raise ValueError('private/oversized content in public CA bundle')
    clean = b'\n'.join(line for line in data.splitlines() if not line.lstrip().startswith(b'#'))
    grammar = rb'\s*(?:-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----\s*)+'
    if not re.fullmatch(grammar, clean):
        raise ValueError('non-certificate content in public CA bundle')
    try:
        certificates = x509.load_pem_x509_certificates(clean)
    except ValueError as exc:
        raise ValueError('invalid public CA certificate') from exc
    if not certificates: raise ValueError('empty public CA bundle')


def origin_value(value: str, development: bool):
    parsed = urlsplit(value)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment or
            not parsed.hostname or parsed.path.rstrip('/') or
            (parsed.scheme != 'https' and not (development and parsed.scheme == 'http' and
                                               parsed.hostname in ('127.0.0.1', '::1')))):
        raise ValueError('origin must be an HTTPS origin (literal-loopback HTTP only with development signing)')
    _ = parsed.port
    return value.rstrip('/')


def publication_origin(args):
    """A reusable external test key is needed by the compiled BASE and TARGET.

    This explicit integration mode is restricted to a compiled UAT identity and
    literal loopback. It cannot publish a production identity or claim platform
    code signing. Normal external-key publications continue to require HTTPS.
    """
    uat = getattr(args, 'uat_loopback', False)
    if uat:
        if (not APP_ID.startswith('MentorFlow.UAT.') or not args.signing_key or
                args.development_signing or args.certificate_thumbprint):
            raise ValueError('loopback UAT requires a compiled UAT identity and an external test key only')
        parsed = urlsplit(args.origin)
        if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', '::1'):
            raise ValueError('loopback UAT requires a literal-loopback HTTP origin')
    return origin_value(args.origin, args.development_signing or uat)


def load_key(args):
    if args.development_signing:
        if args.signing_key:
            raise ValueError('development signing and signing key are mutually exclusive')
        return Ed25519PrivateKey.generate(), 'ephemeral-development', 'DEVELOPMENT_ONLY'
    if not args.signing_key:
        raise ValueError('external Ed25519 PEM signing key required; no implicit test key')
    key_path = Path(os.path.abspath(args.signing_key))
    for root in (args.output, args.target, Path(__file__).absolute().parent.parent):
        if key_path == root or key_path.is_relative_to(root):
            raise ValueError('private signing key must be outside output, repository and packaged target')
    regular(key_path)
    password = os.environ.get(args.key_password_env) if args.key_password_env else None
    try:
        key = load_pem_private_key(key_path.read_bytes(), password.encode() if password else None)
    except (ValueError, TypeError) as exc:
        raise ValueError('cannot load external signing key') from exc
    if not isinstance(key, Ed25519PrivateKey) or not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', args.key_id or ''):
        raise ValueError('Ed25519 private key and explicit safe key ID required')
    return key, args.key_id, 'EXTERNAL_KEY'


def read_previous(path: Path, keys: dict, channel: str):
    regular(path)
    if not path.exists():
        return None
    raw = path.read_bytes()
    witness_version(raw, keys, channel=channel)
    signed = strict_json(raw)['signed']
    return verify_release(raw, keys, current='0.0.0', channel=channel,
                          now=datetime.fromisoformat(signed['issued_at']))


def verify_output(root: Path, release):
    for row in release.rows.values():
        path = root / 'objects' / row['sha256']
        regular(path)
        if path.stat().st_size != row['size'] or file_hash(path) != row['sha256']:
            raise ValueError('published immutable object corruption')


def release_documents(release, receipt, key, public):
    signed = release.signed
    documents = {'release.json': release.envelope, 'file-manifest.json': release.manifest_bytes,
                 'full-assets.json': canonical(signed['full_release']),
                 'incremental-assets.json': canonical(dict(base_versions=signed['base_versions'],
                     assets=[release.assets[p] for p in signed['incremental_assets']], deleted_paths=receipt['deleted_paths'])),
                 'release-notes.txt': signed['release_notes'].encode('utf-8'), 'build-receipt.json': canonical(receipt),
                 'build-receipt.sig': base64.b64encode(key.sign(canonical(receipt)))}
    if receipt['METADATA_SIGNING'] == 'DEVELOPMENT_ONLY':
        documents['DEVELOPMENT-ONLY-public-key.json'] = canonical(dict(key_id=signed['key_id'],
            public_key=base64.b64encode(public).decode(), trusted_by_production=False, private_key_persisted=False))
    checksums = [f'{digest(data)}  {name}' for name, data in sorted(documents.items())]
    checksums += [f'{sha}  ../../objects/{sha}' for sha in sorted({r['sha256'] for r in release.rows.values()})]
    documents['SHA256SUMS'] = ('\n'.join(checksums) + '\n').encode()
    return documents


def build(args):
    for name in ('target', 'output'):
        setattr(args, name, Path(os.path.abspath(getattr(args, name))))
    version_key(args.version)
    version_key(args.min_supported_version)
    version_key(args.min_updater_version)
    if version_key(args.min_supported_version) > version_key(args.version):
        raise ValueError('minimum supported version cannot exceed target')
    if args.channel == 'stable' and not version_key(args.version)[3]:
        raise ValueError('stable channel refuses prerelease')
    if args.target == args.output or args.target.is_relative_to(args.output) or args.output.is_relative_to(args.target):
        raise ValueError('input and output directories must not overlap')
    for sha in (args.source_sha, args.tree_sha):
        if not re.fullmatch('[0-9a-f]{40}', sha or ''):
            raise ValueError('exact packaged source SHA and tree SHA required')
    if not 1 <= args.expiry_days <= 31:
        raise ValueError('expiry days must be 1..31')
    origin = publication_origin(args)
    notes = args.notes.read_text(encoding='utf-8-sig')
    if len(notes.encode('utf-8')) > 65536:
        raise ValueError('release notes exceed 64 KiB')
    regular(args.output, directory=True)
    args.output.mkdir(parents=True, exist_ok=True)
    key, key_id, signing_mode = load_key(args)
    if getattr(args, 'uat_loopback', False): signing_mode = 'EXTERNAL_UAT_ONLY'
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    keys = {key_id: public}
    with publishing_lock(args.output):
        destination = args.output / args.version / args.channel
        regular(destination, directory=True)
        state_dir = args.output / '.release-state'
        regular(state_dir, directory=True)
        state_dir.mkdir(exist_ok=True)
        index = args.output / (args.channel + '.json')
        state = state_dir / (args.channel + '.json')
        previous = [r for r in (read_previous(index, keys, args.channel), read_previous(state, keys, args.channel)) if r]
        highest = max((r.signed['metadata_version'] for r in previous), default=0)
        if len({digest(canonical(r.signed)) for r in previous if r.signed['metadata_version'] == highest}) > 1:
            raise ValueError('publisher metadata epoch equivocation')
        if any(version_key(r.version) > version_key(args.version) for r in previous):
            raise ValueError('publisher downgrade refused')
        with tempfile.TemporaryDirectory(prefix='.build-', dir=args.output) as temporary:
            scratch = Path(temporary)
            objects = scratch / 'objects'
            objects.mkdir()
            signing = dict(PRODUCTION_SIGNING='NOT_CONFIGURED', signed_files=[])
            target = args.target
            if args.certificate_thumbprint and args.development_signing:
                raise ValueError('development feed cannot claim production Authenticode signing')
            manifest, rows = inventory(target, args.version, None if args.certificate_thumbprint else objects)
            bases, old_rows = [], {}
            if args.base:
                base = Path(os.path.abspath(args.base))
                regular(base, directory=True)
                regular(base / 'mentorflow-version.json')
                base_version = strict_json((base / 'mentorflow-version.json').read_bytes())['version']
                if version_key(base_version) >= version_key(args.version):
                    raise ValueError('incremental base must precede target')
                _, old_rows = inventory(base, base_version)
                bases = [base_version]
            changed = sorted(p for p, row in rows.items() if old_rows.get(p) != row)
            if not args.base:
                if destination.exists():
                    frozen = read_previous(destination / 'release.json', keys, args.channel)
                    # A retry keeps the immutable release's original base policy,
                    # even when the channel pointer now names the target itself.
                    bases, changed = frozen.signed['base_versions'], frozen.signed['incremental_assets']
                elif previous:
                    prior = max(previous, key=lambda r: r.signed['metadata_version'])
                    if version_key(prior.version) < version_key(args.version):
                        bases, old_rows = [prior.version], prior.rows
                        changed = sorted(p for p, row in rows.items() if old_rows.get(p) != row)
            policy = dict(version=args.version, channel=args.channel, app_id=APP_ID, origin=origin,
                          source_sha=args.source_sha, tree_sha=args.tree_sha, manifest=manifest,
                          base_versions=bases, base_policy=old_rows if args.base else 'AUTO', notes=notes,
                          min_supported_version=args.min_supported_version, min_updater_version=args.min_updater_version,
                          mandatory=args.mandatory, mandatory_reason=args.mandatory_reason,
                          expiry_days=args.expiry_days, key_id=key_id, public_key=base64.b64encode(public).decode(),
                          certificate_thumbprint=args.certificate_thumbprint, timestamp_server=args.timestamp_server,
                          signing_mode=signing_mode, rollout_percentage=args.rollout_percentage, rollout_seed=args.rollout_seed)
            fingerprint = digest(canonical(policy))
            if destination.exists():
                for name in ('build-receipt.json', 'build-receipt.sig', 'release.json'):
                    regular(destination / name)
                receipt_bytes = (destination / 'build-receipt.json').read_bytes()
                try:
                    key.public_key().verify(base64.b64decode((destination / 'build-receipt.sig').read_bytes(), validate=True), receipt_bytes)
                except (ValueError, InvalidSignature) as exc:
                    raise ValueError('build receipt signature rejected') from exc
                receipt = strict_json(receipt_bytes)
                if receipt['input_fingerprint'] != fingerprint:
                    raise ValueError('immutable version already exists with different inputs; publish a new version')
                raw = (destination / 'release.json').read_bytes()
                release = verify_release(raw, keys, current='0.0.0', channel=args.channel, highest_seen=highest)
                if (receipt['metadata_sha256'] != digest(raw) or
                        (not args.certificate_thumbprint and release.signed['file_manifest'] != manifest) or
                        release.signed['base_versions'] != bases or
                        (args.metadata_version is not None and args.metadata_version != release.signed['metadata_version'])):
                    raise ValueError('immutable signed publication does not match requested inputs')
                verify_output(args.output, release)
                for name, content in release_documents(release, receipt, key, public).items():
                    regular(destination / name)
                    if (destination / name).read_bytes() != content:
                        raise ValueError('immutable release document corruption')
                atomic(state, raw)
                atomic(index, raw)
                return receipt | {'status': 'REUSED', 'release_path': str(destination)}
            if any(version_key(r.version) >= version_key(args.version) for r in previous):
                raise ValueError('same-version publication refused')
            epoch = args.metadata_version if args.metadata_version is not None else highest + 1
            if epoch <= highest:
                raise ValueError('metadata version must exceed the channel high-water mark')
            if args.certificate_thumbprint:
                # Only new publications sign. Retries reuse the authenticated,
                # immutable output instead of generating another timestamp.
                target = scratch / 'signed-package'
                shutil.copytree(args.target, target)
                if inventory(target, args.version)[0] != manifest:
                    raise ValueError('input package changed during signing preparation')
                signing_receipt = scratch / 'authenticode.json'
                subprocess.run([args.powershell, '-NoProfile', '-NonInteractive', '-File',
                                str(Path(__file__).with_name('sign_release.ps1')),
                                '-PackageRoot', str(target), '-CertificateThumbprint', args.certificate_thumbprint,
                                '-TimestampServer', args.timestamp_server, '-ReceiptPath', str(signing_receipt)], check=True)
                signing = strict_json(signing_receipt.read_bytes())
                if signing.get('PRODUCTION_SIGNING') != 'CONFIGURED_AND_VERIFIED':
                    raise ValueError('Authenticode hook did not verify production signing')
                manifest, rows = inventory(target, args.version, objects)
                pe_paths = {p for p in rows if Path(p).suffix.lower() in {'.exe', '.dll', '.pyd'}}
                attestations = signing.get('signed_files', [])
                if (len(attestations) != len(pe_paths) or {r.get('path') for r in attestations} != pe_paths or
                        any(r.get('status') != 'Valid' or r.get('sha256') != rows[r['path']]['sha256'] or
                            r.get('signer_thumbprint', '').lower() != args.certificate_thumbprint.lower() or
                            not re.fullmatch('[0-9a-fA-F]{40}', r.get('timestamp_thumbprint', '')) for r in attestations)):
                    raise ValueError('Authenticode receipt does not cover exact signed PE files')
                changed = sorted(p for p, row in rows.items() if old_rows.get(p) != row)
            now = datetime.now(timezone.utc)
            assets = [{**row, 'url': origin + '/objects/' + row['sha256']} for _, row in sorted(rows.items())]
            signed = dict(schema_version=1, app_id=APP_ID, channel=args.channel, version=args.version,
                          metadata_version=epoch, issued_at=now.isoformat(), published_at=now.isoformat(),
                          expires_at=(now + timedelta(days=args.expiry_days)).isoformat(), key_id=key_id,
                          rollout_percentage=args.rollout_percentage, rollout_seed=args.rollout_seed, withdrawn=False,
                          min_supported_version=args.min_supported_version, min_updater_version=args.min_updater_version,
                          mandatory=args.mandatory, mandatory_reason=args.mandatory_reason, release_notes=notes,
                          full_release=dict(format='file-set-v1', size=sum(r['size'] for r in rows.values()), assets=assets),
                          incremental_assets=changed, file_manifest=manifest, hash_algorithm='sha256',
                          signature_algorithm='ed25519', source_sha=args.source_sha, tree_sha=args.tree_sha, base_versions=bases)
            raw = canonical(dict(signed=signed, key_id=key_id, signature=base64.b64encode(key.sign(canonical(signed))).decode()))
            release = verify_release(raw, keys, current='0.0.0', channel=args.channel, highest_seen=highest)
            receipt = dict(schema='mentorflow.release-build.v1', status='BUILT', input_fingerprint=fingerprint,
                           version=args.version, channel=args.channel, metadata_version=epoch, source_sha=args.source_sha,
                           tree_sha=args.tree_sha, metadata_sha256=digest(raw), METADATA_SIGNING=signing_mode,
                           **signing, full_bytes=signed['full_release']['size'],
                           incremental_bytes=sum(rows[p]['size'] for p in changed),
                           deleted_paths=sorted(set(old_rows) - set(rows)))
            version_stage = scratch / 'version'
            documents = release_documents(release, receipt, key, public)
            for name, data in documents.items():
                write_new(version_stage / name, data)
            object_root = args.output / 'objects'
            regular(object_root, directory=True)
            object_root.mkdir(exist_ok=True)
            for source in objects.iterdir():
                final = object_root / source.name
                regular(final)
                if final.exists():
                    if file_hash(final) != source.name:
                        raise ValueError('existing immutable object corruption')
                else:
                    source.rename(final)
            verify_output(args.output, release)
            destination.parent.mkdir(exist_ok=True)
            version_stage.rename(destination)  # immutable version first; discoverable channel LAST
            atomic(state, raw)  # an interruption cannot silently reuse a lower metadata epoch
            atomic(index, raw)
            return receipt | {'release_path': str(destination)}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--target', type=Path, default=Path('release/distributable/MentorFlow'))
    p.add_argument('--output', type=Path, default=Path('release/feed'))
    p.add_argument('--version', required=True)
    p.add_argument('--channel', choices=('stable', 'beta'), required=True)
    p.add_argument('--notes', type=Path, required=True)
    p.add_argument('--base', type=Path)
    p.add_argument('--origin', required=True)
    p.add_argument('--source-sha', required=True)
    p.add_argument('--tree-sha', required=True)
    p.add_argument('--signing-key', type=Path)
    p.add_argument('--key-id')
    p.add_argument('--key-password-env')
    p.add_argument('--development-signing', action='store_true')
    p.add_argument('--uat-loopback', action='store_true',
                   help='Compiled UAT identity only: reuse an external test key for a literal-loopback BASE/TARGET feed')
    p.add_argument('--min-supported-version', default='1.0.0')
    p.add_argument('--min-updater-version', default='1.0.0')
    p.add_argument('--mandatory', action='store_true')
    p.add_argument('--mandatory-reason', choices=('', 'security', 'protocol', 'data_compatibility'), default='')
    p.add_argument('--metadata-version', type=int)
    p.add_argument('--expiry-days', type=int, default=7)
    p.add_argument('--rollout-percentage', type=int, default=100)
    p.add_argument('--rollout-seed', default='mentorflow-release-v1')
    p.add_argument('--certificate-thumbprint')
    p.add_argument('--timestamp-server', default='')
    p.add_argument('--powershell', default='powershell.exe')
    return p


def main():
    try:
        result = build(parser().parse_args())
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit('RELEASE_BUILD_FAILED: ' + str(exc)) from None
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
