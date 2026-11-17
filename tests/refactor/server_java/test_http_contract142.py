"""HTTP contract assertions against the real local Java server."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

import httpx
import pytest

from contract_support142 import (ORG, REQUEST_ID, WORKSPACE, ProcessEndpoint142,
    LocalJwks, local_jwks142, catalog_dir142, health_state_file142)

ROOT=Path(__file__).resolve().parents[3]
ARTIFACTS=Path(os.environ.get("JAVA142_ARTIFACTS",r"D:\MentorFlow\.local\test-tmp\java142\build"))
FIXTURE_DIR=Path(__file__).resolve().parent


def _java_home():
    value=os.environ.get("JAVA_HOME") or os.environ.get("JAVA142_HOME")
    if value: return Path(value)
    matches=sorted(Path(r"D:\MentorFlow\.local\java").glob("jdk-21*"))
    return matches[-1] if matches else None


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]


def _write_processes(java=None):
    ARTIFACTS.mkdir(parents=True,exist_ok=True)
    path=ARTIFACTS/"processes.json"
    current={}
    if path.exists():
        try: current=json.loads(path.read_text(encoding="utf-8"))
        except Exception: current={}
    if java is not None:
        current["java"]={"pid":java.process.pid,"base_url":java.base_url,"log":str(java.log_path)}
    path.write_text(json.dumps(current,indent=2),encoding="utf-8")


def _stop(endpoint):
    if endpoint.process.poll() is None:
        endpoint.process.terminate()
        try: endpoint.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            endpoint.process.kill(); endpoint.process.wait(timeout=5)


def _start_java_process(local_jwks, catalog_dir, health_file, log_dir):
    java_home=_java_home()
    jar=Path(os.environ.get("JAVA142_JAR",str(ARTIFACTS/"mentorflow-server-0.1.0.jar")))
    classes=ARTIFACTS/"test-classes"
    port=_free_port(); log_path=log_dir/"java-server.log"; log=log_path.open("wb")
    env=dict(os.environ)
    env.update({"PATH":str(java_home/"bin")+os.pathsep+env.get("PATH",""),
        "MENTORFLOW_DEPLOYMENT_MODE":"ENTERPRISE_SERVER","MENTORFLOW_KEYCLOAK_ISSUER":local_jwks.issuer,
        "MENTORFLOW_KEYCLOAK_AUDIENCE":"mentorflow-api","MENTORFLOW_KEYCLOAK_CLIENT_ID":"mentorflow-desktop",
        "MENTORFLOW_KEYCLOAK_REDIRECT_URI":"http://127.0.0.1/callback","MENTORFLOW_KEYCLOAK_ALLOW_LOOPBACK_HTTP":"true",
        "MENTORFLOW_IDENTITY_SESSION_COOKIE_NAME":"__Host-mentorflow_enterprise_session",
        "MENTORFLOW_KEYCLOAK_DISCOVERY_URL":local_jwks.issuer+"/.well-known/openid-configuration",
        "MENTORFLOW_CATALOG_DIR":str(catalog_dir),"MENTORFLOW_BIND_ADDRESS":"127.0.0.1","PORT":str(port),
        "CONTRACT142_HEALTH_FILE":str(health_file)})
    command=[str(java_home/"bin"/"java.exe"),"-Dloader.path="+str(classes),
        "-Dloader.main=contract142.Contract142Main","-cp",str(jar),
        "org.springframework.boot.loader.launch.PropertiesLauncher"]
    proc=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    endpoint=ProcessEndpoint142(f"http://127.0.0.1:{port}",proc,log_path,health_file)
    deadline=time.monotonic()+45
    while time.monotonic()<deadline and proc.poll() is None:
        try:
            if httpx.get(endpoint.base_url+"/health/live",timeout=.4,trust_env=False).status_code==200:
                log.close(); return endpoint
        except Exception: time.sleep(.1)
    _stop(endpoint); log.close()
    pytest.fail("Fresh Java HTTP server failed to start: "+log_path.read_text(encoding="utf-8",errors="replace")[-6000:])


@pytest.fixture(scope="session")
def java_http142(local_jwks142,catalog_dir142,tmp_path_factory,health_state_file142):
    java_home=_java_home()
    jar=Path(os.environ.get("JAVA142_JAR",str(ARTIFACTS/"mentorflow-server-0.1.0.jar")))
    classes=ARTIFACTS/"test-classes"
    if java_home is None or not (java_home/"bin"/"java.exe").is_file():
        pytest.skip("portable Java 21 was not prepared")
    if not jar.is_file() or not classes.is_dir():
        pytest.skip("Java 142 jar/test fixture classes were not built")
    port=_free_port()
    log_dir=tmp_path_factory.mktemp("java-http142")
    log_path=log_dir/"server.log"
    log=log_path.open("wb")
    env=dict(os.environ)
    env.update({
        "PATH":str(java_home/"bin")+os.pathsep+env.get("PATH",""),
        "MENTORFLOW_DEPLOYMENT_MODE":"ENTERPRISE_SERVER",
        "MENTORFLOW_KEYCLOAK_ISSUER":local_jwks142.issuer,
        "MENTORFLOW_KEYCLOAK_AUDIENCE":"mentorflow-api",
        "MENTORFLOW_KEYCLOAK_CLIENT_ID":"mentorflow-desktop",
        "MENTORFLOW_KEYCLOAK_REDIRECT_URI":"http://127.0.0.1/callback",
        "MENTORFLOW_KEYCLOAK_ALLOW_LOOPBACK_HTTP":"true",
        "MENTORFLOW_IDENTITY_SESSION_COOKIE_NAME":"__Host-mentorflow_enterprise_session",
        "MENTORFLOW_KEYCLOAK_DISCOVERY_URL":local_jwks142.issuer+"/.well-known/openid-configuration",
        "MENTORFLOW_CATALOG_DIR":str(catalog_dir142),
        "MENTORFLOW_BIND_ADDRESS":"127.0.0.1","PORT":str(port),
        "CONTRACT142_HEALTH_FILE":str(health_state_file142),
    })
    command=[str(java_home/"bin"/"java.exe"),"-Dloader.path="+str(classes),
        "-Dloader.main=contract142.Contract142Main","-cp",str(jar),
        "org.springframework.boot.loader.launch.PropertiesLauncher"]
    proc=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    endpoint=ProcessEndpoint142(f"http://127.0.0.1:{port}",proc,log_path,health_state_file142)
    _write_processes(java=endpoint)
    deadline=time.monotonic()+45
    while time.monotonic()<deadline and proc.poll() is None:
        try:
            if httpx.get(endpoint.base_url+"/health/live",timeout=.4,trust_env=False).status_code==200: break
        except Exception: time.sleep(.1)
    else:
        _stop(endpoint); log.close()
        pytest.fail("Java HTTP server failed to start: "+log_path.read_text(encoding="utf-8",errors="replace")[-6000:])
    try: yield endpoint
    finally:
        _stop(endpoint); log.close()


@pytest.fixture(scope="session")
def java_client142(java_http142):
    with httpx.Client(base_url=java_http142.base_url,timeout=10,trust_env=False) as client: yield client


@pytest.fixture
def backend_client142(java_client142):
    return java_client142


@pytest.fixture(scope="session")
def auth_headers142(local_jwks142):
    return {"Authorization":"Bearer "+local_jwks142.token(),
        "X-MentorFlow-Organization-ID":ORG,"X-MentorFlow-Workspace-ID":WORKSPACE,
        "X-Request-ID":REQUEST_ID}


def _app_headers(response):
    names=("cache-control","etag","x-request-id","x-mentorflow-runtime-profile","content-type","content-length")
    return {name:response.headers[name] for name in names if name in response.headers}


def _java_response(java_client142,method,path,*,headers=None,expected_status=None,body=None):
    java=java_client142.request(method,path,headers=headers,content=body)
    if expected_status is not None: assert java.status_code==expected_status,java.content
    return java


def _request_id_header(): return {"X-Request-ID":REQUEST_ID}
def _auth(token,**headers):
    return {"Authorization":"Bearer "+token,"X-Request-ID":REQUEST_ID,**headers}


def test_http142_selected_backend_contract(backend_client142,local_jwks142,catalog_dir142,
                                           health_state_file142):
    """The same assertions execute against each independently launched HTTP backend."""
    good=local_jwks142.token()
    headers=_auth(good,**{"X-MentorFlow-Organization-ID":ORG,"X-MentorFlow-Workspace-ID":WORKSPACE})
    assert backend_client142.get("/health/live",headers=_request_id_header()).status_code==200
    assert backend_client142.get("/api/enterprise/catalog/source-whitelist",
                                headers=headers).content==(catalog_dir142/"source-whitelist.json").read_bytes()
    context=backend_client142.get("/api/enterprise/contract142/context",headers=headers)
    assert context.status_code==200
    assert context.json()["actor"]["membership_id"]=="membership-142"
    service_token=local_jwks142.token(subject="svc-sub142",extra_claims={
        "preferred_username":"service-account-mentorflow-desktop"})
    service_response=backend_client142.get("/api/enterprise/contract142/context",
        headers=_auth(service_token,**{"X-MentorFlow-Organization-ID":ORG,"X-MentorFlow-Workspace-ID":WORKSPACE}))
    assert service_response.status_code==200
    service_actor=service_response.json()["actor"]
    assert service_actor["actor_kind"]=="SERVICE"
    assert service_actor["subject_id"]=="service-142"
    assert service_actor["service_permissions"]==["DOCUMENT_READ"]
    no_exp=local_jwks142.token(extra_claims={"exp":None})
    missing_exp=local_jwks142.token(extra_claims={"iat":None})
    for token in (no_exp,missing_exp,local_jwks142.raw_payload_token(b"{not-json")):
        response=backend_client142.get("/api/enterprise/catalog/source-whitelist",
            headers=_auth(token,**{"X-MentorFlow-Organization-ID":ORG,"X-MentorFlow-Workspace-ID":WORKSPACE}))
        assert response.status_code==401
    for mode,expected_status,expected_code,expected_message in (
        ("closed",403,"ACCOUNT_CLOSURE_IN_PROGRESS","账号注销处理中或身份已关闭。"),
        ("unavailable",503,"CLOSURE_STORAGE_UNAVAILABLE","账号状态暂不可用。"),
    ):
        health_state_file142.write_text(json.dumps({"started":True,"postgres":True,"keycloak":True,
                                                     "closure":mode}),encoding="utf-8")
        backend_client142.put("/contract142/health-state",json={"started":True,"postgres":True,
                                "keycloak":True,"closure":mode})
        response=backend_client142.get("/api/enterprise/catalog/source-whitelist",headers=headers)
        assert response.status_code==expected_status
        assert response.json()["error"]["code"]==expected_code
        assert response.json()["error"]["message"]==expected_message
    state={"started":True,"postgres":True,"keycloak":True,"closure":"open"}
    health_state_file142.write_text(json.dumps(state),encoding="utf-8")
    backend_client142.put("/contract142/health-state",json=state)


@pytest.fixture
def jwks_unavailable_java142(tmp_path):
    java_home=_java_home()
    if java_home is None or not (java_home/"bin"/"java.exe").is_file():
        pytest.skip("portable Java 21 was not prepared")
    jar=Path(os.environ.get("JAVA142_JAR",str(ARTIFACTS/"mentorflow-server-0.1.0.jar")))
    if not jar.is_file() or not (ARTIFACTS/"test-classes").is_dir():
        pytest.skip("Java 142 jar/test fixture classes were not built")
    jwks=LocalJwks(tmp_path); jwks.server.jwks_status=503
    catalog=tmp_path/"catalog"; catalog.mkdir()
    (catalog/"source-whitelist.json").write_bytes(b'{"version":"142","sites":[]}')
    (catalog/"model-prices.json").write_bytes(b'{"version":"142","prices":[]}')
    health=tmp_path/"health-state.json"
    health.write_text('{"started":true,"postgres":true,"keycloak":true}',encoding="utf-8")
    java=_start_java_process(jwks,catalog,health,tmp_path)
    try: yield jwks,java
    finally: _stop(java); jwks.close()


def test_http142_jwks_transport_failure_is_temporary_503(jwks_unavailable_java142):
    jwks,java=jwks_unavailable_java142
    headers=_auth(jwks.token(),**{"X-MentorFlow-Organization-ID":ORG,"X-MentorFlow-Workspace-ID":WORKSPACE})
    with httpx.Client(base_url=java.base_url,timeout=10,trust_env=False) as jv:
        java_response=jv.get("/api/enterprise/catalog/source-whitelist",headers=headers)
    assert (java_response.status_code,java_response.json()["error"]["code"])==(503,"OIDC_JWKS_UNAVAILABLE")


def test_http142_health_routes_contract(java_client142,health_state_file142):
    for endpoint in ("/health/live","/health/startup","/health/ready"):
        response=_java_response(java_client142,"GET",endpoint,headers=_request_id_header())
        assert response.status_code==200
        if endpoint=="/health/ready":
            assert response.json()["status"]=="ready"
            assert {item["name"]: item["ready"] for item in response.json()["dependencies"]
                    if item["required"]}=={"postgres": True, "keycloak_discovery": True}
    for state,code in (({"started":False,"postgres":True,"keycloak":True},503),
                       ({"started":True,"postgres":False,"keycloak":True},503),
                       ({"started":True,"postgres":True,"keycloak":False},503)):
        health_state_file142.write_text(json.dumps(state),encoding="utf-8")
        # State lives in each isolated process; ask its fixture-only route to refresh it.
        java_client142.put("/contract142/health-state",json=state)
        for endpoint in ("/health/startup","/health/ready"):
            response=_java_response(java_client142,"GET",endpoint,headers=_request_id_header())
            expected=503 if endpoint=="/health/ready" or not state["started"] else 200
            assert response.status_code==expected
    state={"started":True,"postgres":True,"keycloak":True}
    health_state_file142.write_text(json.dumps(state),encoding="utf-8")
    java_client142.put("/contract142/health-state",json=state)


def test_http142_missing_and_malformed_auth_match(java_client142):
    cases=[("/api/enterprise/catalog/source-whitelist",None,401),
           ("/api/enterprise/catalog/source-whitelist",{"Authorization":"Bearer","X-Request-ID":REQUEST_ID},401),
           ("/api/enterprise/catalog/source-whitelist",{"Authorization":"Basic abc","X-Request-ID":REQUEST_ID},401),
           ("/api/enterprise/catalog/source-whitelist",{"Authorization":"Bearer a b","X-Request-ID":REQUEST_ID},401)]
    for path,headers,status in cases:
        _java_response(java_client142,"GET",path,headers=headers or _request_id_header(),expected_status=status)


def test_http142_jwt_rejections_match(java_client142,local_jwks142):
    tokens=[]
    valid=local_jwks142.token()
    head,claims,signature=valid.split(".")
    replacement="B" if signature[0]=="A" else "A"
    tokens.append(".".join((head,claims,replacement+signature[1:]))) # Always change the byte, even when it already starts with A.
    tokens.extend((
        local_jwks142.token(extra_claims={"iss":"https://wrong.example/realm"}),
        local_jwks142.token(audience="wrong-api"),
        local_jwks142.token(extra_claims={"exp":int(time.time())-100,"iat":int(time.time())-300,
                                          "auth_time":int(time.time())-300}),
        local_jwks142.token(extra_claims={"exp":int(time.time())-100}),
        local_jwks142.token(extra_claims={"exp":int(time.time())-10,"iat":int(time.time())-300,
                                          "auth_time":int(time.time())-300}),
        local_jwks142.token(extra_claims={"exp":"not-a-number"}),
        local_jwks142.token(extra_claims={"nbf":int(time.time())+300}),
        local_jwks142.token(extra_claims={"azp":"wrong-client"}),
        local_jwks142.token(extra_claims={"aud":["mentorflow-api","another-api"],"azp":None}),
        local_jwks142.token(header={"crit":["b64"]}),
        local_jwks142.token(header={"typ":"NotJWT"}),
        local_jwks142.token(header={"kid":"unknown-142"}),
        local_jwks142.token(header={"alg":"HS256"}),
        local_jwks142.token(extra_claims={"sub":None}),
        local_jwks142.raw_payload_token(b"{broken-json"),
        local_jwks142.raw_payload_token(b'{"iss":"x","aud":"mentorflow-api","iat":1,"sub":"user-142"}'),
    ))
    for token in tokens:
        headers=_auth(token,**{"X-MentorFlow-Organization-ID":ORG,"X-MentorFlow-Workspace-ID":WORKSPACE})
        _java_response(java_client142,"GET","/api/enterprise/catalog/source-whitelist",headers=headers,expected_status=401)
    inconsistent=local_jwks142.token(extra_claims={"iat":int(time.time())-300})
    headers=_auth(inconsistent,**{"X-MentorFlow-Organization-ID":ORG,"X-MentorFlow-Workspace-ID":WORKSPACE})
    _java_response(java_client142,"GET","/api/enterprise/catalog/source-whitelist",headers=headers,expected_status=500)


def test_http142_context_requires_org_and_resolves_membership(java_client142,local_jwks142):
    token=local_jwks142.token()
    headers=_auth(token,**{"X-MentorFlow-Organization-ID":ORG,"X-MentorFlow-Workspace-ID":WORKSPACE})
    response=_java_response(java_client142,"GET","/api/enterprise/contract142/context",headers=headers,expected_status=200)
    actor=response.json()["actor"]; tenant=response.json()["tenant"]
    assert actor["subject_id"]=="user-142" and actor["membership_id"]=="membership-142"
    assert actor["roles"]==["LEARNER"] and tenant["workspace_id"]==WORKSPACE
    _java_response(java_client142,"GET","/api/enterprise/contract142/context",
        headers=_auth(token),expected_status=403)
    denied=_auth(token,**{"X-MentorFlow-Organization-ID":"wrong-org","X-MentorFlow-Workspace-ID":WORKSPACE})
    _java_response(java_client142,"GET","/api/enterprise/contract142/context",headers=denied,expected_status=403)
    no_workspace=_auth(token,**{"X-MentorFlow-Organization-ID":ORG})
    response=_java_response(java_client142,"GET","/api/enterprise/contract142/context",headers=no_workspace,expected_status=200)
    assert response.json()["tenant"]["workspace_id"] is None
    # This contract rejects opaque native tokens and browser cookies in bearer auth.
    _java_response(java_client142,"GET","/api/enterprise/catalog/source-whitelist",
        headers=_auth("mfn1_a_"+"x"*64,**{"X-MentorFlow-Organization-ID":ORG,"X-MentorFlow-Workspace-ID":WORKSPACE}),expected_status=401)
    cookie={**headers,"Cookie":"__Host-mentorflow_enterprise_session=unknown-cookie"}
    _java_response(java_client142,"GET","/api/enterprise/catalog/source-whitelist",headers=cookie,expected_status=401)


def test_http142_catalog_verbatim_hash_and_conditionals(java_client142,catalog_dir142,auth_headers142):
    for name in ("source-whitelist","model-prices"):
        path="/api/enterprise/catalog/"+name
        raw=(catalog_dir142/(name+".json")).read_bytes()
        response=_java_response(java_client142,"GET",path,headers=auth_headers142,expected_status=200)
        assert response.content==raw
        etag='"'+hashlib.sha256(raw).hexdigest()+'"'
        assert response.headers["etag"]==etag
        assert response.headers["cache-control"]=="private, max-age=300"
        for value in (etag,"W/"+etag,'"other", W/'+etag,'*'):
            conditional={**auth_headers142,"If-None-Match":value}
            response=_java_response(java_client142,"GET",path,headers=conditional,expected_status=304)
            assert response.content==b""
        conditional={**auth_headers142,"If-None-Match":'"different"'}
        _java_response(java_client142,"GET",path,headers=conditional,expected_status=200)


def test_http142_catalog_rejects_bad_json_bytes_and_size(java_client142,catalog_dir142,auth_headers142):
    path="/api/enterprise/catalog/source-whitelist"
    file=catalog_dir142/"source-whitelist.json"
    original=file.read_bytes()
    max_bytes=1024*1024
    valid=b'{"version":"142"}'
    inclusive=valid+b" "*(max_bytes-len(valid))
    cases=((b'{"version":"142","v":NaN}',503),(b'{"version":"142","v":Infinity}',503),
           (b'{"version":"142","v":-Infinity}',503),(b'{"sites":[]}',503),(b'[]',503),
           (b'{"version":"142"}\xff',503),(inclusive,200),
           (valid+b" "*(max_bytes-len(valid)+1),503))
    try:
        for payload,status in cases:
            file.write_bytes(payload)
            response=_java_response(java_client142,"GET",path,headers=auth_headers142,expected_status=status)
            if status==200: assert response.content==inclusive
            else: assert response.json()["error"]["code"]=="CATALOG_INVALID"
        file.unlink()
        response=_java_response(java_client142,"GET",path,headers=auth_headers142,expected_status=404)
        assert response.json()["error"]=={"code":"CATALOG_NOT_CONFIGURED","message":"目录文件尚未配置。","details":{},"request_id":REQUEST_ID}
        file.mkdir()
        response=_java_response(java_client142,"GET",path,headers=auth_headers142,expected_status=503)
        assert response.json()["error"]["message"]=="目录文件暂不可用。"
    finally:
        if file.is_dir(): file.rmdir()
        file.write_bytes(original)


@pytest.mark.skipif(os.environ.get("JAVA142_BENCHMARK")!="1",reason="benchmark run separately after HTTP contract suite")
def test_http142_resource_benchmark(java_http142,auth_headers142):
    from benchmark142 import measure
    result={"measurement_scope":"local test fixtures; local fake JWKS and in-memory directory; catalog GET only",
            "java":measure(java_http142.base_url,java_http142.process.pid,auth_headers142,requests=1000)}
    ARTIFACTS.mkdir(parents=True,exist_ok=True)
    (ARTIFACTS/"resource142.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
