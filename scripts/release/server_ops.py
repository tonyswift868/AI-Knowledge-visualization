"""Filesystem operations shared by SSH publication and local rehearsals.

The caller verifies signed metadata with the existing delivery protocol BEFORE
invoking these operations. This module is sent to sudo python3 over SSH; it uses
only stdlib and never builds or signs software on the server.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile
import uuid

INSTALLER_KEEP_COUNT = 3


def retain_installers(releases):
    """Keep numeric top releases plus every installer hard-linked by latest.

    Counts are file-content bytes, not filesystem block/allocation estimates.
    Events go to stderr AND the result: SSH's stdout remains one JSON response.
    """
    directories = []
    for directory in releases.iterdir():
        if (not directory.is_dir() or directory.is_symlink()
                or (os.name == 'nt' and directory.is_junction())):
            continue
        try:
            key = tuple(map(int, version(directory.name).split('.')))
        except ValueError:
            continue
        directories.append((key, directory))
    directories.sort(reverse=True)
    latest = releases / 'latest/MentorFlow-Setup.exe'
    protected = [directory.name for _, directory in directories
                 if latest.is_file()
                 and (directory / ('MentorFlow-Setup-' + directory.name + '.exe')).is_file()
                 and os.path.samefile(latest, directory / ('MentorFlow-Setup-' + directory.name + '.exe'))]
    result = dict(keep_count=INSTALLER_KEEP_COUNT, protected_latest=protected,
                  deleted=[], removed_bytes=0, released_bytes=0, events=[])

    def log(event, **values):
        entry = dict(event=event, **values)
        result['events'].append(entry)
        print(json.dumps(entry, ensure_ascii=True), file=sys.stderr, flush=True)

    for _, directory in directories[INSTALLER_KEEP_COUNT:]:
        exe = directory / ('MentorFlow-Setup-' + directory.name + '.exe')
        if directory.name in protected or not exe.is_file():
            continue
        stat = exe.stat()
        row = dict(version=directory.name, path=exe.relative_to(releases).as_posix(),
                   bytes=stat.st_size, released_bytes=stat.st_size if stat.st_nlink == 1 else 0)
        log('installer_retention_before_delete', **row)
        try:
            exe.unlink()
        except OSError as error:
            log('installer_retention_delete_failed', **row, error=str(error))
            raise
        result['deleted'].append(row)
        result['removed_bytes'] += row['bytes']
        result['released_bytes'] += row['released_bytes']
        log('installer_retention_after_delete', **row)
    log('installer_retention_complete', deleted_versions=[r['version'] for r in result['deleted']],
        removed_bytes=result['removed_bytes'], released_bytes=result['released_bytes'])
    return result


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            value.update(chunk)
    return value.hexdigest()


def checked(path, row):
    if not path.is_file() or path.stat().st_size != row['size'] or sha(path) != row['sha256']:
        raise ValueError('missing or corrupt object: ' + str(path))


def version(value):
    if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value):
        raise ValueError('numeric release version required')
    return value


def atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        temp.write_bytes(data)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def link_or_copy(source, target):
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def dispatch(request):
    app, state = Path(request['app']), Path(request['state'])
    action = request['action']
    v = version(request['version'])
    stage = app / ('.staging-' + v)
    if action == 'fetch':
        if request.get('staged'):
            path = stage / 'stable/stable.json'
        else:
            path = app / 'releases' / v / 'stable.json'
            if not path.exists():
                if v == '2.0.2':
                    path = state / 'stable-2.0.2-before-2.0.3.json'
                else:
                    candidate = app / 'stable/stable.json'
                    if candidate.exists() and json.loads(candidate.read_bytes())['signed']['version'] == v:
                        path = candidate
        return dict(raw=base64.b64encode(path.read_bytes()).decode())
    if action == 'live':
        return dict(raw=base64.b64encode((app / 'stable/stable.json').read_bytes()).decode())
    if action == 'cleanup':
        if stage.exists():
            shutil.rmtree(stage)
        return dict(status='STAGING_REMOVED')
    if action == 'missing':
        missing = []
        for row in request['rows']:
            path = app / 'objects' / row['sha256']
            if path.exists():
                checked(path, row)  # Existing corrupt content is NOT silently replaced.
            else:
                missing.append(row['sha256'])
        return dict(missing=sorted(set(missing)))
    if action in ('stage_objects', 'stage_manifest', 'installer'):
        transfer = Path(request['transfer'])
        try:
            if action == 'stage_objects':
                rows = {r['sha256']: r for r in request['rows']}
                # Check the entire incoming batch before exposing any staging object.
                stage.mkdir(parents=True, exist_ok=True)
                unpack = Path(tempfile.mkdtemp(prefix='release-unpack-', dir=stage))
                try:
                    with tarfile.open(transfer, 'r') as archive:
                        for member in archive:
                            if member.name not in rows or not member.isfile():
                                raise ValueError('unexpected archive member: ' + member.name)
                            source = archive.extractfile(member)
                            with (unpack / member.name).open('wb') as target:
                                shutil.copyfileobj(source, target)
                            checked(unpack / member.name, rows[member.name])
                    for name, row in rows.items():
                        checked(unpack / name, row)
                    (stage / 'objects').mkdir(parents=True, exist_ok=True)
                    for name in rows:
                        target = stage / 'objects' / name
                        if target.exists():
                            checked(target, rows[name])
                        else:
                            os.replace(unpack / name, target)
                finally:
                    shutil.rmtree(unpack)
                return dict(status='STAGED', files=len(rows))
            raw = base64.b64decode(request['manifest'])
            if action == 'stage_manifest':
                if sha(transfer) != request['sha256']:
                    raise ValueError('manifest transfer hash mismatch')
                atomic_bytes(stage / 'stable/stable.json', transfer.read_bytes())
                return dict(status='SIGNED_MANIFEST_STAGED')
            checked(transfer, request['installer'])
            releases = app / 'releases'
            releases.mkdir(parents=True, exist_ok=True)
            destination = releases / v
            if destination.exists():
                # Metadata stays immutable even when retention removed the exe.
                if (destination / 'stable.json').read_bytes() != raw:
                    raise ValueError('published version has different signed metadata')
                exe = destination / ('MentorFlow-Setup-' + v + '.exe')
                if exe.exists():
                    checked(exe, request['installer'])
                else:
                    restored = destination / ('.MentorFlow-Setup-' + uuid.uuid4().hex + '.exe')
                    try:
                        shutil.copyfile(transfer, restored)
                        checked(restored, request['installer'])
                        os.replace(restored, exe)
                    finally:
                        restored.unlink(missing_ok=True)
            else:
                temp = Path(tempfile.mkdtemp(prefix='.' + v + '-', dir=releases))
                try:
                    exe = temp / ('MentorFlow-Setup-' + v + '.exe')
                    shutil.copyfile(transfer, exe)
                    checked(exe, request['installer'])
                    (temp / 'stable.json').write_bytes(raw)
                    # mkdtemp is 0700 on Linux; nginx must traverse the public
                    # version directory after its atomic rename.
                    temp.chmod(0o755)
                    temp.rename(destination)
                finally:
                    if temp.exists():
                        shutil.rmtree(temp)
            if request.get('latest'):
                latest = releases / 'latest'
                if latest.is_symlink() or (os.name=='nt' and latest.is_junction()):
                    raise ValueError('latest must be an ordinary directory')
                latest.mkdir(exist_ok=True)
                next_link = latest / ('.MentorFlow-Setup-' + uuid.uuid4().hex + '.exe')
                try:
                    # update-static disables symlinks. Require a hard link on
                    # this filesystem; never replace it with a symlink or copy.
                    os.link(destination / ('MentorFlow-Setup-' + v + '.exe'), next_link)
                    checked(next_link, request['installer'])
                    os.replace(next_link, latest / 'MentorFlow-Setup.exe')
                finally:
                    next_link.unlink(missing_ok=True)
                old = app / 'installer/MentorFlow-Setup.exe'
                if old.exists():
                    state.mkdir(parents=True, exist_ok=True)
                    backup = state / ('installer-before-releases-' + uuid.uuid4().hex + '.exe')
                    shutil.copyfile(old, backup)
                    if sha(old) != sha(backup):
                        raise ValueError('old installer backup mismatch')
                    old.unlink()
            retention = retain_installers(releases)
            return dict(status='INSTALLER_PUBLISHED', version=v, latest=bool(request.get('latest')),
                        retention=retention)
        finally:
            transfer.unlink(missing_ok=True)
    if action == 'promote':
        raw = base64.b64decode(request['manifest'])
        if (stage / 'stable/stable.json').read_bytes() != raw:
            raise ValueError('staged manifest changed after verification')
        rows = request['rows']
        archived = app / 'releases' / v / 'stable.json'
        if archived.exists() and archived.read_bytes() != raw:
            raise ValueError('published version has different signed metadata')
        # No stable replacement until ALL objects have been checked.
        for row in rows:
            existing = app / 'objects' / row['sha256']
            checked(existing if existing.exists() else stage / 'objects' / row['sha256'], row)
        objects = app / 'objects'
        objects.mkdir(parents=True, exist_ok=True)
        for row in rows:
            target = objects / row['sha256']
            if not target.exists():
                # Hard link is exclusive: never overwrite an existing object.
                os.link(stage / 'objects' / row['sha256'], target)
        live = app / 'stable/stable.json'
        state.mkdir(parents=True, exist_ok=True)
        if live.exists() and live.read_bytes() != raw:
            old_version = version(json.loads(live.read_bytes())['signed']['version'])
            backup = state / ('stable-' + old_version + '-before-' + v + '-' + uuid.uuid4().hex + '.json')
            atomic_bytes(backup, live.read_bytes())
        # Keep every signed version available for a later mf-installer run,
        # including daily publications that deliberately do not build an exe.
        if not archived.exists():
            atomic_bytes(archived, raw)
        atomic_bytes(live, raw)
        return dict(status='STABLE_PUBLISHED', version=v, files=len(rows),
                    time=datetime.now(timezone.utc).isoformat())
    raise ValueError('unknown server operation: ' + action)


if __name__ == '__main__':
    request = json.load(sys.stdin)
    Path(request['state']).mkdir(parents=True, exist_ok=True)
    print(json.dumps(dispatch(request), ensure_ascii=True))
