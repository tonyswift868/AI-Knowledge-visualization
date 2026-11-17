"""Native Java access checks keep refresh history out of the access query."""
from pathlib import Path
import os
import subprocess
import sys

_SERVER_JAVA = Path(__file__).resolve().parents[1] / "server_java"
if str(_SERVER_JAVA) not in sys.path:
    sys.path.insert(0, str(_SERVER_JAVA))
from java_build156 import default_build_path, default_classpath_path


def test_java_access_family_query_excludes_refresh_history():
    source = (Path(__file__).resolve().parents[3]
        / 'refactor/server-java/src/main/java/win/clannad/mentorflow/account/NativeSessionService.java').read_text(encoding='utf-8')
    method = source.split('private Family findFamily(String familyId)', 1)[1].split('private Family family(ResultSet row)', 1)[0]
    assert 'used_digests' not in method
    assert 'principal::text' in method and 'revoked_reason' in method


def test_java_access_authentication_uses_lightweight_family_row():
    repo = Path(__file__).resolve().parents[3]
    task_tmp = Path(os.environ['TEMP']) / 'java-harness'
    classes = task_tmp / 'classes'
    classes.mkdir(parents=True, exist_ok=True)
    jdk = Path(r'D:\MentorFlow\.local\java\jdk-21.0.12.1+1')
    cache_root = Path(os.environ.get('JAVA_CACHE_ROOT',
        r'D:\MentorFlow\.local\test-tmp\java-cache'))
    dependency_cp = Path(os.environ.get('JAVA156_CLASSPATH_FILE',
        str(default_classpath_path(cache_root))))
    assert dependency_cp.is_file(), f'Java classpath file missing: {dependency_cp}'
    build_classes = Path(os.environ.get('JAVA156_BUILD',
        str(default_build_path(cache_root)))) / 'classes'
    cp = os.pathsep.join((str(build_classes), dependency_cp.read_text(encoding='utf-8').strip()))
    harness = repo / 'tests/refactor/server_java/java/session219/NativeSession219Main.java'
    service = repo / 'refactor/server-java/src/main/java/win/clannad/mentorflow/account/NativeSessionService.java'
    env = os.environ.copy()
    env.update(TEMP=str(task_tmp), TMP=str(task_tmp), TMPDIR=str(task_tmp))
    subprocess.run([str(jdk / 'bin/javac.exe'), '-encoding', 'UTF-8', '-cp', cp, '-d', str(classes),
                    str(service), str(harness)], cwd=repo, env=env, check=True, capture_output=True, text=True)
    runtime_cp = os.pathsep.join((str(classes), cp))
    result = subprocess.run([str(jdk / 'bin/java.exe'), '-cp', runtime_cp, 'session219.NativeSession219Main'],
                            cwd=repo, env=env, check=True, capture_output=True, text=True)
    assert 'excluded used_digests' in result.stdout
