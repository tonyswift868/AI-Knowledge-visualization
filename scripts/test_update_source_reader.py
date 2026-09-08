import os
from unittest.mock import patch
import pytest
from scripts.update_source_reader import open_source
from scripts import windows_install_transaction as tx


def test_real_file_lease_blocks_inplace_mutation_and_accepts_hardlink(tmp_path):
    original=tmp_path/'original.bin';original.write_bytes(b'unchanged immutable source')
    staged=tmp_path/'staged.bin';os.link(original,staged)
    with open_source(staged) as stream:
        assert stream.read()==original.read_bytes()
        with pytest.raises(OSError):original.write_bytes(b'tamper')
        with pytest.raises(OSError):staged.write_bytes(b'tamper')
    assert original.read_bytes()==b'unchanged immutable source'


@pytest.fixture
def case():
    from scripts.test_install_transaction import TransactionTests
    case=TransactionTests('test_success_selected_path_receipt_registration_shortcut')
    case.setUp()
    try:yield case
    finally:case.doCleanups()


def test_signed_hardlink_copy_is_independent(case):
        linked=case.base/'linked';linked.mkdir()
        source_file=case.source/'MentorFlow.exe'
        os.link(source_file,linked/'same.exe')
        transaction=case.transaction(signed_source=True)
        transaction.run()
        installed=case.root/'App/MentorFlow.exe'
        assert installed.stat().st_nlink==1
        assert source_file.stat().st_nlink==2
        assert installed.stat().st_ino != source_file.stat().st_ino
        assert installed.read_bytes()==source_file.read_bytes()

def test_signed_tamper_still_rollback(case):
        file=case.source/'MentorFlow.exe';file.write_bytes(b'forged')
        with pytest.raises(ValueError,match='hash mismatch'):
            case.transaction(signed_source=True).run()
        case.assert_fresh_rolled_back()


def test_live_canonical_receipt_rejects_staging_hardlink(case):
    from scripts import windows_install_trust as trust
    case.transaction().run()
    current=case.root/'App/MentorFlow.exe'
    os.link(current,case.base/'staging-link.exe')
    assert current.stat().st_nlink==2
    with pytest.raises((ValueError,RuntimeError)):
        trust.verify_installation(case.root,**case.ids)
