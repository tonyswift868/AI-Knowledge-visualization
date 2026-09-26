"""Account-scoped background settings work and password reauthentication cleanup."""
import threading

from PySide6.QtCore import QObject, QTimer, Signal

from .account_protocol import AccountError
from .account_security_client import SECURITY_MESSAGES
from .login_session import account_session_lock

class AccountSettingsController(QObject):
    delivered = Signal(object)

    def __init__(self, page, client, session, identity, credentials, *, password_result, session_expired, session_epoch, parent=None):
        super().__init__(parent)
        self.page, self.client, self.session = page, client, session
        self.identity, self.credentials = identity, credentials
        self.password_result, self.session_expired = password_result, session_expired
        self._session_epoch = session_epoch
        self.password_epoch = None
        self._generation = 0
        self._closed = False
        self._busy = False
        self._operation = None
        self.delivered.connect(self._finish)
        page.read_requested.connect(lambda: self.start('read'))
        page.code_requested.connect(lambda email: self.start('request', email=email))
        page.verify_requested.connect(lambda code: self.start('verify', code=code))
        page.password_requested.connect(lambda current, new: self.start('password', current=current, new=new))
        page.revocation_requested.connect(lambda: self.start('revoke'))
        page.cancel_email_requested.connect(self.cancel_email)
        page.closed.connect(self.close)

    @property
    def password_pending(self):
        return self._busy and self._operation in ('password', 'revoke')

    def cancel_email(self):
        self._generation += 1
        self.client.cancel()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.cancel_email()
        self.client.transport.close()
        if not self._busy:
            self.deleteLater()

    def start(self, operation, **payload):
        if self._closed or self._busy:
            payload.clear()
            return
        self._busy = True
        self._operation = operation
        if operation in ('password', 'revoke'):
            self.password_epoch = self._session_epoch()
        generation = self._generation
        self.page.set_busy(operation)

        def work():
            try:
                with account_session_lock(self.identity.profile()):
                    if self._closed or (operation not in ('password', 'revoke') and generation != self._generation):
                        raise AccountError('ACCOUNT_ACTION_CANCELLED', '操作已取消。')
                    if operation in ('password', 'revoke'):
                        value = self._password(payload, revoke=operation == 'revoke')
                    elif operation == 'read':
                        value = self.client.read()
                    elif operation == 'request':
                        value = self.client.request_code(payload['email'])
                    else:
                        value = self.client.verify_code(payload['code'])
                result = (generation, operation, value, '')
            except Exception as exc:
                code = getattr(exc, 'code', 'ACCOUNT_RESULT_UNCERTAIN')
                result = (generation, operation, None, code if code in SECURITY_MESSAGES else 'ACCOUNT_RESULT_UNCERTAIN')
            finally:
                payload.clear()
            try:
                self.delivered.emit(result)
            except RuntimeError:
                pass

        threading.Thread(target=work, name='mentorflow-account-settings', daemon=True).start()

    def _password(self, payload, *, revoke=False):
        profile = self.identity.profile()
        # Write before the network request. Even response loss/process shutdown
        # leaves an account-specific marker preventing old saved login material.
        self.session.set_reauthentication_required(profile)
        previous_pending = self.session.revocation_pending(profile)
        state = 'SESSION_REVOCATION_PENDING' if revoke else 'PASSWORD_CHANGE_OUTCOME_UNKNOWN'
        self.session.set_revocation_pending(profile, state)
        try:
            state = self.client.revoke_sessions() if revoke else self.client.change_password(payload['current'], payload['new'])
        except AccountError as exc:
            if exc.code in {'CURRENT_PASSWORD_INVALID', 'NEW_PASSWORD_SAME_AS_CURRENT', 'PASSWORD_INPUT_INVALID', 'ACCOUNT_ACTION_CANCELLED'}:
                self.session.set_revocation_pending(profile, previous_pending)
                self.session.set_reauthentication_required(profile, False)
                raise
            # An ambiguous result is never retried and never reported as success.
        confirmed = state in {'PASSWORD_CHANGED', 'PASSWORD_CHANGED_REVOCATION_PENDING'}
        remote = state in {'PASSWORD_CHANGED', 'SESSION_REVOCATION_CONFIRMED'}
        metadata_saved = True
        try:
            self.session.set_revocation_pending(profile, '' if remote else state)
        except Exception:
            metadata_saved = False
        cleaned = self._cleanup() and metadata_saved
        return {'state': state, 'confirmed': confirmed, 'cleaned': cleaned, 'remote_confirmed': remote}

    def _cleanup(self):
        try:
            cleaned = self.session.invalidate_saved_login(self.identity.profile())
        except Exception:
            cleaned = False
        try:
            self.credentials.logout(self.identity.profile())
        except Exception:
            cleaned = False
        return cleaned

    def _finish(self, result):
        generation, operation, value, error = result
        self._busy = False
        # Password consequences belong to the captured account even if the
        # settings page closed or the user switched accounts during the request.
        if operation in ('password', 'revoke') and not error:
            self.password_result(self, value)
        elif not self._closed and (operation in ('password', 'revoke') or generation == self._generation):
            self.page.complete(operation, value, error)
            if error == 'UNAUTHENTICATED':
                self.session_expired(self)
            elif error in {'EMAIL_ALREADY_VERIFIED', 'EMAIL_ALREADY_BOUND'} and operation in ('request', 'verify'):
                # Another session may have verified/bound the mailbox. Reconcile
                # once with the existing read endpoint; never resend a code.
                QTimer.singleShot(0, lambda: self.start('read') if generation == self._generation else None)
        elif not self._closed:
            self.page.set_busy(None)
        if self._closed:
            self.deleteLater()
