from __future__ import annotations

from .inventory.models import KnowledgeType, TeachingDepth, stable_hash
from .map_v2.compiler import KnowledgeMapCompiler
from .map_v2.models import (
    KnowledgeEdgeType,
    KnowledgeMapSnapshot,
    KnowledgeRelationCandidate,
    RelationBasis,
)
from .pipeline_v2.handoff import (
    ConceptVerificationStatus,
    KnowledgeMapConceptCandidate,
    KnowledgeMapEvidenceLink,
    KnowledgeMapHandoffCandidate,
    RepairLineage,
)
from .pipeline_v2.progress import PipelineStatus


JAVA_DATABASE_DEMO_ALIAS = "java-database-demo"
JAVA_DATABASE_DEMO_SOURCE = "demo://mentorflow/java-database"


def _concept(
    key: str,
    name: str,
    *,
    section: str,
    aliases: tuple[str, ...] = (),
    confidence: float = 0.94,
    repair: bool = False,
    knowledge_type: KnowledgeType = KnowledgeType.CONCEPT,
) -> KnowledgeMapConceptCandidate:
    evidence_id = f"demo-evidence:{key}"
    lineage = (
        RepairLineage(
            patch_id=f"demo-repair:{key}",
            stable_candidate_id=f"demo-candidate:{key}",
            proposed_item_id=f"demo-concept:{key}",
            classification="TERMINOLOGY_ALIGNMENT",
            verifier_version="demo-verifier.v1",
        ),
    ) if repair else ()
    return KnowledgeMapConceptCandidate(
        stable_concept_id=f"demo-concept:{key}",
        canonical_name=name,
        raw_names=aliases,
        knowledge_type=knowledge_type,
        teaching_depth=TeachingDepth.CORE_EXPLANATION,
        evidence_links=(
            KnowledgeMapEvidenceLink(
                evidence_id=evidence_id,
                part_index=1,
                part_cid=f"java-database:{key}",
                start_ms=0,
                end_ms=1_000,
                original_evidence_hash=stable_hash(
                    "demo-evidence-hash",
                    {"key": key, "section": section},
                ),
            ),
        ),
        dependencies=(),
        hints=(f"Java 数据库实战 / {section}",),
        confidence=confidence,
        verification_status=(
            ConceptVerificationStatus.VERIFIED_REPAIR_CANDIDATE
            if repair
            else ConceptVerificationStatus.VERIFIED_COVERED
        ),
        repair_lineage=lineage,
    )


def _relation(
    key: str,
    source: str,
    target: str,
    edge_type: KnowledgeEdgeType,
    *,
    confidence: float = 0.93,
) -> KnowledgeRelationCandidate:
    uncertain = edge_type is KnowledgeEdgeType.POSSIBLE_PREREQUISITE
    return KnowledgeRelationCandidate(
        relation_id=f"demo-relation:{key}",
        source_reference=source,
        target_reference=target,
        edge_type=edge_type,
        basis=(
            RelationBasis.CANDIDATE_PREREQUISITE_HINT
            if uncertain
            else RelationBasis.VERIFIED_EXPLICIT_RELATION
        ),
        evidence_ids=(f"demo-relation-evidence:{key}",),
        confidence=confidence,
    )


def _java_database_concepts() -> tuple[KnowledgeMapConceptCandidate, ...]:
    return (
        _concept("java-types", "Java 类型系统", section="第 1 章 Java 基础"),
        _concept("relational-model", "关系模型", section="第 2 章 数据库基础"),
        _concept("sql", "SQL 查询", section="第 3 章 SQL"),
        _concept("jdbc", "JDBC 驱动与 DriverManager", section="第 4 章 JDBC"),
        _concept("connection", "数据库连接 Connection", section="第 4 章 JDBC"),
        _concept(
            "prepared-statement",
            "PreparedStatement 参数化查询",
            section="第 5 章 安全查询",
            aliases=("参数化 SQL",),
            confidence=0.98,
        ),
        _concept("result-set", "ResultSet 结果集", section="第 5 章 查询结果"),
        _concept("transaction", "JDBC 事务", section="第 6 章 事务", confidence=0.97),
        _concept(
            "isolation",
            "事务隔离级别",
            section="第 6 章 事务隔离",
            aliases=("Isolation Level",),
            repair=True,
        ),
        _concept("connection-pool", "数据库连接池", section="第 7 章 性能"),
        _concept("dao", "DAO 数据访问层", section="第 8 章 架构"),
        _concept("index", "数据库索引策略", section="第 9 章 查询优化"),
        _concept(
            "migration",
            "数据库迁移工具",
            section="附录 数据库迁移",
            confidence=0.86,
        ),
    )


def _java_database_relations() -> tuple[KnowledgeRelationCandidate, ...]:
    return (
        _relation(
            "types-jdbc",
            "Java 类型系统",
            "JDBC 驱动与 DriverManager",
            KnowledgeEdgeType.PREREQUISITE,
        ),
        _relation("model-sql", "关系模型", "SQL 查询", KnowledgeEdgeType.PREREQUISITE),
        _relation(
            "jdbc-connection",
            "JDBC 驱动与 DriverManager",
            "数据库连接 Connection",
            KnowledgeEdgeType.PREREQUISITE,
        ),
        _relation(
            "sql-prepared",
            "SQL 查询",
            "PreparedStatement 参数化查询",
            KnowledgeEdgeType.PREREQUISITE,
        ),
        _relation(
            "connection-prepared",
            "数据库连接 Connection",
            "PreparedStatement 参数化查询",
            KnowledgeEdgeType.SUPPORTS,
        ),
        _relation(
            "prepared-result",
            "PreparedStatement 参数化查询",
            "ResultSet 结果集",
            KnowledgeEdgeType.PREREQUISITE,
        ),
        _relation(
            "connection-transaction",
            "数据库连接 Connection",
            "JDBC 事务",
            KnowledgeEdgeType.PREREQUISITE,
        ),
        _relation("transaction-isolation", "JDBC 事务", "事务隔离级别", KnowledgeEdgeType.PREREQUISITE),
        _relation("pool-connection", "数据库连接池", "数据库连接 Connection", KnowledgeEdgeType.RELATED),
        _relation(
            "dao-prepared",
            "PreparedStatement 参数化查询",
            "DAO 数据访问层",
            KnowledgeEdgeType.SUPPORTS,
        ),
        _relation(
            "index-prepared",
            "数据库索引策略",
            "PreparedStatement 参数化查询",
            KnowledgeEdgeType.POSSIBLE_PREREQUISITE,
            confidence=0.62,
        ),
    )


def build_java_database_demo_snapshot(
    *,
    pipeline_status: PipelineStatus = PipelineStatus.COMPLETE,
    include_cycle: bool = False,
) -> KnowledgeMapSnapshot:
    concepts = _java_database_concepts()
    relations = list(_java_database_relations())
    if include_cycle:
        relations.append(
            _relation(
                "isolation-connection-cycle",
                "事务隔离级别",
                "数据库连接 Connection",
                KnowledgeEdgeType.PREREQUISITE,
            )
        )
    handoff = KnowledgeMapHandoffCandidate(
        handoff_id="demo-handoff:java-database:v1",
        source_identity=JAVA_DATABASE_DEMO_SOURCE,
        source_revision="demo-java-database-v1",
        verification_status=pipeline_status,
        concepts=concepts,
        pipeline_receipt_id="demo-pipeline-receipt:java-database:v1",
    )
    known_evidence_ids = tuple(
        sorted(
            {
                *(
                    link.evidence_id
                    for concept in concepts
                    for link in concept.evidence_links
                ),
                *(
                    evidence_id
                    for relation in relations
                    for evidence_id in relation.evidence_ids
                ),
            }
        )
    )
    return KnowledgeMapCompiler(clock=lambda: 0.0).compile_handoff(
        handoff,
        known_evidence_ids=known_evidence_ids,
        relations=tuple(relations),
        critical_concept_references=(
            "数据库连接 Connection",
            "PreparedStatement 参数化查询",
            "JDBC 事务",
        ),
    ).snapshot


__all__ = [
    "JAVA_DATABASE_DEMO_ALIAS",
    "JAVA_DATABASE_DEMO_SOURCE",
    "build_java_database_demo_snapshot",
]
