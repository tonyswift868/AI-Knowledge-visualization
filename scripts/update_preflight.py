"""Shared read-only update acceptance before consent and inside high helper.

No caller-provided public keys or root authorization. root is supplied by the
protected bridge's own installation location. Request bytes are consistency
only; current receipt, native root identity and signed target are independent.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path

from desktop import update_request
from desktop.update_protocol import SecurityError, verify_release, digest
from desktop.windows_identity import read_directory
from scripts import windows_install_trust as trust
from scripts import windows_install_transaction as tx
from scripts.install_product import configured
from scripts.install_receipt_state import read_document, validate_installed_record
from scripts.update_source_reader import open_source


def trusted_keys():
    try:
        from mentorflow_delivery_authority import PUBLIC_KEYS
    except ModuleNotFoundError as error:
        if error.name!='mentorflow_delivery_authority':raise
        return {}
    if (not isinstance(PUBLIC_KEYS,dict) or not PUBLIC_KEYS or
            any(not isinstance(key,str) or type(value) is not bytes or len(value)!=32 for key,value in PUBLIC_KEYS.items())):
        raise SecurityError('invalid compiled update public keys')
    return dict(PUBLIC_KEYS)


def accept(root,request_path,request_sha256,*,verify_staging=True):
    root=tx.validate_target(root)
    product=configured()
    request_path=Path(request_path).absolute()
    with open_source(request_path) as stream:
        raw=stream.read(65537)
    if len(raw)>65536:raise SecurityError('oversized frozen request')
    doc=update_request.read(raw,expected_sha256=request_sha256)
    if doc['app_id']!=product.app_id or Path(doc['install_root'])!=root or doc['install_identity']!=read_directory(root):
        raise SecurityError('request does not own this protected bridge installation')
    stage=Path(doc['staged_app'])
    if product.uat:
        from mentorflow_delivery_authority import WORK_ROOT
        work=Path(WORK_ROOT).absolute()
        if not stage.is_relative_to(work) or not request_path.is_relative_to(work):
            raise SecurityError('UAT update input outside compiled isolated work root')
    receipt_path=root/trust.RECEIPT
    tx.assert_no_alias(receipt_path);trust.assert_protected(receipt_path)
    if receipt_path.stat().st_size>16*1024**2:raise SecurityError('oversized canonical receipt')
    raw_receipt=receipt_path.read_bytes()
    receipt=validate_installed_record(read_document(receipt_path),root)
    if receipt.get('schema_version')==2 and receipt.get('state')!='HEALTHY':
        raise SecurityError('BASE real first-launch health is still pending')
    binding=receipt['binding']
    if (digest(raw_receipt)!=doc['base_receipt_sha256'] or binding['version']!=doc['base_version'] or
            binding['manifest_sha256']!=doc['base_manifest_sha256']):
        raise SecurityError('request base differs from protected canonical receipt')
    trust.verify_installation(root,**tx.identities_from_receipt(root),workers=1)
    claim=root/tx.STAGING/tx.UPDATE_METADATA/(doc['attempt_id']+'.claim.json')
    if claim.exists():raise SecurityError('update request replay refused')
    with open_source(stage.parent/'release.json') as stream:
        envelope=stream.read(16*1024**2+1)
    if digest(envelope)!=doc['release_sha256']:raise SecurityError('frozen release envelope changed')
    keys=trusted_keys()
    if not keys:raise SecurityError('update signing authority is not provisioned')
    release=verify_release(envelope,keys,current=binding['version'])
    if release.version!=doc['target_version'] or digest(release.manifest_bytes)!=doc['target_manifest_sha256']:
        raise SecurityError('request target differs from signed release')
    # Full signature/manifest checks above run again in high code, regardless
    # of the ordinary-permission preflight result.
    if verify_staging:
        expected=dict(release.rows)
        actual=set();pending=[stage]
        while pending:
            parent=pending.pop();tx.assert_no_alias(parent)
            for child in parent.iterdir():
                tx.assert_no_alias(child)
                if child.is_dir():pending.append(child)
                elif child.is_file():actual.add(child.relative_to(stage).as_posix())
                else:raise SecurityError('special staged entry rejected')
        if actual!=set(expected):raise SecurityError('staged file set differs from signed release')
        def verify_row(item):
            key,row=item
            with open_source(stage/key) as stream:
                sha=hashlib.sha256();size=0
                while chunk:=stream.read(1024**2):sha.update(chunk);size+=len(chunk)
            if size!=row['size'] or sha.hexdigest()!=row['sha256']:
                raise SecurityError('staged signed content changed: '+key)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for _ in pool.map(verify_row,expected.items()):pass
    return doc,release
