"""Offline migration-contract checks for RAG source chunk metadata."""
import hashlib
import importlib.util
import shutil
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "rag_source_chunks_0048", ROOT / "refactor/server/migrations/versions/0048_rag_source_chunks.py"
)
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


def test_source_chunk_backfill_only_marks_unpromoted_canonical_layout(schema_template, tmp_path):
    database = tmp_path / "rag-source-chunks.sqlite"
    shutil.copyfile(schema_template, database)
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO rag_documents(account_scope,document_id,content_sha256,title,source,total_chunks,status) "
                "VALUES ('a','plain',:digest1,'plain','fixture',2,'ready'), "
                "('a','promoted',:digest2,'promoted','fixture',2,'ready')"
            ), {"digest1": "f" * 64, "digest2": "e" * 64})
            connection.execute(text(
                "INSERT INTO rag_chunks(account_scope,document_id,seq,text,page,section,embedding) "
                "VALUES ('a','plain',0,'你好','1','intro',X'00'), "
                "('a','plain',1,'world','2','body',X'00'), "
                "('a','promoted',0,'你好','legacy-page','legacy-intro',X'00'), "
                "('a','promoted',1,'world','legacy-page','legacy-body',X'00')"
            ))
            connection.execute(text(
                "INSERT INTO rag_document_aliases(account_scope,requested_document_id,canonical_document_id,"
                "total_chunks,chunk_digests) VALUES ('a','old-alias','plain',1,:digest)"
            ), {"digest": hashlib.sha256("你好world".encode()).hexdigest()})
            connection.execute(text(
                "CREATE TABLE rag_document_upload_manifests (account_scope TEXT, document_id TEXT, "
                "total_chunks INTEGER, chunk_digests TEXT, PRIMARY KEY(account_scope,document_id))"
            ))
            # Matching hashes do not prove that promoted source page/section
            # metadata matches the physical canonical's metadata.
            hashes = ",".join(hashlib.sha256(value.encode()).hexdigest() for value in ("你好", "world"))
            connection.execute(text(
                "INSERT INTO rag_document_upload_manifests VALUES ('a','promoted',2,:hashes)"
            ), {"hashes": hashes})
            connection.execute(text(
                "CREATE TABLE rag_source_chunks (account_scope TEXT, document_id TEXT, canonical_document_id TEXT, "
                "seq INTEGER,total_chunks INTEGER,text_sha256 TEXT,byte_length INTEGER,start_byte INTEGER,end_byte INTEGER,"
                "page TEXT,section TEXT,PRIMARY KEY(account_scope,document_id,seq))"
            ))
            migration._backfill(connection)
            rows = connection.execute(text(
                "SELECT document_id,seq,text_sha256,byte_length,start_byte,end_byte,page,section "
                "FROM rag_source_chunks ORDER BY seq"
            )).all()
            assert rows == [
                ("plain", 0, hashlib.sha256("你好".encode()).hexdigest(), 6, 0, 6, "1", "intro"),
                ("plain", 1, hashlib.sha256(b"world").hexdigest(), 5, 6, 11, "2", "body"),
            ]
            assert connection.scalar(text(
                "SELECT count(*) FROM rag_source_chunks WHERE document_id IN ('old-alias','promoted')"
            )) == 0
    finally:
        engine.dispose()
