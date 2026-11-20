"""The updater's stale-App check must accept the exit chain that 2.0.49/2.0.50 really write."""
from refactor.desktop.delivery import per_user_install as install

# Copied from the 2026-11-18 user log (BUG-279): EVENT_LOOP_RETURNED carries no pid in these versions.
OLD_CHAIN = [
    '{"at": "2026-11-19T02:08:55", "level": "INFO", "module": "refactor.runtime", "event": "window", "state": "HIDDEN", "window": "LEARNING_MAIN", "window_id": 30}',
    '{"at": "2026-11-19T02:08:55", "level": "INFO", "module": "refactor.runtime", "event": "exit", "reason": "UPDATE_HANDOFF", "pid": 14500}',
    '{"at": "2026-11-19T02:08:55", "level": "INFO", "module": "refactor.runtime", "event": "exit", "code": "UPDATE_HANDOFF", "reason": "ABOUT_TO_QUIT", "pid": 14500}',
    '{"at": "2026-11-19T02:08:55", "level": "INFO", "module": "refactor.runtime", "event": "exit", "reason": "EVENT_LOOP_RETURNED", "exit_code": 0}',
    '{"event": "update", "phase": "runtime_operation_failed", "action": "launch", "error_type": "RecoverableError", "detail": "close the current App before continuing"}',
]


def _write(tmp_path, lines):
    root = tmp_path / 'MentorFlow'
    log = install.target(root) / 'Data' / 'logs' / 'mentorflow.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return root


def test_old_version_chain_without_pid_on_return_is_confirmed(tmp_path):
    assert install.app_exit_confirmed(_write(tmp_path, OLD_CHAIN), 14500)


def test_new_version_chain_with_pid_is_confirmed(tmp_path):
    lines = OLD_CHAIN[:3] + ['{"event": "exit", "reason": "EVENT_LOOP_RETURNED", "exit_code": 0, "pid": 14500}']
    assert install.app_exit_confirmed(_write(tmp_path, lines), 14500)


def test_other_pid_chain_is_not_ours(tmp_path):
    assert not install.app_exit_confirmed(_write(tmp_path, OLD_CHAIN), 999)


def test_normal_close_chain_is_confirmed(tmp_path):
    # Once ABOUT_TO_QUIT and EVENT_LOOP_RETURNED are logged, the App has
    # finished Qt shutdown even when no update handoff was requested.
    lines = ['{"event": "exit", "reason": "USER_MAIN_CLOSE", "pid": 14500}',
             '{"event": "exit", "reason": "ABOUT_TO_QUIT", "code": "USER_MAIN_CLOSE", "pid": 14500}',
             '{"event": "exit", "reason": "EVENT_LOOP_RETURNED", "exit_code": 0}']
    assert install.app_exit_confirmed(_write(tmp_path, lines), 14500)


def test_return_before_handoff_does_not_count(tmp_path):
    lines = [OLD_CHAIN[3], OLD_CHAIN[1], OLD_CHAIN[2]]
    assert not install.app_exit_confirmed(_write(tmp_path, lines), 14500)
