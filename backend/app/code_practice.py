from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from .active_teaching import _connect, _error, _evidence, _id, _now, _normalise


router = APIRouter(prefix="/api/active-teaching", tags=["active-code-practice"])
MAX_CODE_BYTES = 40_000
MAX_OUTPUT_CHARS = 8_000
EXECUTION_TIMEOUT_SECONDS = 2.0
SUPPORTED_LANGUAGES = {"python"}


def _init_code_schema(connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS active_practice_problem_specs (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            knowledge_point_id TEXT NOT NULL,
            material_id TEXT NOT NULL,
            title TEXT NOT NULL,
            language TEXT NOT NULL,
            entrypoint TEXT NOT NULL,
            prompt TEXT NOT NULL,
            hints TEXT NOT NULL,
            reference_solution TEXT NOT NULL,
            reference_status TEXT NOT NULL,
            reference_validation TEXT NOT NULL DEFAULT '{}',
            source_provenance TEXT NOT NULL DEFAULT '{}',
            verification_scope TEXT NOT NULL DEFAULT 'bundle_consistency',
            source_evidence TEXT NOT NULL,
            test_bundle_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(workspace_id, knowledge_point_id, language)
        );
        CREATE TABLE IF NOT EXISTS active_practice_problem_tests (
            id TEXT PRIMARY KEY,
            problem_id TEXT NOT NULL REFERENCES active_practice_problem_specs(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            kind TEXT NOT NULL,
            args_json TEXT NOT NULL,
            kwargs_json TEXT NOT NULL,
            expected_json TEXT NOT NULL,
            UNIQUE(problem_id, ordinal)
        );
        CREATE TABLE IF NOT EXISTS active_practice_code_drafts (
            session_id TEXT NOT NULL,
            problem_id TEXT NOT NULL REFERENCES active_practice_problem_specs(id) ON DELETE CASCADE,
            language TEXT NOT NULL,
            code TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(session_id, problem_id)
        );
        CREATE TABLE IF NOT EXISTS active_practice_code_submissions (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            problem_id TEXT NOT NULL REFERENCES active_practice_problem_specs(id) ON DELETE CASCADE,
            idempotency_key TEXT NOT NULL,
            mode TEXT NOT NULL,
            language TEXT NOT NULL,
            code_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            passed_count INTEGER NOT NULL,
            total_count INTEGER NOT NULL,
            hidden_passed_count INTEGER NOT NULL,
            hidden_total_count INTEGER NOT NULL,
            results TEXT NOT NULL,
            duration_ms INTEGER NOT NULL,
            hint_level INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(session_id, idempotency_key)
        );
        CREATE TABLE IF NOT EXISTS active_practice_hint_events (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            problem_id TEXT NOT NULL REFERENCES active_practice_problem_specs(id) ON DELETE CASCADE,
            level INTEGER NOT NULL,
            content TEXT NOT NULL,
            full_answer_confirmed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        """
    )
    spec_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(active_practice_problem_specs)"
        ).fetchall()
    }
    if "reference_validation" not in spec_columns:
        connection.execute(
            """ALTER TABLE active_practice_problem_specs
               ADD COLUMN reference_validation TEXT NOT NULL DEFAULT '{}'"""
        )
    if "source_provenance" not in spec_columns:
        connection.execute(
            """ALTER TABLE active_practice_problem_specs
               ADD COLUMN source_provenance TEXT NOT NULL DEFAULT '{}'"""
        )
    if "verification_scope" not in spec_columns:
        connection.execute(
            """ALTER TABLE active_practice_problem_specs
               ADD COLUMN verification_scope TEXT NOT NULL DEFAULT 'bundle_consistency'"""
        )


def _safe_json(value: Any, field: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        _error("PROBLEM_SPEC_JSON_INVALID", f"{field} 必须是 JSON 可序列化值", 422)
        raise exc


def _source_evidence(connection, point_id: str) -> list[dict[str, Any]]:
    row = connection.execute(
        "SELECT source_evidence FROM active_knowledge_points WHERE id=?", (point_id,)
    ).fetchone()
    return json.loads(row["source_evidence"]) if row else []


def _evidence_with_source_files(
    connection, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    filenames = {
        row["id"]: row["original_filename"]
        for row in connection.execute(
            "SELECT id,original_filename FROM active_materials"
        ).fetchall()
    }
    return [
        {
            **item,
            "source_file": item.get("source_file")
            or filenames.get(str(item.get("material_id") or ""), ""),
        }
        for item in items
    ]


def _problem_and_session(connection, session_id: str, problem_id: str):
    session = connection.execute(
        "SELECT * FROM active_teaching_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if not session:
        _error("ACTIVE_SESSION_NOT_FOUND", "主动教学会话不存在", 404)
    problem = connection.execute(
        "SELECT * FROM active_practice_problem_specs WHERE id=?", (problem_id,)
    ).fetchone()
    if not problem:
        _error("PROBLEM_NOT_FOUND", "可执行题目不存在", 404)
    if problem["workspace_id"] != session["workspace_id"]:
        _error(
            "PROBLEM_SESSION_WORKSPACE_MISMATCH",
            "题目与教学会话不属于同一学习空间",
            409,
        )
    return problem, session


def _public_problem(connection, row) -> dict[str, Any]:
    tests = connection.execute(
        """SELECT id,ordinal,kind,args_json,kwargs_json
           FROM active_practice_problem_tests WHERE problem_id=? ORDER BY ordinal""",
        (row["id"],),
    ).fetchall()
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "knowledge_point_id": row["knowledge_point_id"],
        "material_id": row["material_id"],
        "title": row["title"],
        "language": row["language"],
        "entrypoint": row["entrypoint"],
        "prompt": row["prompt"],
        "reference_status": row["reference_status"],
        "reference_validation": json.loads(row["reference_validation"]),
        "source_provenance": json.loads(row["source_provenance"]),
        "verification_scope": row["verification_scope"],
        "source_evidence": json.loads(row["source_evidence"]),
        "test_bundle_hash": row["test_bundle_hash"],
        "test_summary": {
            "sample": sum(item["kind"] == "sample" for item in tests),
            "boundary": sum(item["kind"] == "boundary" for item in tests),
            "hidden": sum(item["kind"] == "hidden" for item in tests),
        },
        "hints_available": len(json.loads(row["hints"])),
    }


def _load_problem_spec_document(connection, workspace_id: str, material_id: str) -> dict[str, Any]:
    material = connection.execute(
        """SELECT * FROM active_materials
           WHERE id=? AND workspace_id=? AND status='ready'""",
        (material_id, workspace_id),
    ).fetchone()
    if not material:
        _error("MATERIAL_NOT_FOUND", "题目规格资料不存在或尚未解析成功", 404)
    if Path(material["original_filename"]).suffix.lower() != ".json":
        _error(
            "PROBLEM_SPEC_FORMAT_REQUIRED",
            "可执行题目规格必须来自上传的 JSON 文件，普通资料不会被臆造为测试用例。",
            415,
        )
    try:
        payload = json.loads(Path(material["stored_path"]).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _error("PROBLEM_SPEC_PARSE_FAILED", "无法解析上传的题目规格 JSON", 422)
    if not isinstance(payload, dict):
        _error("PROBLEM_SPEC_INVALID", "题目规格 JSON 顶层必须是对象", 422)
    return payload


@router.post("/workspaces/{workspace_id}/problems/import")
def import_problem_specs(workspace_id: str, body: dict[str, Any]) -> dict[str, Any]:
    material_id = str(body.get("material_id") or "").strip()
    if not material_id:
        _error("MATERIAL_ID_REQUIRED", "请选择包含可验证测试的 JSON 资料", 422)
    with _connect() as connection:
        _init_code_schema(connection)
        payload = _load_problem_spec_document(connection, workspace_id, material_id)
        specification_evidence = [
            {**_evidence(row), "source_file": row["original_filename"]}
            for row in connection.execute(
                """SELECT chunks.*,materials.original_filename
                   FROM active_chunks AS chunks
                   JOIN active_materials AS materials ON materials.id=chunks.material_id
                   WHERE chunks.material_id=? ORDER BY chunks.ordinal""",
                (material_id,),
            ).fetchall()
        ]
        specs = payload.get("problem_specs")
        if not isinstance(specs, list) or not specs:
            _error("PROBLEM_SPECS_REQUIRED", "JSON 中缺少非空 problem_specs 数组", 422)
        points = connection.execute(
            "SELECT * FROM active_knowledge_points WHERE workspace_id=?", (workspace_id,)
        ).fetchall()
        point_by_name = {_normalise(row["name"]): row for row in points}
        imported = []
        for index, raw in enumerate(specs):
            if not isinstance(raw, dict):
                _error("PROBLEM_SPEC_INVALID", f"第 {index + 1} 个题目规格不是对象", 422)
            point_name = str(raw.get("knowledge_point_name") or raw.get("title") or "").strip()
            point = point_by_name.get(_normalise(point_name))
            if not point:
                _error(
                    "PROBLEM_POINT_NOT_FOUND",
                    f"题目规格“{point_name}”无法对应当前知识地图节点",
                    422,
                )
            language = str(raw.get("language") or "python").strip().lower()
            if language not in SUPPORTED_LANGUAGES:
                _error("CODE_LANGUAGE_UNSUPPORTED", f"暂不支持执行语言：{language}", 415)
            entrypoint = str(raw.get("entrypoint") or "solve").strip()
            if not entrypoint.isidentifier() or entrypoint.startswith("_"):
                _error("PROBLEM_ENTRYPOINT_INVALID", "entrypoint 必须是公开 Python 标识符", 422)
            tests = raw.get("tests")
            if not isinstance(tests, list) or not tests:
                _error("PROBLEM_TESTS_REQUIRED", f"“{point_name}”缺少测试用例", 422)
            kinds = {str(item.get("kind") or "") for item in tests if isinstance(item, dict)}
            if not {"sample", "boundary", "hidden"}.issubset(kinds):
                _error(
                    "PROBLEM_TEST_COVERAGE_REQUIRED",
                    f"“{point_name}”必须同时包含 sample、boundary 和 hidden 测试",
                    422,
                )
            hints = raw.get("hints")
            if not isinstance(hints, list) or len(hints) < 3:
                _error("PROBLEM_HINTS_REQUIRED", f"“{point_name}”至少需要 3 层来源提示", 422)
            reference_solution = str(raw.get("reference_solution") or "").strip()
            source_reference_status = str(raw.get("reference_status") or "").strip()
            if source_reference_status != "verified" or not reference_solution:
                _error(
                    "PROBLEM_REFERENCE_UNVERIFIED",
                    f"“{point_name}”没有由资料明确声明的参考实现，不能开始系统校验。",
                    422,
                )
            source_provenance = raw.get("source_provenance") or {}
            if not isinstance(source_provenance, dict):
                _error(
                    "PROBLEM_SOURCE_PROVENANCE_INVALID",
                    f"“{point_name}”的 source_provenance 必须是对象。",
                    422,
                )
            source_kind = str(source_provenance.get("kind") or "").strip()
            if source_kind and source_kind not in {
                "user_authored",
                "official",
                "open_source",
            }:
                _error(
                    "PROBLEM_SOURCE_KIND_INVALID",
                    f"“{point_name}”的来源类型无效：{source_kind}",
                    422,
                )
            if source_kind in {"official", "open_source"} and not str(
                source_provenance.get("url") or ""
            ).strip():
                _error(
                    "PROBLEM_SOURCE_URL_REQUIRED",
                    f"“{point_name}”的外部来源缺少 URL。",
                    422,
                )
            if source_kind and not str(
                source_provenance.get("attribution") or ""
            ).strip():
                _error(
                    "PROBLEM_SOURCE_ATTRIBUTION_REQUIRED",
                    f"“{point_name}”的来源缺少 attribution。",
                    422,
                )
            if source_kind == "open_source":
                missing = [
                    field
                    for field in ("revision", "license", "attribution")
                    if not str(source_provenance.get(field) or "").strip()
                ]
                if missing:
                    _error(
                        "PROBLEM_OPEN_SOURCE_PROVENANCE_REQUIRED",
                        f"“{point_name}”的开源来源缺少：{', '.join(missing)}。",
                        422,
                    )
            test_oracle = raw.get("test_oracle") or {}
            if not isinstance(test_oracle, dict):
                _error(
                    "PROBLEM_TEST_ORACLE_INVALID",
                    f"“{point_name}”的 test_oracle 必须是对象。",
                    422,
                )
            oracle_status = str(test_oracle.get("status") or "").strip()
            if oracle_status and oracle_status not in {
                "unverified",
                "independently_verified",
            }:
                _error(
                    "PROBLEM_TEST_ORACLE_STATUS_INVALID",
                    f"“{point_name}”的测试依据状态无效：{oracle_status}",
                    422,
                )
            independent_oracle = oracle_status == "independently_verified"
            if independent_oracle:
                if not source_kind:
                    _error(
                        "PROBLEM_INDEPENDENT_ORACLE_PROVENANCE_REQUIRED",
                        f"“{point_name}”声明了独立测试依据，但缺少可追踪来源。",
                        422,
                    )
                missing = [
                    field
                    for field in ("method", "evidence")
                    if not str(test_oracle.get(field) or "").strip()
                ]
                if missing:
                    _error(
                        "PROBLEM_TEST_ORACLE_EVIDENCE_REQUIRED",
                        f"“{point_name}”的独立测试依据缺少：{', '.join(missing)}。",
                        422,
                    )
            verification_scope = (
                "independent_oracle" if independent_oracle else "bundle_consistency"
            )
            canonical_tests = []
            seen_inputs: set[str] = set()
            for ordinal, test in enumerate(tests):
                if not isinstance(test, dict):
                    _error("PROBLEM_TEST_INVALID", "测试用例必须是对象", 422)
                kind = str(test.get("kind") or "").strip()
                if kind not in {"sample", "boundary", "hidden"}:
                    _error("PROBLEM_TEST_KIND_INVALID", f"测试类型无效：{kind}", 422)
                args = test.get("args", [])
                kwargs = test.get("kwargs", {})
                if not isinstance(args, list) or not isinstance(kwargs, dict):
                    _error("PROBLEM_TEST_INPUT_INVALID", "args 必须是数组，kwargs 必须是对象", 422)
                if "expected" not in test:
                    _error(
                        "PROBLEM_TEST_EXPECTED_REQUIRED",
                        "每个测试用例必须明确提供 expected，不能把缺失值当作 null 答案。",
                        422,
                    )
                input_hash = hashlib.sha256(
                    json.dumps(
                        {"args": args, "kwargs": kwargs},
                        sort_keys=True,
                        ensure_ascii=False,
                    ).encode()
                ).hexdigest()
                if input_hash in seen_inputs:
                    _error(
                        "PROBLEM_TEST_DUPLICATE_INPUT",
                        "测试用例输入必须唯一，不能复制同一输入伪装覆盖率。",
                        422,
                    )
                seen_inputs.add(input_hash)
                canonical_tests.append(
                    {
                        "ordinal": ordinal,
                        "kind": kind,
                        "args": args,
                        "kwargs": kwargs,
                        "expected": test.get("expected"),
                    }
                )
            bundle_hash = hashlib.sha256(
                json.dumps(canonical_tests, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            try:
                reference_result = _execute_python(
                    reference_solution, entrypoint, canonical_tests
                )
            except HTTPException as exc:
                detail = exc.detail.get("error", {}) if isinstance(exc.detail, dict) else {}
                _error(
                    "PROBLEM_REFERENCE_VALIDATION_FAILED",
                    f"“{point_name}”的参考实现未通过系统静态校验。",
                    422,
                    validator_error=detail.get("code") or "REFERENCE_STATIC_ERROR",
                )
            reference_results = reference_result.get("results") or []
            reference_passed = sum(
                bool(item.get("passed")) for item in reference_results
            )
            if (
                reference_result.get("status") != "completed"
                or reference_passed != len(canonical_tests)
            ):
                _error(
                    "PROBLEM_REFERENCE_VALIDATION_FAILED",
                    f"“{point_name}”的参考实现未通过全部系统测试，不能启用判题。",
                    422,
                    execution_status=reference_result.get("status"),
                    passed_count=reference_passed,
                    total_count=len(canonical_tests),
                )
            reference_status = "system_verified"
            reference_validation = {
                "validator": "mentorflow-python-isolated-v1",
                "source_claim": source_reference_status,
                "verification_scope": verification_scope,
                "test_oracle": test_oracle,
                "test_bundle_hash": bundle_hash,
                "passed_count": reference_passed,
                "total_count": len(canonical_tests),
                "validated_at": _now(),
            }
            now = _now()
            existing = connection.execute(
                """SELECT id FROM active_practice_problem_specs
                   WHERE workspace_id=? AND knowledge_point_id=? AND language=?""",
                (workspace_id, point["id"], language),
            ).fetchone()
            problem_id = existing["id"] if existing else _id("problem")
            connection.execute(
                """INSERT INTO active_practice_problem_specs
                    (id,workspace_id,knowledge_point_id,material_id,title,language,entrypoint,
                    prompt,hints,reference_solution,reference_status,reference_validation,
                    source_provenance,verification_scope,source_evidence,test_bundle_hash,
                    created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(workspace_id,knowledge_point_id,language) DO UPDATE SET
                     material_id=excluded.material_id,title=excluded.title,
                     entrypoint=excluded.entrypoint,prompt=excluded.prompt,hints=excluded.hints,
                     reference_solution=excluded.reference_solution,
                     reference_status=excluded.reference_status,
                     reference_validation=excluded.reference_validation,
                     source_provenance=excluded.source_provenance,
                     verification_scope=excluded.verification_scope,
                     source_evidence=excluded.source_evidence,
                     test_bundle_hash=excluded.test_bundle_hash,updated_at=excluded.updated_at""",
                (
                    problem_id,
                    workspace_id,
                    point["id"],
                    material_id,
                    str(raw.get("title") or point["name"]),
                    language,
                    entrypoint,
                    str(raw.get("prompt") or point["acceptance_method"]),
                    _safe_json([str(item) for item in hints[:3]], "hints"),
                    reference_solution,
                    reference_status,
                    _safe_json(reference_validation, "reference_validation"),
                    _safe_json(source_provenance, "source_provenance"),
                    verification_scope,
                    _safe_json(
                        _evidence_with_source_files(
                            connection,
                            _source_evidence(connection, point["id"])
                            + specification_evidence,
                        ),
                        "source_evidence",
                    ),
                    bundle_hash,
                    now,
                    now,
                ),
            )
            connection.execute("DELETE FROM active_practice_problem_tests WHERE problem_id=?", (problem_id,))
            for test in canonical_tests:
                connection.execute(
                    """INSERT INTO active_practice_problem_tests
                       (id,problem_id,ordinal,kind,args_json,kwargs_json,expected_json)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        _id("problem_test"),
                        problem_id,
                        test["ordinal"],
                        test["kind"],
                        _safe_json(test["args"], "args"),
                        _safe_json(test["kwargs"], "kwargs"),
                        _safe_json(test["expected"], "expected"),
                    ),
                )
            imported.append(
                _public_problem(
                    connection,
                    connection.execute(
                        "SELECT * FROM active_practice_problem_specs WHERE id=?", (problem_id,)
                    ).fetchone(),
                )
            )
        connection.commit()
        return {"workspace_id": workspace_id, "items": imported}


@router.get("/workspaces/{workspace_id}/problems")
def list_problems(workspace_id: str) -> dict[str, Any]:
    with _connect() as connection:
        _init_code_schema(connection)
        rows = connection.execute(
            "SELECT * FROM active_practice_problem_specs WHERE workspace_id=? ORDER BY created_at,id",
            (workspace_id,),
        ).fetchall()
        return {"workspace_id": workspace_id, "items": [_public_problem(connection, row) for row in rows]}


FORBIDDEN_CALLS = {
    "open",
    "exec",
    "eval",
    "compile",
    "__import__",
    "input",
    "breakpoint",
    "globals",
    "locals",
    "vars",
}


def _validate_python_source(code: str, entrypoint: str) -> None:
    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        _error("CODE_TOO_LARGE", "代码超过 40 KB 限制", 413)
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        _error(
            "CODE_SYNTAX_ERROR",
            f"Python 语法错误：第 {exc.lineno or 0} 行 {exc.msg}",
            422,
        )
    defined = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            _error(
                "CODE_ASYNC_UNSUPPORTED",
                "当前受限执行器只支持同步函数，不能提交 async def。",
                422,
            )
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)):
            _error("CODE_OPERATION_FORBIDDEN", "受限执行不允许导入模块或修改全局作用域", 422)
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            _error("CODE_OPERATION_FORBIDDEN", "受限执行不允许访问双下划线属性", 422)
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            _error("CODE_OPERATION_FORBIDDEN", "受限执行不允许使用双下划线名称", 422)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
            _error("CODE_OPERATION_FORBIDDEN", f"受限执行不允许调用 {node.func.id}", 422)
        if isinstance(node, ast.FunctionDef):
            defined.add(node.name)
    if entrypoint not in defined:
        _error("CODE_ENTRYPOINT_MISSING", f"代码必须定义函数 {entrypoint}", 422)


RUNNER_SOURCE = r"""
import contextlib
import io
import json
import sys
import time

payload = json.loads(sys.stdin.read())
scope = {"__builtins__": {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "float": float, "int": int, "len": len,
    "list": list, "max": max, "min": min, "range": range, "reversed": reversed,
    "set": set, "sorted": sorted, "str": str, "sum": sum, "tuple": tuple, "zip": zip,
    "Exception": Exception, "ValueError": ValueError, "TypeError": TypeError,
}}
captured = io.StringIO()
results = []
started = time.perf_counter()
try:
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        exec(compile(payload["code"], "<learner>", "exec"), scope, scope)
        fn = scope[payload["entrypoint"]]
        for case in payload["tests"]:
            case_started = time.perf_counter()
            try:
                actual = fn(*case["args"], **case["kwargs"])
                passed = actual == case["expected"]
                error = None
            except Exception as exc:
                actual = None
                passed = False
                error = f"{type(exc).__name__}: {exc}"
            results.append({
                "ordinal": case["ordinal"],
                "kind": case["kind"],
                "passed": passed,
                "actual": actual,
                "expected": case["expected"],
                "error": error,
                "duration_ms": round((time.perf_counter() - case_started) * 1000),
            })
    response = {"status": "completed", "results": results}
except Exception as exc:
    response = {"status": "runtime_error", "error": f"{type(exc).__name__}: {exc}", "results": results}
response["captured_output"] = captured.getvalue()[:8000]
response["duration_ms"] = round((time.perf_counter() - started) * 1000)
sys.stdout.write(json.dumps(response, ensure_ascii=False))
"""


def _execute_python(code: str, entrypoint: str, tests: list[dict[str, Any]]) -> dict[str, Any]:
    _validate_python_source(code, entrypoint)
    payload = json.dumps(
        {"code": code, "entrypoint": entrypoint, "tests": tests},
        ensure_ascii=False,
    )
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="mentorflow-code-") as directory:
            runner = Path(directory) / "runner.py"
            runner.write_text(RUNNER_SOURCE, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-I", "-S", str(runner)],
                input=payload,
                text=True,
                capture_output=True,
                timeout=EXECUTION_TIMEOUT_SECONDS,
                cwd=directory,
                env={},
                check=False,
            )
    except subprocess.TimeoutExpired:
        return {
            "status": "time_limit",
            "results": [],
            "captured_output": "",
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
    if completed.returncode != 0:
        return {
            "status": "runtime_error",
            "results": [],
            "captured_output": (completed.stderr or "")[:MAX_OUTPUT_CHARS],
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "status": "sandbox_error",
            "results": [],
            "captured_output": completed.stdout[:MAX_OUTPUT_CHARS],
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }


def _problem_tests(connection, problem_id: str, mode: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM active_practice_problem_tests WHERE problem_id=? ORDER BY ordinal",
        (problem_id,),
    ).fetchall()
    if mode == "run":
        rows = [row for row in rows if row["kind"] in {"sample", "boundary"}]
    return [
        {
            "ordinal": row["ordinal"],
            "kind": row["kind"],
            "args": json.loads(row["args_json"]),
            "kwargs": json.loads(row["kwargs_json"]),
            "expected": json.loads(row["expected_json"]),
        }
        for row in rows
    ]


def _max_hint_level(connection, session_id: str, problem_id: str) -> int:
    row = connection.execute(
        "SELECT MAX(level) FROM active_practice_hint_events WHERE session_id=? AND problem_id=?",
        (session_id, problem_id),
    ).fetchone()
    return int(row[0] or 0)


@router.put("/sessions/{session_id}/problems/{problem_id}/draft")
def save_code_draft(session_id: str, problem_id: str, body: dict[str, Any]) -> dict[str, Any]:
    code = str(body.get("code") or "")
    language = str(body.get("language") or "python")
    with _connect() as connection:
        _init_code_schema(connection)
        problem, _session = _problem_and_session(connection, session_id, problem_id)
        if language != problem["language"]:
            _error("CODE_LANGUAGE_MISMATCH", "代码语言与题目规格不一致", 422)
        existing = connection.execute(
            "SELECT revision FROM active_practice_code_drafts WHERE session_id=? AND problem_id=?",
            (session_id, problem_id),
        ).fetchone()
        expected = body.get("expected_revision")
        if existing and expected is not None and int(expected) != int(existing["revision"]):
            _error("DRAFT_REVISION_CONFLICT", "代码草稿版本已变化，请刷新后重试", 409)
        revision = int(existing["revision"]) + 1 if existing else 1
        connection.execute(
            """INSERT INTO active_practice_code_drafts(session_id,problem_id,language,code,revision,updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(session_id,problem_id) DO UPDATE SET
                 language=excluded.language,code=excluded.code,
                 revision=excluded.revision,updated_at=excluded.updated_at""",
            (session_id, problem_id, language, code, revision, _now()),
        )
        connection.commit()
        return {
            "session_id": session_id,
            "problem_id": problem_id,
            "language": language,
            "code": code,
            "revision": revision,
        }


@router.get("/sessions/{session_id}/problems/{problem_id}/practice")
def get_code_practice(session_id: str, problem_id: str) -> dict[str, Any]:
    with _connect() as connection:
        _init_code_schema(connection)
        problem, _session = _problem_and_session(connection, session_id, problem_id)
        draft = connection.execute(
            "SELECT * FROM active_practice_code_drafts WHERE session_id=? AND problem_id=?",
            (session_id, problem_id),
        ).fetchone()
        submissions = []
        for row in connection.execute(
            """SELECT * FROM active_practice_code_submissions
               WHERE session_id=? AND problem_id=? ORDER BY created_at,id""",
            (session_id, problem_id),
        ).fetchall():
            item = dict(row)
            item["results"] = json.loads(item["results"])
            submissions.append(item)
        return {
            "problem": _public_problem(connection, problem),
            "draft": dict(draft) if draft else None,
            "hint_level": _max_hint_level(connection, session_id, problem_id),
            "submissions": submissions,
        }


def _execute_submission(
    connection, session_id: str, problem_id: str, body: dict[str, Any], mode: str
) -> dict[str, Any]:
    problem, session = _problem_and_session(connection, session_id, problem_id)
    idempotency_key = str(body.get("idempotency_key") or "").strip()
    if not idempotency_key:
        _error("IDEMPOTENCY_KEY_REQUIRED", "运行或提交代码必须提供 idempotency_key", 422)
    code = str(body.get("code") or "")
    code_hash = hashlib.sha256(code.encode()).hexdigest()
    replay = connection.execute(
        """SELECT * FROM active_practice_code_submissions
           WHERE session_id=? AND idempotency_key=?""",
        (session_id, idempotency_key),
    ).fetchone()
    if replay:
        if (
            replay["problem_id"] != problem_id
            or replay["mode"] != mode
            or replay["code_hash"] != code_hash
        ):
            _error(
                "IDEMPOTENCY_PAYLOAD_CONFLICT",
                "同一 idempotency_key 已用于不同题目、操作或代码内容。",
                409,
            )
        item = dict(replay)
        item["results"] = json.loads(item["results"])
        item["idempotent_replay"] = True
        return item
    tests = _problem_tests(connection, problem_id, mode)
    result = _execute_python(code, problem["entrypoint"], tests)
    raw_results = result.get("results") or []
    passed_count = sum(bool(item.get("passed")) for item in raw_results)
    total_count = len(tests)
    hidden_total = sum(item["kind"] == "hidden" for item in tests)
    hidden_passed = sum(
        item.get("kind") == "hidden" and bool(item.get("passed")) for item in raw_results
    )
    if result["status"] == "completed":
        status = "accepted" if total_count > 0 and passed_count == total_count else "wrong_answer"
    else:
        status = result["status"]
    public_results = []
    for item in raw_results:
        if item.get("kind") == "hidden":
            public_results.append(
                {
                    "ordinal": item["ordinal"],
                    "kind": "hidden",
                    "passed": bool(item["passed"]),
                    "error": "runtime_error" if item.get("error") else None,
                    "duration_ms": item.get("duration_ms"),
                }
            )
        else:
            public_results.append(item)
    hint_level = _max_hint_level(connection, session_id, problem_id)
    submission_id = _id("code_submission")
    connection.execute(
        """INSERT INTO active_practice_code_submissions
           (id,session_id,problem_id,idempotency_key,mode,language,code_hash,status,
            passed_count,total_count,hidden_passed_count,hidden_total_count,results,
            duration_ms,hint_level,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            submission_id,
            session_id,
            problem_id,
            idempotency_key,
            mode,
            problem["language"],
            code_hash,
            status,
            passed_count,
            total_count,
            hidden_passed,
            hidden_total,
            json.dumps(public_results, ensure_ascii=False),
            int(result.get("duration_ms") or 0),
            hint_level,
            _now(),
        ),
    )
    if mode == "submit" and status == "accepted":
        score = 0.9 if hint_level == 0 else 0.7
        independently_passed = hint_level == 0
        evidence_id = _id("mastery")
        connection.execute(
            """INSERT INTO active_mastery_evidence
               (id,session_id,knowledge_point_id,evidence_type,score,passed,evidence,citations,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                evidence_id,
                session_id,
                problem["knowledge_point_id"],
                "code_hidden_tests",
                score,
                int(independently_passed),
                json.dumps(
                    {
                        "submission_id": submission_id,
                        "test_bundle_hash": problem["test_bundle_hash"],
                        "passed_count": passed_count,
                        "total_count": total_count,
                        "hidden_passed_count": hidden_passed,
                        "hidden_total_count": hidden_total,
                        "hint_level": hint_level,
                    },
                    ensure_ascii=False,
                ),
                problem["source_evidence"],
                _now(),
            ),
        )
        if independently_passed:
            connection.execute(
                "UPDATE active_knowledge_points SET mastery=MAX(mastery,?) WHERE id=?",
                (score, problem["knowledge_point_id"]),
            )
        state = json.loads(session["state"])
        state.setdefault("code_verified_points", {})[problem["knowledge_point_id"]] = {
            "submission_id": submission_id,
            "independent": independently_passed,
            "hint_level": hint_level,
            "score": score,
            "test_bundle_hash": problem["test_bundle_hash"],
        }
        state["next_action"] = (
            "schedule_delayed_review" if independently_passed else "retry_without_hints"
        )
        connection.execute(
            "UPDATE active_teaching_sessions SET state=?,updated_at=? WHERE id=?",
            (json.dumps(state, ensure_ascii=False), _now(), session_id),
        )
    connection.commit()
    return {
        "id": submission_id,
        "session_id": session_id,
        "problem_id": problem_id,
        "mode": mode,
        "status": status,
        "passed_count": passed_count,
        "total_count": total_count,
        "hidden_passed_count": hidden_passed,
        "hidden_total_count": hidden_total,
        "results": public_results,
        "duration_ms": int(result.get("duration_ms") or 0),
        "captured_output": (
            ""
            if mode == "submit" and hidden_total
            else str(result.get("captured_output") or "")[:MAX_OUTPUT_CHARS]
        ),
        "hint_level": hint_level,
        "idempotent_replay": False,
    }


@router.post("/sessions/{session_id}/problems/{problem_id}/run")
def run_code(session_id: str, problem_id: str, body: dict[str, Any]) -> dict[str, Any]:
    with _connect() as connection:
        _init_code_schema(connection)
        return _execute_submission(connection, session_id, problem_id, body, "run")


@router.post("/sessions/{session_id}/problems/{problem_id}/submit")
def submit_code(session_id: str, problem_id: str, body: dict[str, Any]) -> dict[str, Any]:
    with _connect() as connection:
        _init_code_schema(connection)
        return _execute_submission(connection, session_id, problem_id, body, "submit")


@router.post("/sessions/{session_id}/problems/{problem_id}/hints")
def request_code_hint(
    session_id: str, problem_id: str, body: dict[str, Any] = Body(default_factory=dict)
) -> dict[str, Any]:
    with _connect() as connection:
        _init_code_schema(connection)
        problem, _session = _problem_and_session(connection, session_id, problem_id)
        current = _max_hint_level(connection, session_id, problem_id)
        next_level = min(4, current + 1)
        hints = json.loads(problem["hints"])
        if next_level < 4:
            content = hints[next_level - 1]
            confirmed = False
        else:
            confirmed = body.get("confirm_full_answer") is True
            if not confirmed:
                _error(
                    "FULL_ANSWER_CONFIRMATION_REQUIRED",
                    "第四级提示会公开完整参考实现，必须由用户明确确认。",
                    409,
                )
            content = problem["reference_solution"]
        connection.execute(
            """INSERT INTO active_practice_hint_events
               (id,session_id,problem_id,level,content,full_answer_confirmed,created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                _id("hint"),
                session_id,
                problem_id,
                next_level,
                content,
                int(confirmed),
                _now(),
            ),
        )
        connection.commit()
        return {
            "session_id": session_id,
            "problem_id": problem_id,
            "level": next_level,
            "content": content,
            "full_answer_confirmed": confirmed,
        }
