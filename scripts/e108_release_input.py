"""Offline E108 artifact admission and precise private handoff to publisher 04.

No App/installer invocation, network, deployment, signing-key disclosure or Git
mutation. Public upload is an explicit allowlist; publisher state stays private.
"""
import base64
import os
from pathlib import Path
import sys

from desktop.update_protocol import canonical, strict_json, verify_release
from scripts import build_e108_user_candidate as builder


def file_fact(path):
    path = Path(path).resolve(strict=True)
    return dict(path=str(path), sha256=builder.sha(path), bytes=path.stat().st_size)


def public_uploads(feed):
    """Only immutable release objects/documents and the final channel pointer."""
    items = []
    roots = [feed / 'releases/objects',
             *(feed / 'releases' / version / 'stable'
               for version in (builder.BASE_VERSION, builder.TARGET_VERSION))]
    for root in roots:
        if not root.is_dir(): raise ValueError('Missing public release root')
        for path in sorted(root.rglob('*')):
            if path.is_symlink() or path.is_junction(): raise ValueError('Public feed alias refused')
            if path.is_file():
                items.append(dict(**file_fact(path), relative_path=path.relative_to(feed).as_posix(),
                                  phase='IMMUTABLE_FIRST'))
    pointer = feed / builder.METADATA_RELATIVE_PATH
    items.append(dict(**file_fact(pointer), relative_path=builder.METADATA_RELATIVE_PATH,
                      phase='ATOMIC_CHANNEL_LAST'))
    return items


def finalize(work, output, source, binary_checks):
    from mentorflow_delivery_authority import PUBLIC_KEYS, APP_ID, SOURCE_SHA, ONE_CLICK_TEST_FIXTURE
    from scripts import build_release as publisher
    from scripts.per_user_install import verify_tree
    from scripts.per_user_update_core import static_feed_url
    from desktop.version import installed_release_details
    if source != builder.identity() or SOURCE_SHA != source['source_sha']:
        raise ValueError('Final source is not the clean admitted source')
    if len(binary_checks) != 2:
        raise ValueError('Both App and native runtime require compiled source evidence')
    if APP_ID != builder.APP_ID or ONE_CLICK_TEST_FIXTURE is not False:
        raise ValueError('Incorrect final product authority')
    feed = work / 'feed-for-04-publication'
    packages, releases = {}, {}
    feed_url = builder.require_update_feed_url(strict_json((work / 'build-input.json').read_bytes()).get('UPDATE_FEED_URL'))
    metadata_url = feed_url + builder.METADATA_RELATIVE_PATH
    for label, version in (('BASE', builder.BASE_VERSION), ('TARGET', builder.TARGET_VERSION)):
        package = work / label
        release_path = feed / 'releases' / version / 'stable/release.json'
        release = verify_release(release_path.read_bytes(), PUBLIC_KEYS, current='0.0.0', channel='stable')
        if (release.signed['source_sha'] != source['source_sha'] or
                release.signed['tree_sha'] != source['tree_sha'] or release.version != version):
            raise ValueError('Signed release source/version mismatch')
        verify_tree(package, release)
        publisher.verify_output(feed / 'releases', release)
        for path in package.rglob('*'):
            if path.is_file() and path.stat().st_nlink != 1:
                raise ValueError('BASE/TARGET payload must have independent regular files')
        document = strict_json((package / 'mentorflow-version.json').read_bytes())
        if document != builder.version_document(version, PUBLIC_KEYS, feed_url):
            raise ValueError('Installed version/feed/notes differ from final inputs')
        if (package / 'mentorflow-version.json').read_bytes() != (package / '_internal/mentorflow-version.json').read_bytes():
            raise ValueError('Frozen version manifests differ')
        if static_feed_url(document) != metadata_url:
            raise ValueError('Consumer metadata endpoint does not match 04 mount')
        if strict_json((package / 'SELF-CHECK/source-identity.json').read_bytes()) != source:
            raise ValueError('Payload source identity differs')
        for asset in release.assets.values():
            expected = feed_url + 'releases/objects/' + asset['sha256']
            if asset['url'] != expected: raise ValueError('Asset escapes the public release mount')
        packages[label] = dict(app_dir=str(package), version=version,
            executable=file_fact(package / 'MentorFlow.exe'),
            runtime=file_fact(package / 'UserRuntime/UserRuntime.exe'),
            manifest=file_fact(package / 'SELF-CHECK/package-manifest.json'),
            file_count=len(release.rows), payload_bytes=sum(row['size'] for row in release.rows.values()),
            count_definition='all signed payload files INCLUDING SELF-CHECK/package-manifest.json',
            signed_release=file_fact(release_path), installed_release_details=installed_release_details(document))
        releases[label] = release
    if packages['BASE']['installed_release_details'] is not None or not packages['TARGET']['installed_release_details']:
        raise ValueError('TARGET has no actual installed-notes/copy-version affordance')
    changed = sorted(path for path, row in releases['TARGET'].rows.items() if releases['BASE'].rows.get(path) != row)
    if changed != sorted(releases['TARGET'].signed['incremental_assets']):
        raise ValueError('Incremental release does not cover exact changed files')
    if releases['TARGET'].signed['base_versions'] != [builder.BASE_VERSION]:
        raise ValueError('TARGET is not bound to BASE')
    if (feed / builder.METADATA_RELATIVE_PATH).read_bytes() != releases['TARGET'].envelope:
        raise ValueError('Channel pointer is not the signed TARGET')
    installer = output / 'Installer/MentorFlow-Setup.exe'
    if sorted(path.relative_to(output).as_posix() for path in output.rglob('*') if path.is_file()) != ['Installer/MentorFlow-Setup.exe']:
        raise ValueError('User directory must contain exactly one installer and nothing else')
    compiler_receipts = list((work / 'inno-build').glob('inno-product-*/build-receipt.json'))
    if len(compiler_receipts) != 1: raise ValueError('Ambiguous Inno compilation evidence')
    compiled = strict_json(compiler_receipts[0].read_bytes())
    if (compiled['sha256'] != builder.sha(installer) or
            compiled['signed_release_sha256'] != packages['BASE']['signed_release']['sha256'] or
            compiled['package_manifest_sha256'] != packages['BASE']['manifest']['sha256'] or
            compiled['app_id'] != APP_ID or compiled['install_mode'] != 'per_user' or
            compiled['file_count'] != packages['BASE']['file_count'] or
            compiled['payload_bytes'] != packages['BASE']['payload_bytes'] or
            compiled['installer_launches'] != 0):
        raise ValueError('Installer is not compiled from exact signed BASE')
    for check, path in zip(binary_checks, (work / 'BASE/MentorFlow.exe', work / 'BASE/UserRuntime/UserRuntime.exe')):
        if not check['as_invoker'] or check['sha256'] != builder.sha(path):
            raise ValueError('Compiled source/asInvoker evidence mismatch')
    public_manifest = work / 'E108_PUBLIC_UPLOAD_MANIFEST.json'
    upload_items = public_uploads(feed)
    builder.emit(public_manifest, dict(schema='mentorflow.public-upload-list.v1', files=upload_items,
        excluded=['.release-state', '.release-build.lock', 'releases/stable.json', 'all private build inputs and keys']))
    notes = work / 'release-notes.txt'
    authority_path = work / 'generated/mentorflow_delivery_authority.py'
    infra = Path('E:/Codex/Temp/mf-e108-w05-https-20260909/RECEIPT.json')
    if builder.sha(infra) != 'f39843f368ceb57bf043f0390f199a71fefbd3606ddf1110f712bd91717347e2':
        raise ValueError('05 HTTPS authority evidence changed')
    handoff = dict(schema='mentorflow.e108-final-release-input.v1', status='READY_FOR_04_FINAL_DEPLOY',
        app_id=APP_ID, base_version=builder.BASE_VERSION, target_version=builder.TARGET_VERSION,
        source_root=str(builder.ROOT), source_sha=source['source_sha'], source_tree=source['tree_sha'],
        channel='stable', STAGING_UPDATE_BASE_URL=feed_url,
        UPDATE_FEED_URL=feed_url, metadata_url=metadata_url,
        BASE=packages['BASE'], TARGET=packages['TARGET'],
        BASE_APP_DIR=packages['BASE']['app_dir'], TARGET_APP_DIR=packages['TARGET']['app_dir'],
        BASE_MANIFEST=packages['BASE']['manifest']['path'], TARGET_MANIFEST=packages['TARGET']['manifest']['path'],
        release_notes_source=file_fact(notes), base_release_notes_source=file_fact(work / 'base-release-notes.txt'),
        target_user_visible_change='About: offline installed release notes and copy-version action; clipboard includes no configuration',
        installer=file_fact(installer), installer_compile_receipt=file_fact(compiler_receipts[0]),
        user_visible_installer_count=1, binary_source_checks=binary_checks,
        update=dict(incremental_paths=changed,
            incremental_bytes=sum(releases['TARGET'].rows[path]['size'] for path in changed),
            full_bytes=packages['TARGET']['payload_bytes'],
            automatic_relaunch='CoreUpdatePort -> apply_and_relaunch -> native coordinator',
            on_next_launch='UserRuntime launch -> per_user_launcher -> resume_pending_on_normal_launch'),
        signing=dict(key_id=builder.KEY_ID, algorithm='ed25519',
            public_keys={key: base64.b64encode(value).decode() for key, value in PUBLIC_KEYS.items()},
            private_key_reference=file_fact(work / 'test-signing-key.pem'),
            private_key_policy='04 local signing input ONLY; NEVER upload, package or change key for this BASE',
            compiled_authority=file_fact(authority_path), authenticode='NOT_CONFIGURED',
            trust_scope='isolated MentorFlow.UAT.E108Assembly1; not production signing authority'),
        publication=dict(local_feed_root=str(feed), upload_allowlist=file_fact(public_manifest),
            static_server_root='/opt/mentorflow/update-static/e108/',
            sequence=['Verify every input hash and source identity',
                'Upload ONLY IMMUTABLE_FIRST allowlist files, preserving relative paths and hashes',
                'Verify all immutable objects via public HTTPS',
                'Atomically replace stable/stable.json LAST on the same filesystem',
                'Verify signed metadata, HEAD, Range, ETag and packaged consumer through HTTPS under separate authority'],
            must_not_upload=['private key', 'generated authority', 'build logs', '.release-state', 'releases/stable.json'],
            deployed=False, refresh_requires_new_signed_metadata_epoch=True,
            metadata_expires_at=releases['TARGET'].signed['expires_at']),
        regeneration=dict(python=str(Path(sys.executable).resolve()), cwd=str(builder.ROOT),
            environment={'PYTHONPATH': str(authority_path.parent) + os.pathsep + str(builder.ROOT),
                         'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONIOENCODING': 'utf-8'},
            commands=[[str(Path(sys.executable).resolve()), '-B', '-m', 'scripts.build_release',
                *builder.release_arguments(work / 'TARGET', builder.TARGET_VERSION, work / 'BASE', work, source, feed_url)]],
            initial_build_commands_for_audit_only=[[str(Path(sys.executable).resolve()), '-B', '-m', 'scripts.build_release',
                *builder.release_arguments(work / label, version, work / 'BASE' if label == 'TARGET' else None, work, source, feed_url)]
                for label, version in (('BASE', builder.BASE_VERSION), ('TARGET', builder.TARGET_VERSION))],
            channel_copy={'from': str(feed / 'releases/stable.json'), 'to': str(feed / builder.METADATA_RELATIVE_PATH)},
            app_rebuild_required=False,
            note='Prepared signed feed is ready to deploy. commands only verify/reuse the existing TARGET; '
                 'never replay BASE against a channel already advanced to TARGET. Initial build commands are an audit record, '
                 'not a replay sequence. Preserve key and publisher epoch state; do not use development-signing.'),
        offline_evidence=dict(https_owner_receipt=file_fact(infra),
            payload_manifest_and_signed_objects='PASS', consumer_endpoint_binding='PASS',
            runtime_source_closure='PASS', install_execution_level='asInvoker',
            product_windows_launched=0, installer_launches=0, uac_launches=0,
            github_actions_executed=0, remote_push='NOT_RUN',
            actual_https_deployed_feed_consumer='NOT_RUN: reserved for 04 deployment/qualification',
            local_server_required=False, cmd_required=False, browser_update=False))
    path = work / 'E108_FINAL_RELEASE_INPUT.json'
    builder.emit(path, handoff)
    builder.emit(work / 'FINAL_OFFLINE_RECEIPT.json', dict(status=handoff['status'],
        handoff=file_fact(path), source=source, installer=handoff['installer'],
        worktree_clean=builder.identity() == source, app_launches=0, installer_launches=0))
    return handoff
