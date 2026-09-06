"""Prepare verified embedded bytes and compile-time pins; does not build/install."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import zipfile
from scripts import verify_windows_package as v


def _entries(root):
    pending = [(root, '')]
    while pending:
        parent, prefix = pending.pop()
        with os.scandir(parent) as children:
            for entry in children:
                path = Path(entry.path)
                metadata = path.lstat()
                directory = stat.S_ISDIR(metadata.st_mode)
                key = prefix + entry.name
                v._validate_package_entry(path, root, directory=directory, resolve_path=False,
                                          cached_stat=metadata, cached_relative=key)
                yield key, path, directory
                if directory:
                    pending.append((path, key + '/'))


def prepare(payload, output, *, manifest_sha256, source_sha, tree_sha):
    for value, length in ((manifest_sha256, 64), (source_sha, 40), (tree_sha, 40)):
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{%d}' % length, value):
            raise ValueError('Invalid fixed release identity')
    payload = v._validate_package_root(Path(payload)).resolve()
    output = Path(os.path.abspath(output))
    v._ensure_e_drive(output, label='bootstrap preparation')
    if output.exists() or output == payload or output.is_relative_to(payload) or payload.is_relative_to(output):
        raise ValueError('New output directory outside payload required')
    v._validate_package_root(output.parent)
    manifest_bytes = (payload / v.MANIFEST_RELATIVE).read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256:
        raise v.PackageError('Pinned manifest mismatch')
    v.verify_package(payload, data_root=None, check_data=False, smoke=False, smoke_seconds=0)
    manifest = json.loads(manifest_bytes)
    version = manifest['version']
    if not re.fullmatch(r'[0-9A-Za-z.+-]{1,128}', version):
        raise v.PackageError('Unsafe compiled version literal')
    expected = {row['path']: row['sha256'] for row in manifest['files']}
    if len(expected) != len(manifest['files']):
        raise v.PackageError('Duplicate manifest paths')
    expected[v.MANIFEST_RELATIVE.as_posix()] = manifest_sha256
    if 'MentorFlowInstallGate.exe' not in expected:
        raise v.PackageError('Protected native gate is required')
    output.mkdir()
    archive_path = output / 'payload.zip'
    # The archive's compressed bytes are bound only after every streamed file
    # matched the already pinned manifest. No hash/identity is emitted on error.
    with v._watch_gate_directory(payload) as unchanged:
        entries = sorted(_entries(payload))
        if len(entries) > 50000 or {key for key, _, directory in entries if not directory} != set(expected):
            raise v.PackageError('Payload file set changed')
        total = 0
        with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for key, path, directory in entries:
                info = zipfile.ZipInfo(key + '/' if directory else key)
                info.external_attr = (stat.S_IFDIR | 0o755 if directory else stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                if directory:
                    archive.writestr(info, b'')
                    continue
                digest = hashlib.sha256()
                with path.open('rb') as incoming, archive.open(info, 'w', force_zip64=True) as outgoing:
                    while block := incoming.read(1024 * 1024):
                        total += len(block)
                        if total > 8 * 1024**3:
                            raise v.PackageError('Payload size limit exceeded')
                        digest.update(block)
                        outgoing.write(block)
                if digest.hexdigest() != expected[key]:
                    raise v.PackageError('Payload changed while archiving: ' + key)
        unchanged()
    archive_sha = v.sha256_file(archive_path)
    if archive_path.stat().st_size > 8 * 1024**3:
        raise v.PackageError('Archive size limit exceeded')
    values = dict(ArchiveSha256=archive_sha, ManifestSha256=manifest_sha256,
                  SourceSha=source_sha, TreeSha=tree_sha, Version=version)
    source = '// Generated from verified archive bytes. No runtime sidecar pins.\nnamespace MentorFlow.Installation {\ninternal static class ReleaseIdentity {\n'
    source += ''.join('internal const string %s = "%s";\n' % item for item in values.items())
    source += '}\n}\n'
    with (output / 'ReleaseIdentity.cs').open('x', encoding='utf-8', newline='\n') as generated:
        generated.write(source)
    return dict(archive=str(archive_path), identity_source=str(output / 'ReleaseIdentity.cs'),
                identities=values, files=len(expected), entries=len(entries), uncompressed_bytes=total)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--payload-root', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    for name in ('manifest-sha256', 'source-sha', 'tree-sha'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.payload_root, args.output_root, manifest_sha256=args.manifest_sha256,
                             source_sha=args.source_sha, tree_sha=args.tree_sha)))


if __name__ == '__main__':
    main()
