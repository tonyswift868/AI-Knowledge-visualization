"""Regression guards for the three asynchronous frontend races in batch 219."""
import os
from pathlib import Path


ROOT = Path(os.environ.get("MF219_SOURCE_ROOT", Path(__file__).resolve().parents[3]))


def test_frontend219_settings_read_is_fenced_by_account_scope_and_request():
    source = (ROOT / "refactor/learning/web/src/App.tsx").read_text(encoding="utf-8")
    ready = source.split('on("ready",', 1)[1].split('on("account",', 1)[0]
    scope = source.split('on("scope_changed",', 1)[1].split('on("sync_status",', 1)[0]
    assert "readSettings(d.account?.account_id, waitsForScopedSettings)" in ready
    assert "const revision = ++settingsReadRevision.current" in source
    assert "epoch !== modelScopeEpoch.current || revision !== settingsReadRevision.current" in source
    assert "activeAccountId !== accountId" in source
    assert "settingsReadRevision.current++" in scope


def test_frontend219_rag_session_open_ignores_old_success_and_failure():
    source = (ROOT / "refactor/learning/web/src/pages/Rag.tsx").read_text(encoding="utf-8")
    open_session = source.split("async function openSession", 1)[1].split("function toggleDocument", 1)[0]
    assert "const revision = ++sessionReadRevision.current" in open_session
    assert "revision !== sessionReadRevision.current" in open_session
    assert "revision === sessionReadRevision.current" in open_session
    assert "sessionReadRevision.current++" in source


def test_frontend219_plan_detail_ignores_stale_refresh_error():
    source = (ROOT / "refactor/learning/web/src/pages/PlanDetail.tsx").read_text(encoding="utf-8")
    assert '.catch(e=>{if(live&&revision===linksRead.current)setLinksError' in source


def test_frontend219_chat_has_archive_or_restore_and_confirmed_direct_delete():
    source = (ROOT / "refactor/learning/web/src/pages/Chat.tsx").read_text(encoding="utf-8")
    assert 'onArchive={() => void setArchived(s.id, true)}' in source
    assert 'onDelete={() => remove(s.id)}' in source
    assert 'confirm ? onDelete() : setConfirm(true)' in source
    assert 'call("chat.delete", { session_id: id })' in source
    assert 'bridge.register("chat.delete", guarded(delete))' in (ROOT / "refactor/learning/app/chat.py").read_text(encoding="utf-8")
