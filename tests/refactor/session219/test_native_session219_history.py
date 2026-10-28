import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import subprocess

import sqlalchemy as sa
import pytest

from refactor.server.enterprise.identity.native import NativeSessions, NATIVE_FAMILIES
from refactor.server.enterprise.identity.models import AuthenticatedPrincipal
from refactor.server.enterprise.identity.oidc import IdentityError

ISSUER = 'https://identity.example.test/realms/mentorflow'


class Verifier:
    async def authenticate(self, username, password):
        now = datetime.now(timezone.utc)
        return AuthenticatedPrincipal(issuer=ISSUER, subject=username, audiences=('mentorflow-api',),
            token_id='test', issued_at=now, expires_at=now + timedelta(minutes=5),
            authentication_time=now, authentication_strength='password_verified', client_id='mentorflow-desktop')
    async def aclose(self): pass


def test_persistent_access_authentication_does_not_load_refresh_history():
    engine = sa.create_engine('sqlite://', poolclass=sa.pool.StaticPool, connect_args={'check_same_thread': False})
    NATIVE_FAMILIES.create(engine)
    statements = []
    sa.event.listen(engine, 'before_cursor_execute', lambda conn, cursor, statement, params, context, many: statements.append(statement.lower()))
    service = NativeSessions(Verifier(), ISSUER, engine=engine, account_check=lambda principal: asyncio.sleep(0, result='binding'))
    async def exercise():
        pair = await service.login('fixture', 'synthetic', 'peer')
        replay = 'mfn1_r_' + 'z' * 43
        used = [service.digest(replay), *[f'{i:064x}' for i in range(1000)]]
        with engine.begin() as conn:
            conn.execute(sa.update(NATIVE_FAMILIES).values(used_digests=used))
        statements.clear()
        assert service.authenticate(pair['access_token']).subject == 'fixture'
        auth_sql = [s for s in statements if 'native_session_families' in s]
        assert auth_sql and all('used_digests' not in s for s in auth_sql)
        with pytest.raises(IdentityError):
            await service.refresh(replay)
        with engine.connect() as conn:
            row = conn.execute(sa.select(NATIVE_FAMILIES.c.used_digests, NATIVE_FAMILIES.c.revoked_reason)).one()
        assert len(row.used_digests) == 1001 and row.revoked_reason == 'REVOKED'
    try:
        asyncio.run(exercise())
    finally:
        engine.dispose()


@pytest.mark.parametrize('change', ['revoked', 'rotated'])
def test_python_access_authentication_rechecks_persisted_family_state(change):
    engine = sa.create_engine('sqlite://', poolclass=sa.pool.StaticPool, connect_args={'check_same_thread': False})
    NATIVE_FAMILIES.create(engine)
    service = NativeSessions(Verifier(), ISSUER, engine=engine,
        account_check=lambda principal: asyncio.sleep(0, result='binding'))
    async def exercise():
        pair = await service.login('fixture', 'synthetic', 'peer')
        values = {'revoked_reason': 'REVOKED'} if change == 'revoked' else {'refresh_digest': 'b' * 64}
        with engine.begin() as conn:
            conn.execute(sa.update(NATIVE_FAMILIES).values(**values))
        with pytest.raises(IdentityError):
            service.authenticate(pair['access_token'])
    try:
        asyncio.run(exercise())
    finally:
        engine.dispose()


@pytest.mark.parametrize('change', ['missing', 'revoked', 'rotated', 'superseded'])
def test_python_access_authentication_clears_stale_family_caches(change):
    engine = sa.create_engine('sqlite://', poolclass=sa.pool.StaticPool, connect_args={'check_same_thread': False})
    NATIVE_FAMILIES.create(engine)
    service = NativeSessions(Verifier(), ISSUER, engine=engine,
        account_check=lambda principal: asyncio.sleep(0, result='binding'))
    async def exercise():
        pair = await service.login('fixture', 'synthetic', 'peer')
        pair = await service.refresh(pair['refresh_token'])
        digest = service.digest(pair['access_token'])
        fid = service.access[digest]
        current_refresh = service.families[fid].refresh_hash
        cached_hashes = service.families[fid].used_refreshes | {current_refresh}
        with engine.begin() as conn:
            if change == 'missing':
                conn.execute(sa.delete(NATIVE_FAMILIES).where(NATIVE_FAMILIES.c.family_id == fid))
            elif change == 'revoked':
                conn.execute(sa.update(NATIVE_FAMILIES).where(NATIVE_FAMILIES.c.family_id == fid).values(revoked_reason='REVOKED'))
            elif change == 'rotated':
                conn.execute(sa.update(NATIVE_FAMILIES).where(NATIVE_FAMILIES.c.family_id == fid).values(refresh_digest='b' * 64))
            else:
                conn.execute(sa.update(NATIVE_FAMILIES).where(NATIVE_FAMILIES.c.family_id == fid).values(revoked_reason='NATIVE_SESSION_SUPERSEDED'))
        with pytest.raises(IdentityError) as caught:
            service.authenticate(pair['access_token'])
        assert bool(caught.value.code == 'NATIVE_SESSION_SUPERSEDED') == (change == 'superseded')
        assert fid not in service.families and digest not in service.access
        assert not (cached_hashes & service.refreshes.keys())
        if change == 'superseded':
            assert digest in service.superseded
        with engine.connect() as conn:
            saved = conn.execute(sa.select(NATIVE_FAMILIES.c.refresh_digest, NATIVE_FAMILIES.c.revoked_reason)
                .where(NATIVE_FAMILIES.c.family_id == fid)).first()
        if change == 'missing':
            assert saved is None
        elif change == 'revoked':
            assert saved.refresh_digest == current_refresh and saved.revoked_reason == 'REVOKED'
        elif change == 'rotated':
            assert saved.refresh_digest == 'b' * 64 and saved.revoked_reason is None
        else:
            assert saved.refresh_digest == current_refresh and saved.revoked_reason == 'NATIVE_SESSION_SUPERSEDED'
    try:
        asyncio.run(exercise())
    finally:
        engine.dispose()


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
    dependency_cp = Path(os.environ.get('JAVA156_CLASSPATH_FILE',
        r'D:\MentorFlow\.local\test-tmp\java-cache\test-classpath.txt'))
    assert dependency_cp.is_file(), f'Java classpath file missing: {dependency_cp}'
    build_classes = Path(os.environ.get('JAVA156_BUILD',
        r'D:\MentorFlow\.local\test-tmp\java-cache\build')) / 'classes'
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
