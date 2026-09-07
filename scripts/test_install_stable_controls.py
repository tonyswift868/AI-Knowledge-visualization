"""Real filesystem transaction, ordinary ACL/metadata adapters only."""
from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import pytest
from scripts.test_install_process_recovery import adapters,Registry,Shortcut,source
from scripts import windows_install_transaction as tx,windows_install_trust as trust,verify_windows_package as v


@pytest.mark.parametrize('fail',[False,True])
def test_stable_controls_outside_swapped_app_and_rollback(fail):
    base=Path(tempfile.mkdtemp(prefix='controls-',dir=os.environ['TEMP']))
    with ExitStack() as stack:
        adapters(stack)
        old,_=source(base,'old');new,_=source(base,'new')
        for payload in (old,new):
            (payload/'UpdateBridge.exe').write_bytes(b'bridge-'+payload.name.encode())
            runtime=payload/'UpdateRuntime/_internal';runtime.mkdir(parents=True)
            (payload/'UpdateRuntime/UpdateRuntime.exe').write_bytes(b'runtime-'+payload.name.encode())
            (runtime/'runtime.dll').write_bytes(b'dll-'+payload.name.encode())
        def ids(payload):return dict(manifest_sha256=tx.sha(v.build_manifest(payload)),source_sha='a'*40,tree_sha='b'*40)
        uninstall=base/'uninstall.exe';uninstall.write_bytes(b'fixture')
        root=base/'installed';registry=Registry(base);shortcut=Shortcut(base)
        tx.Transaction(old,root,ids(old),uninstall,registry=registry,shortcut=shortcut).run()
        original={path:(root/path).read_bytes() for path in ('UpdateBridge.exe','UpdateRuntime/UpdateRuntime.exe','UpdateRuntime/_internal/runtime.dll')}
        def fault(point):
            if fail and point=='after_swap':raise RuntimeError('synthetic rollback')
        txn=tx.Transaction(new,root,ids(new),uninstall,registry=registry,shortcut=shortcut,fault=fault,retain_previous=True)
        if fail:
            with pytest.raises(RuntimeError):txn.run()
        else:txn.run()
        for path,data in original.items():assert (root/path).read_bytes()==data
        assert (root/'App/UpdateBridge.exe').read_bytes()==(b'bridge-old' if fail else b'bridge-new')
        trust.verify_installation(root,**tx.identities_from_receipt(root),workers=1)
