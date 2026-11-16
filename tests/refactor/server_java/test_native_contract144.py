"""HTTP-only Java session contracts over Alembic's real PostgreSQL schema."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import re
import ssl
import threading
import httpx
import psycopg
import pytest
from contract_support144 import (LOCAL,NATIVE_PREFIX,OLD_ISSUER,FakeKeycloak144,make_tls144,
    prepare_databases144,seed_tenant144,start_java144,start_postgres144,
    stop_postgres144,stop_server144,ORG,WORKSPACE)

def client(f,backend):
    return httpx.Client(base_url=f[backend]["base_url"],verify=ssl.create_default_context(cafile=str(f["tls"]["ca"])),
        timeout=25,trust_env=False,headers={"X-Request-ID":"contract144"})

def auth(token,tenant=False):
    h={"Authorization":"Bearer "+token}
    if tenant:h.update({"X-MentorFlow-Organization-ID":ORG,"X-MentorFlow-Workspace-ID":WORKSPACE})
    return h

def code(r):
    v=r.json();return v.get("code") or v.get("error",{}).get("code")

def snapshot(r):
    transport={"date","server","connection","transfer-encoding","keep-alive"}
    h={k:v for k,v in r.headers.items() if k not in transport}
    if "set-cookie" in r.headers:h["set-cookie"]=re.sub("expires=[^;]+","expires=<time>",r.headers["set-cookie"])
    body=r.content
    if not body:return r.status_code,h,""
    v=r.json()
    if "actor" in v and "tenant" in v:
        actor=v["actor"]
        assert actor["subject_id"]=="subject144-alice"
        assert actor["issuer"]==OLD_ISSUER and actor["roles"]==["LEARNER"]
        assert actor["session_id"]=="bearer-"+hashlib.sha256((OLD_ISSUER+chr(31)+actor["token_id"]).encode()).hexdigest()
        assert v["tenant"]["organization_id"]==ORG and v["tenant"]["workspace_id"]==WORKSPACE
        for key in ("token_id","session_id","authentication_time"):actor[key]="<entropy-or-time>"
        return r.status_code,h,json.dumps(v,ensure_ascii=False,separators=(",",":")).encode().hex()
    if v.get("schema")=="mentorflow.native-session.v1":
        for k,prefix in (("access_token","mfn1_a_"),("refresh_token","mfn1_r_")):
            assert re.fullmatch(prefix+"[A-Za-z0-9_-]{43}",v[k]);v[k]=prefix+"<entropy>"
        assert type(v["expires_in"]) is int and 0<v["expires_in"]<=300
        v["expires_in"]="<remaining-seconds>"
        body=json.dumps(v,ensure_ascii=False,separators=(",",":")).encode()
    else:
        assert int(r.headers["content-length"])==len(body);h["content-length"]=r.headers["content-length"]
    return r.status_code,h,body.hex()

def db(f,backend):
    return psycopg.connect(f[backend]["env"]["MENTORFLOW_DATABASE_URL"].replace("postgresql+psycopg://","postgresql://",1))

def state(f,backend):
    with db(f,backend) as conn:
        rows=conn.execute("SELECT family_id,issuer,subject,refresh_digest,used_digests,principal,account_binding,deadline,revoked_reason FROM native_session_families").fetchall()
    result=[]
    for fid,issuer,subject,refresh,used,principal,binding,deadline,reason in rows:
        assert issuer==OLD_ISSUER and principal["issuer"]==OLD_ISSUER
        for digest in (fid,refresh,binding,*used):assert re.fullmatch("[0-9a-f]{64}",digest)
        assert len(used)==len(set(used)) and refresh not in used
        assert principal["identity_provider_session_id"] is None
        stable=dict(principal)
        for key in ("token_id","issued_at","expires_at","authentication_time"):
            assert isinstance(stable[key],str);stable[key]="<entropy-or-time>"
        result.append(dict(issuer=issuer,subject=subject,used_count=len(used),principal=stable,
            account_binding=binding,deadline=deadline,revoked_reason=reason))
    return sorted(result,key=lambda row:json.dumps(row,sort_keys=True))

def test_native144_session_rotation_revocation_concurrency_restart(native_http144):
    backend="java"
    f=native_http144;trace=[];fake=f["fake"]
    def observe(r,status):
        assert r.status_code==status,r.text
        trace.append(snapshot(r));return r
    def login(c,user):
        value=observe(c.post(NATIVE_PREFIX+"/login",json={"username":user,"password":"correct"}),200).json()
        assert value["identity"]=={"issuer":OLD_ISSUER,"subject":"subject144-"+user,"display_name":user}
        return value
    with client(f,backend) as c:
        observe(c.post(NATIVE_PREFIX+"/login",json={"username":"alice","password":"wrong"}),401)
        first=login(c,"alice")
        observe(c.get(NATIVE_PREFIX+"/session",headers=auth(first["access_token"])),200)
        observe(c.get("/api/enterprise/contract144/context",headers=auth(first["access_token"],True)),200)
        for path in ("source-whitelist","model-prices"):
            observe(c.get("/api/enterprise/catalog/"+path,headers=auth(first["access_token"],True)),200)
        observe(c.get("/api/enterprise/catalog/source-whitelist",headers=auth(first["access_token"])),403)
        rotated=observe(c.post(NATIVE_PREFIX+"/refresh",json={"refresh_token":first["refresh_token"]}),200).json()
        assert rotated["access_token"]!=first["access_token"] and rotated["refresh_token"]!=first["refresh_token"]
        observe(c.get(NATIVE_PREFIX+"/session",headers=auth(first["access_token"])),401)
        replay=observe(c.post(NATIVE_PREFIX+"/refresh",json={"refresh_token":first["refresh_token"]}),401)
        assert code(replay)=="ENTERPRISE_SESSION_REAUTH_REQUIRED"
        observe(c.get(NATIVE_PREFIX+"/session",headers=auth(rotated["access_token"])),401)
        observe(c.post(NATIVE_PREFIX+"/logout",json={"refresh_token":rotated["refresh_token"]}),200)
        expiring=login(c,"alice")
        with db(f,backend) as conn:
            conn.execute("UPDATE native_session_families SET deadline=0 WHERE refresh_digest=%s",(hashlib.sha256(expiring["refresh_token"].encode()).hexdigest(),))
        observe(c.get(NATIVE_PREFIX+"/session",headers=auth(expiring["access_token"])),401)
        observe(c.post(NATIVE_PREFIX+"/refresh",json={"refresh_token":expiring["refresh_token"]}),401)
        old=login(c,"alice");current=login(c,"alice")
        for _ in range(2):
            superseded=observe(c.get(NATIVE_PREFIX+"/session",headers=auth(old["access_token"])),401)
            assert code(superseded)=="NATIVE_SESSION_SUPERSEDED"
        observe(c.post(NATIVE_PREFIX+"/refresh",json={"refresh_token":old["refresh_token"]}),401)
        observe(c.get(NATIVE_PREFIX+"/session",headers=auth(current["access_token"])),200)
        barrier=threading.Barrier(2)
        def concurrent_login(_):
            with client(f,backend) as other:
                barrier.wait(timeout=10);return other.post(NATIVE_PREFIX+"/login",json={"username":"bob","password":"correct"})
        with ThreadPoolExecutor(max_workers=2) as pool:devices=list(pool.map(concurrent_login,range(2)))
        assert [r.status_code for r in devices]==[200,200]
        sessions=[c.get(NATIVE_PREFIX+"/session",headers=auth(r.json()["access_token"])) for r in devices]
        assert sorted(r.status_code for r in sessions)==[200,401]
        winner=devices[next(i for i,r in enumerate(sessions) if r.status_code==200)].json()
        with db(f,backend) as conn:
            assert conn.execute("SELECT count(*) FROM native_session_families WHERE subject='subject144-bob' AND revoked_reason IS NULL").fetchone()[0]==1
        fake.admin_delay=.6;barrier=threading.Barrier(2)
        def concurrent_refresh(_):
            with client(f,backend) as other:
                barrier.wait(timeout=10);return other.post(NATIVE_PREFIX+"/refresh",json={"refresh_token":winner["refresh_token"]})
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:refreshes=list(pool.map(concurrent_refresh,range(2)))
        finally:fake.admin_delay=0
        assert sorted(r.status_code for r in refreshes)==[401,401],[r.text for r in refreshes]
        assert all(code(r)=="ENTERPRISE_SESSION_REAUTH_REQUIRED" for r in refreshes)
        observe(c.get(NATIVE_PREFIX+"/session",headers=auth(winner["access_token"])),401)
        with db(f,backend) as conn:
            assert conn.execute("SELECT count(*) FROM native_session_families WHERE subject='subject144-bob' AND revoked_reason IS NULL").fetchone()[0]==0
        recoverable=login(c,"bob");fake.admin_status=503
        try:
            outage=observe(c.post(NATIVE_PREFIX+"/refresh",json={"refresh_token":recoverable["refresh_token"]}),503)
            assert code(outage)=="NATIVE_AUTH_UNAVAILABLE"
        finally:fake.admin_status=200
        with db(f,backend) as conn:
            assert conn.execute("SELECT revoked_reason FROM native_session_families WHERE refresh_digest=%s",(hashlib.sha256(recoverable["refresh_token"].encode()).hexdigest(),)).fetchone()[0] is None
        recovered=observe(c.post(NATIVE_PREFIX+"/refresh",json={"refresh_token":recoverable["refresh_token"]}),200).json()
        fake.password_created+=1
        try:observe(c.post(NATIVE_PREFIX+"/refresh",json={"refresh_token":recovered["refresh_token"]}),401)
        finally:fake.password_created-=1
        departing=login(c,"bob")
        observe(c.post(NATIVE_PREFIX+"/logout",json={"refresh_token":departing["refresh_token"]}),200)
        observe(c.get(NATIVE_PREFIX+"/session",headers=auth(departing["access_token"])),401)
        persistent=login(c,"bob")
    original=f[backend];stop_server144(original)
    f[backend]=start_java144(original["env"]["MENTORFLOW_DATABASE_URL"],fake,f["tls"],f["run"]/backend)
    f["servers"].append(f[backend])
    with client(f,backend) as c:
        observe(c.get(NATIVE_PREFIX+"/session",headers=auth(persistent["access_token"])),401)
        continued=observe(c.post(NATIVE_PREFIX+"/refresh",json={"refresh_token":persistent["refresh_token"]}),200).json()
        observe(c.get(NATIVE_PREFIX+"/session",headers=auth(continued["access_token"])),200)
    f["traces"][backend]=trace;f["states"][backend]=state(f,backend)

def test_native144_java_state_and_trace_are_recorded(native_http144):
    f=native_http144
    assert f["traces"]["java"]
    assert isinstance(f["states"]["java"],list)
    (LOCAL/"contract144-results.json").write_text(json.dumps(dict(http_steps=len(f["traces"]["java"]),
        postgres_version=f["postgres_version"],java_state=f["states"]["java"]),ensure_ascii=False,indent=2),encoding="utf-8")

def test_native144_boundaries_and_cookie_headers(native_http144):
    backend="java"
    f=native_http144;trace=[]
    with client(f,backend) as c:
        for body in ({},[],{"username":"alice"},{"username":"alice","password":1},{"username":" ","password":"correct"},
                {"username":"\u00a0","password":"correct"},{"username":" "*321+"alice","password":"correct"}):
            r=c.post(NATIVE_PREFIX+"/login",json=body);assert r.status_code==400,r.text;trace.append(snapshot(r))
        for raw,content_type in ((b"{}","text/plain"),(b"{broken","application/json"),(b"x"*4097,"application/json")):
            r=c.post(NATIVE_PREFIX+"/login",content=raw,headers={"Content-Type":content_type})
            assert r.status_code==400;trace.append(snapshot(r))
        for headers in ({"Origin":"https://localhost"},{"Cookie":"other=1"},{"Authorization":"Bearer example"}):
            r=c.post(NATIVE_PREFIX+"/login",json={"username":"alice","password":"correct"},headers=headers)
            assert r.status_code==403;trace.append(snapshot(r))
        r=c.get(NATIVE_PREFIX+"/session");assert r.status_code==401;trace.append(snapshot(r))
        for headers in ({"Cookie":"__Host-mentorflow_enterprise_session=unknown"},
                {"Cookie":"__Host-mentorflow_enterprise_session=unknown","Authorization":"Bearer example"}):
            r=c.get("/api/enterprise/catalog/source-whitelist",headers=headers)
            assert r.status_code==401;trace.append(snapshot(r))
        r=c.post(NATIVE_PREFIX+"/logout",json={"refresh_token":"unknown"})
        assert r.status_code==200;trace.append(snapshot(r))
        for method,path in (("GET","/login"),("PUT","/refresh"),("OPTIONS","/logout"),("POST","/session"),("HEAD","/session")):
            r=c.request(method,NATIVE_PREFIX+path)
            assert r.status_code==405;trace.append(snapshot(r))
    f["traces"]["boundary_"+backend]=trace

def test_native144_disabled_account_and_database_outage(native_http144):
    backend="java"
    f=native_http144;trace=[]
    with client(f,backend) as c:
        r=c.post(NATIVE_PREFIX+"/login",json={"username":"bob","password":"correct"})
        assert r.status_code==200,r.text;credentials=r.json()
        f["fake"].enabled=False
        try:
            r=c.post(NATIVE_PREFIX+"/refresh",json={"refresh_token":credentials["refresh_token"]})
            assert r.status_code==401 and code(r)=="ENTERPRISE_SESSION_REAUTH_REQUIRED";trace.append(snapshot(r))
        finally:f["fake"].enabled=True
        assert c.get(NATIVE_PREFIX+"/session",headers=auth(credentials["access_token"])).status_code==401
        r=c.post(NATIVE_PREFIX+"/login",json={"username":"bob","password":"correct"})
        assert r.status_code==200,r.text;credentials=r.json()
        name="java144"
        admin_url=f"host=127.0.0.1 port={f['pg']['port']} user=postgres dbname={name}"
        with psycopg.connect(admin_url,autocommit=True) as conn:
            conn.execute("ALTER TABLE native_session_families RENAME TO native_session_families_outage144")
        try:
            r=c.post(NATIVE_PREFIX+"/refresh",json={"refresh_token":credentials["refresh_token"]})
            assert r.status_code==503 and code(r)=="NATIVE_AUTH_UNAVAILABLE";trace.append(snapshot(r))
        finally:
            with psycopg.connect(admin_url,autocommit=True) as conn:
                conn.execute("ALTER TABLE native_session_families_outage144 RENAME TO native_session_families")
        r=c.post(NATIVE_PREFIX+"/refresh",json={"refresh_token":credentials["refresh_token"]})
        assert r.status_code==200,r.text;trace.append(snapshot(r))
    f["traces"]["fault_"+backend]=trace

def test_native144_verifier_unicode_subject_boundary(native_http144):
    f=native_http144;f["fake"]._users["unicode-invalid"]="\u00a0subject144-alice"
    try:
        with client(f,"java") as c:
            response=c.post(NATIVE_PREFIX+"/login",json={"username":"unicode-invalid","password":"correct"})
            assert response.status_code==503 and code(response)=="NATIVE_IDENTITY_INVALID",response.text
    finally:f["fake"]._users.pop("unicode-invalid",None)
