"""Real local filesystem location tests; small transaction with explicit ACL adapters."""
from pathlib import Path
from unittest.mock import patch
import shutil
import pytest
from scripts.install_location import existing_parent,low_space_message
from scripts import windows_install_transaction as tx
from scripts import test_install_transaction as fixture

@pytest.fixture
def case():
    value=fixture.TransactionTests();value.setUp();value.root=value.base/'MentorFlow'
    try:yield value
    finally:value.doCleanups()

def test_existing_parent_only_one_application_level(case):
    existing_parent(case.root);case.transaction().run()
    assert case.root.parent==case.base and case.root.is_dir()
    assert tx.recognised(case.root)

def test_nonexistent_parent_never_created(case):
    root=case.base/'NO_PARENT_X/NO_PARENT_Y/MentorFlow'
    with pytest.raises(ValueError,match='所选安装位置不存在'):existing_parent(root)
    case.root=root
    with pytest.raises(ValueError,match='所选安装位置不存在'):case.transaction().preflight()
    assert not (case.base/'NO_PARENT_X').exists()

def test_nonexistent_drive_without_fallback():
    drive=next(letter for letter in 'ZYXWVUTSRQPONMLKJIHGF' if not Path(letter+':/').exists())
    with pytest.raises(ValueError,match='找不到所选磁盘'):existing_parent(Path(drive+':/Apps/MentorFlow'))
    assert not Path(drive+':/').exists()

def test_existing_empty_target(case):
    case.root.mkdir();existing_parent(case.root);case.transaction().preflight()
    assert list(case.root.iterdir())==[]

def test_unknown_nonempty_target_preserved(case):
    case.root.mkdir();sentinel=case.root/'unknown.txt';sentinel.write_bytes(b'untouched')
    with pytest.raises(ValueError,match='已有其他文件'):case.transaction().preflight()
    assert sentinel.read_bytes()==b'untouched'

def test_low_space_human_message(case):
    with patch.object(shutil,'disk_usage',return_value=shutil._ntuple_diskusage(100,100,0)),pytest.raises(ValueError,match='MB|GB'):
        case.transaction().preflight()
    message=low_space_message(2200000000,1000000)
    assert '2200000000' not in message and 'GB' in message and 'MB' in message

def test_large_file_reports_intermediate_byte_progress(case):
    src=case.base/'large.bin';src.write_bytes(b'a'*1024*1024)
    txn=case.transaction();txn.payload_total=src.stat().st_size
    values=[];txn.status=lambda text,p:values.append(p)
    ticks=iter(i*.2 for i in range(100))
    with patch.object(tx.time,'monotonic',side_effect=lambda:next(ticks)):
        txn.copy_file(src,case.base/'copy.bin')
    assert len(set(values))>5 and any(0<p<70 for p in values)

def test_native_folder_picker_and_no_space_confirmation():
    source=(Path(__file__).resolve().parents[1]/'installer/ProtectedInstall.nsh').read_text(encoding='utf-8')
    assert 'nsDialogs::SelectFolderDialog' in source and 'GetFileAttributesW' in source
    assert 'InstallDir "$PROGRAMFILES64\\MentorFlow"' in source
    assert 'MF14I' not in source and 'PROTECTED_DEFAULT_DIR' not in source
    leave=source.split('Function ValidateDirectory',1)[1].split('FunctionEnd',1)[0]
    assert 'MB_OKCANCEL' not in leave and 'preflight $HelperArgs' in leave

def test_desktop_and_start_menu_links_use_real_executable(case):
    case.transaction().run()
    links=tx.ShortcutSet.__new__(tx.ShortcutSet)
    links.links={key:tx.Shortcut(case.base/(key+'.lnk')) for key in ('desktop','start_menu')}
    links.publish(case.root,case.ids)
    assert links.points_to(case.root)
    saved=links.snapshot()
    assert saved['desktop'] and saved['start_menu']
    links.restore(None)
    assert links.snapshot()==dict(desktop=None,start_menu=None)

def test_shortcut_failure_restores_first_link(case):
    case.transaction().run()
    links=tx.ShortcutSet.__new__(tx.ShortcutSet)
    links.links={key:tx.Shortcut(case.base/(key+'.lnk')) for key in ('desktop','start_menu')}
    with patch.object(links.links['start_menu'],'publish',side_effect=OSError('test write failure')),pytest.raises(OSError):
        links.publish(case.root,case.ids)
    assert links.snapshot()==dict(desktop=None,start_menu=None)
