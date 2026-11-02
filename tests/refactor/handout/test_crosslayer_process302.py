from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from scripts.handout.crosslayer_process import extract


def write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_process302_proves_packaged_user_runtime_and_apply_handoff(tmp_path):
    write(tmp_path, "scripts/build_learning_installer.py", '''
def main():
    runtime = base + ["--name", "UserRuntime"]
    run(runtime + [ROOT / "refactor/desktop/delivery/per_user_install.py"])
    a = Analysis([ROOT / "refactor/desktop/packaged_main.py"])
    exe = EXE(name='MentorFlow')
''')
    write(tmp_path, "refactor/desktop/delivery/per_user_install.py", '''
import subprocess
def handoff(root):
    process = subprocess.Popen([str(root / 'Updater/UserRuntime.exe'), 'apply'])
def coordinate(root, intent):
    pass
def _execute(args):
    if args.action == 'apply': return coordinate(args.root, args.intent)
def main():
    args = parser.parse_args()
    return _execute(args)
''')
    result = extract(tmp_path)
    assert not any(row["name"].startswith("package ") for row in result["connections"])
    apply = next(row for row in result["connections"] if row["name"] == "UserRuntime.exe apply")
    assert apply["source"]["function"] == "handoff"
    assert apply["target"]["function"] == "coordinate"
    assert any(row["function"] == "_execute" for row in apply["via"])
    assert any(row["file"] == "scripts/build_learning_installer.py" for row in apply["via"])


def test_process302_tracks_app_launch_repair_branches_and_tts_default_factory(tmp_path):
    write(tmp_path, "scripts/build_learning_installer.py", '''
def main():
    runtime = base + ["--name", "UserRuntime"]
    run(runtime + [ROOT / "refactor/desktop/delivery/per_user_install.py"])
    a = Analysis([ROOT / "refactor/desktop/packaged_main.py"])
    exe = EXE(name='MentorFlow')
''')
    write(tmp_path, "refactor/desktop/delivery/per_user_install.py", '''
import subprocess
def verify_recovery_runtime(root):
    return runtime / 'UserRuntime.exe'
def launch_health(root):
    process = subprocess.Popen([str(app / 'MentorFlow.exe'), '--install-mode', 'per_user'])
def handoff_repair(root, shell=False):
    runtime = verify_recovery_runtime(root)
    command = [str(runtime), 'repair-update' if shell else 'repair-installation', '--root', str(root)]
    process = subprocess.Popen(command)
def _execute(args):
    if args.action == 'repair-update': return repair_update_shell(args.root)
    if args.action == 'repair-installation': return repair_installation(args.root)
def main():
    args = parser.parse_args()
    return _execute(args)
''')
    write(tmp_path, "refactor/desktop/packaged_main.py", '''
def main():
    args = parser.parse_args()
    from refactor.desktop.account_shell_main import main as run_shell
    return run_shell(install_root=root)
''')
    write(tmp_path, "refactor/desktop/account_shell_main.py", '''
def main():
    pass
''')
    write(tmp_path, "refactor/desktop/delivery/per_user_launcher.py", '''
def repair_update_shell(root):
    pass
''')
    write(tmp_path, "refactor/desktop/delivery/per_user_update_core.py", '''
def repair_installation(root):
    pass
''')
    write(tmp_path, "refactor/learning/voice/tts/runtime.py", '''
class TtsRuntime:
    def __init__(self, process_factory=None):
        self._factory = process_factory or subprocess.Popen
    def _launch(self):
        self._process = self._factory([str(engine_python), '-B', str(Path(__file__).with_name('engine_server.py')), '--config', str(config_path)])
''')
    write(tmp_path, "refactor/learning/voice/tts/engine_server.py", '''
def main():
    pass
''')
    result = extract(tmp_path)
    by_name = {row["name"]: row for row in result["connections"]}
    assert by_name["MentorFlow.exe --install-mode per_user"]["target"]["function"] == "main"
    assert by_name["UserRuntime.exe repair-update (shell=True)"]["target"]["function"] == "repair_update_shell"
    assert by_name["UserRuntime.exe repair-installation (shell=False)"]["target"]["function"] == "repair_installation"
    tts = by_name["TTS engine_server.py --config"]
    assert tts["target"]["file"].endswith("engine_server.py")
    assert "仅当未注入 process_factory" in tts["rule"]


def test_process302_does_not_guess_dynamic_external_executable(tmp_path):
    write(tmp_path, "refactor/learning/tools.py", '''
import subprocess
import asyncio
def run_tool(executable, args):
    return subprocess.Popen([executable, *args])
def ordinary_methods(obj, coro):
    obj.run()
    asyncio.run(coro)
''')
    result = extract(tmp_path)
    assert result["connections"] == []
    assert len(result["unresolved"]) == 1
    assert result["unresolved"][0]["source"]["function"] == "run_tool"
    assert "目标由运行时可执行文件" in result["unresolved"][0]["reason"]


def test_process302_requires_literal_tts_target_and_default_popen(tmp_path):
    engine = "def main():\n    pass\n"
    write(tmp_path, "refactor/learning/voice/tts/engine_server.py", engine)
    variants = [
        '''
class TtsRuntime:
    def __init__(self, process_factory=None):
        self._factory = process_factory or subprocess.Popen
    def _launch(self):
        self._process = self._factory([python, str(Path(__file__).with_name(engine_name)), '--config', config])
''',
        '''
class TtsRuntime:
    def __init__(self, process_factory):
        self._factory = process_factory
    def _launch(self):
        self._process = self._factory([python, str(Path(__file__).with_name('engine_server.py')), '--config', config])
''',
    ]
    for index, source in enumerate(variants):
        root = tmp_path / str(index)
        write(root, "refactor/learning/voice/tts/engine_server.py", engine)
        write(root, "refactor/learning/voice/tts/runtime.py", source)
        assert extract(root)["connections"] == []
