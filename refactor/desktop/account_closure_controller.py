"""One captured account/operation; explicit writes, resumable queries and cleanup."""
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .account_closure_client import MESSAGES, failure
from .account_closure_store import REPLACEMENT_REFUSED, can_reprepare
from .login_session import account_session_lock


class ClosureController(QObject):
    delivered = Signal(object)
    export_delivered = Signal(object)

    def __init__(self, page, client, store, cleanup, *, release_account, changed,
                 export_plan=None, export_is_current=None, parent=None):
        super().__init__(parent)
        self.page, self.client, self.store, self.cleanup = page, client, store, cleanup
        self.profile = page.profile
        self.release_account, self.changed = release_account, changed
        self._closed = False
        self._busy = False
        self._cancelled = threading.Event()
        self._unsaved = None
        self._export_plan, self._export_is_current = export_plan, export_is_current
        self._export_attempt = None
        self.record = store.load(self.profile)
        self._queried = False
        page.set_prepare_authenticated(client.credentials is not None)
        self.delivered.connect(self._finish)
        self.export_delivered.connect(self._finish_export)
        page.closed.connect(self.close)
        page.prepare_requested.connect(lambda password: self.start('prepare', password=password))
        page.reprepare_requested.connect(lambda password: self.start('reprepare', password=password))
        page.commit_requested.connect(lambda: self.start('commit'))
        page.query_requested.connect(lambda: self.start('query'))
        page.save_requested.connect(lambda: self.start('save'))
        page.export_requested.connect(self.start_export)
        page.cleanup_requested.connect(self.start_cleanup)
        if self.record:
            page.render(self.record, error='CLOSURE_UNCONFIRMED' if self.record['operation_id'] else '')
            if self.record['state'] == 'COMPLETED':
                QTimer.singleShot(0, self.start_cleanup)
            elif self.record['operation_id']:
                QTimer.singleShot(0, lambda: self.start('query'))

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._cancelled.set()
        self._unsaved = None
        self._export_attempt = None
        if not self._busy:
            self.deleteLater()

    def start_export(self):
        if self._closed or self._busy or self._export_attempt is not None or self.record:
            return
        if not self._export_plan or not self._export_is_current or not self._export_is_current():
            self.page.finish_export(message='当前账号的计划导出暂不可用，请确认登录状态后重试。')
            return
        attempt = self._export_attempt = object()
        self.page.begin_export()

        def finished(result):
            try:
                self.export_delivered.emit((attempt, result))
            except RuntimeError:
                pass  # The dialog/account may have closed while export ran.

        try:
            self._export_plan(finished, cleanup_roots=(self.cleanup.managed_accounts / self.profile.account_id,))
        except Exception:
            finished(None)

    def _finish_export(self, delivery):
        attempt, result = delivery
        if self._closed or self._export_attempt is not attempt:
            return
        self._export_attempt = None
        message = '导出未能确认完成，请稍后重试。'
        saved = False
        if not self._export_is_current():
            message = '账号会话已变化，请返回当前账号后重新保存。'
        elif result is not None:
            # ExportResult uses a str Enum; do not import optional learning
            # modules into account startup or conflate the five outcomes.
            status = getattr(result, 'status', None)
            if not isinstance(status, str):
                status = None
            message = {
                'CANCELLED': '已取消保存。',
                'FAILED': '导出失败，请在计划页面查看原因并重试。',
                'MISSING_ORIGINALS': '关联资料缺少原件，请在计划页面补齐后重试保存。',
                'NO_CURRENT_PLAN': '当前没有可导出的计划，未生成导出包。',
            }.get(status, message)
            if status == 'SUCCEEDED' and getattr(result, 'account_id', None) == self.profile.account_id:
                try:
                    output = Path(result.output_path).resolve(strict=True)
                    root = (self.cleanup.managed_accounts / self.profile.account_id).resolve()
                    saved = output.is_file() and not output.is_relative_to(root)
                except (OSError, ValueError, TypeError, RuntimeError):
                    saved = False
        self.page.finish_export(saved=saved, message=message)

    def start_cleanup(self):
        if self._busy or self._export_attempt is not None or not self.record or self.record['state'] != 'COMPLETED':
            return
        # GUI-thread disposal must complete before filesystem/credential cleanup.
        try:
            self.release_account(self.profile)
        except Exception:
            if not self._closed:
                self.page.render(self.record, error='CLOSURE_LOCAL_CLEANUP_FAILED')
            return
        self.start('cleanup', allow_closed=True)

    def start(self, action, *, password=None, allow_closed=False):
        if self._busy or self._export_attempt is not None or (self._closed and not allow_closed):
            return
        if action == 'reprepare' and (not can_reprepare(self.record) or self._unsaved is not None):
            return
        if action == 'commit' and (not self._queried or not self.record or self.record['state'] not in {'PREPARED', 'RETRYABLE_FAILURE'}):
            return
        self._busy = True
        if not self._closed:
            self.page.set_busy(True)

        def work():
            nonlocal password
            error = ''
            finished = False
            try:
                with account_session_lock(self.profile):
                    self.record = self.store.load(self.profile)
                    if action in {'prepare', 'reprepare'}:
                        replacing = action == 'reprepare'
                        if self._cancelled.is_set():
                            raise failure('CLOSURE_CANCELLED')
                        if not isinstance(password, str) or not password or self.client.credentials is None:
                            raise failure('UNAUTHENTICATED')
                        self.record = self.store.begin(self.profile, self.client.config.api_base_url, replace_unknown=replacing)
                        if self._cancelled.is_set():
                            raise failure('CLOSURE_CANCELLED')
                        try:
                            value = self.client.prepare(password)
                        except Exception as exc:
                            code = getattr(exc, 'code', '')
                            if code == 'CLOSURE_ALREADY_COMMITTED':
                                # The server knows a submitted operation exists;
                                # never invent its missing ID or offer replacement.
                                self.record.update(state=REPLACEMENT_REFUSED, submitted=True)
                                self.store.write(self.record)
                            elif not replacing and code in {'CURRENT_PASSWORD_INVALID', 'CLOSURE_SCOPE_UNRESOLVED', 'UNAUTHENTICATED'}:
                                self.store.remove(self.record, rejected_prepare=True)
                                self.record = None
                            raise
                        self.record.update(operation_id=value['operation_id'], state='PREPARED')
                        prepared = (dict(self.record), value.pop('recovery_token'))
                        self._unsaved = prepared
                        self.store.write(self.record)
                        self.store.save_token(self.record, prepared[1])
                        prepared = None
                        self._unsaved = None
                    elif action == 'save':
                        prepared = self._unsaved
                        if not prepared:
                            raise failure('CLOSURE_RECOVERY_UNAVAILABLE')
                        self.record = dict(prepared[0])
                        self.store.write(self.record)
                        self.store.save_token(self.record, prepared[1])
                        prepared = None
                        self._unsaved = None
                    elif action in {'query', 'commit'}:
                        if not self.record or not self.record['operation_id']:
                            raise failure('CLOSURE_PREPARE_UNKNOWN')
                        token = self.store.token(self.record)
                        try:
                            if action == 'commit':
                                if self._cancelled.is_set():
                                    raise failure('CLOSURE_CANCELLED')
                                # This write is the submission boundary. A crash
                                # from here onwards requires querying this ID.
                                self.record['submitted'] = True
                                self.store.write(self.record)
                                if self._cancelled.is_set():
                                    raise failure('CLOSURE_CANCELLED')
                            state = self.client.status(self.record, token, commit=action == 'commit')
                            self.record['state'] = state
                            self.store.write(self.record)
                        finally:
                            token = None
                    elif action == 'cleanup':
                        if not self.record or self.record['state'] != 'COMPLETED':
                            raise failure()
                        if not self.record['local_cleaned']:
                            self.cleanup.run(self.record)
                            self.record['local_cleaned'] = True
                            self.store.write(self.record)
                        self.store.remove(self.record)
                        finished = True
            except Exception as exc:
                code = getattr(exc, 'code', '')
                error = 'CLOSURE_LOCAL_CLEANUP_FAILED' if action == 'cleanup' else code if code in MESSAGES else 'CLOSURE_STORAGE_FAILED' if self._unsaved else 'CLOSURE_UNCONFIRMED'
            finally:
                password = None
                prepared = None
                if self._closed:
                    self._unsaved = None
            try:
                self.delivered.emit((action, error, finished))
            except RuntimeError:
                pass

        threading.Thread(target=work, name='mentorflow-account-closure', daemon=True).start()

    def _finish(self, result):
        action, error, finished = result
        self._busy = False
        self._queried = not error and self.record is not None
        self.changed()
        if not self._closed:
            if error == 'UNAUTHENTICATED':
                self.page.set_prepare_authenticated(False)
            self.page.render(self.record, error=error, unsaved=bool(self._unsaved), finished=finished)
        if not error and not finished and self.record and self.record['state'] == 'COMPLETED':
            self.start_cleanup()
        if self._closed and not self._busy:
            self.deleteLater()
