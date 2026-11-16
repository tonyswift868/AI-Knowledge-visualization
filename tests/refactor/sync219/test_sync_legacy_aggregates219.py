"""The Java server rejects retired product aggregate records cleanly."""
from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest



RETIRED = ("product_plan_state", "product_review_state", "product_account_settings")
KINDS = {"product_plan_state": "plan", "product_review_state": "review",
         "product_account_settings": "setting"}


def _payload(table):
    values = {"id": f"retired-{table}", "state": "{}"}
    spec = {"kind": KINDS[table]}
    return spec, values, {"product_record": {"schema": "mentorflow.product-record.v1",
            "record_type": table, "values": values}}


def _legacy_entity_id(table, values):
    key_json = json.dumps([values["id"]], ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(key_json.encode()).hexdigest()
    return f"{table}:{digest}"


def test_sync_legacy_aggregates219_java_rejects_retired_types(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    java = Path(r"D:\MentorFlow\.local\java\jdk-21.0.12.1+1\bin\java.exe")
    javac = java.with_name("javac.exe")
    cache = Path(os.environ.get("JAVA_MAVEN_REPO", r"D:\MentorFlow\.local\java\m2")) / "com/fasterxml/jackson/core"
    jackson_jars = [cache / "jackson-databind/2.21.4/jackson-databind-2.21.4.jar",
                    cache / "jackson-core/2.21.4/jackson-core-2.21.4.jar",
                    cache / "jackson-annotations/2.21/jackson-annotations-2.21.jar"]
    assert java.is_file() and javac.is_file() and all(path.is_file() for path in jackson_jars)
    cp = os.pathsep.join(map(str, jackson_jars))
    classes = tmp_path / "classes"
    (classes / "sync").mkdir(parents=True)
    shutil.copy2(repo / "refactor/server-java/src/main/resources/sync/SyncDefinitions.json",
                 classes / "sync/SyncDefinitions.json")
    package = repo / "refactor/server-java/src/main/java/win/clannad/mentorflow/sync"
    harness = tmp_path / "LegacyAggregate219.java"
    harness.write_text('''
import java.util.*;
import win.clannad.mentorflow.sync.SyncPayload;
public class LegacyAggregate219 {
  public static void main(String[] args) {
    for (int i=0; i<args.length; i+=3) {
      String table=args[i], kind=args[i+1], entity=args[i+2];
      Map<String,Object> values = new LinkedHashMap<>();
      values.put("id", "retired-" + table); values.put("state", "{}");
      Map<String,Object> rec = Map.of("schema", "mentorflow.product-record.v1",
          "record_type", table, "values", values);
      try {
        SyncPayload.project(kind, entity, Map.of("product_record", rec), false);
        throw new AssertionError(table + " was accepted");
      } catch (win.clannad.mentorflow.sync.SyncFailure expected) {
        if (!"PRODUCT_SYNC_RECORD_FIELDS".equals(expected.code)) throw expected;
      } catch (Exception ex) { throw new RuntimeException(ex); }
    }
  }
}
''', encoding="utf-8")
    args = []
    for table in RETIRED:
        spec, values, _ = _payload(table)
        args.extend((table, spec["kind"], _legacy_entity_id(table, values)))
    sources = [package / name for name in ("SyncPayload.java", "SyncFailure.java", "SyncJson.java")]
    subprocess.run([str(javac), "-encoding", "UTF-8", "-cp", cp, "-d", str(classes),
                    *map(str, sources), str(harness)], check=True)
    subprocess.run([str(java), "-cp", str(classes) + os.pathsep + cp,
                    "LegacyAggregate219", *args], check=True, capture_output=True, text=True)
