import { useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  type ReactFlowInstance,
} from "@xyflow/react";
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  Focus,
  GitBranch,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";

import "@xyflow/react/dist/style.css";
import "./verifiedKnowledgeMap.css";

import { verifiedKnowledgeMapApi, type VerifiedKnowledgeMapApi } from "./api";
import {
  availableSourceFilters,
  buildConceptDetail,
  filterKnowledgeGraph,
  mapKnowledgeGraphToReactFlow,
  type KnowledgeFlowEdge,
  type KnowledgeFlowNode,
} from "./graphAdapter";
import type {
  KnowledgeMapEnvelope,
  KnowledgeMapFilterMode,
  KnowledgeMapFilters,
} from "./types";

const DEFAULT_FILTERS: KnowledgeMapFilters = {
  mode: "all",
  source: "",
  verificationStatus: "all",
};

type Props = {
  mapReference?: string;
  initialEnvelope?: KnowledgeMapEnvelope;
  api?: VerifiedKnowledgeMapApi;
};

const FILTER_LABELS: Array<[KnowledgeMapFilterMode, string]> = [
  ["all", "全部"],
  ["critical", "关键概念"],
  ["uncertain", "不确定关系"],
  ["orphan", "孤立概念"],
];

export function VerifiedKnowledgeMapExperience({
  mapReference,
  initialEnvelope,
  api = verifiedKnowledgeMapApi,
}: Props) {
  const [envelope, setEnvelope] = useState<KnowledgeMapEnvelope | null>(
    initialEnvelope || null,
  );
  const [readState, setReadState] = useState<"loading" | "ready" | "error">(
    initialEnvelope ? "ready" : "loading",
  );
  const [error, setError] = useState("");
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [layoutGeneration, setLayoutGeneration] = useState(0);
  const flowRef = useRef<ReactFlowInstance<
    KnowledgeFlowNode,
    KnowledgeFlowEdge
  > | null>(null);

  useEffect(() => {
    if (initialEnvelope) return undefined;
    const controller = new AbortController();
    setReadState("loading");
    setError("");
    const request = mapReference
      ? api.getSnapshot(mapReference, controller.signal)
      : api.getDemo(controller.signal);
    void request.then((payload) => {
      setEnvelope(payload);
      setReadState("ready");
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : "知识图读取失败");
      setReadState("error");
    });
    return () => controller.abort();
  }, [api, initialEnvelope, mapReference]);

  const snapshot = envelope?.snapshot;
  const sourceFilters = useMemo(
    () => snapshot ? availableSourceFilters(snapshot) : [],
    [snapshot],
  );
  const filteredGraph = useMemo(
    () => snapshot
      ? filterKnowledgeGraph(snapshot, filters)
      : { nodes: [], edges: [] },
    [filters, snapshot],
  );
  const flowGraph = useMemo(
    () => mapKnowledgeGraphToReactFlow(filteredGraph),
    [filteredGraph, layoutGeneration],
  );
  const selectedNode = snapshot?.nodes.find(
    (node) => node.concept_id === selectedNodeId,
  );
  const selectedDetail = useMemo(
    () => snapshot && selectedNode
      ? buildConceptDetail(snapshot, selectedNode)
      : null,
    [selectedNode, snapshot],
  );

  if (readState === "loading") {
    return (
      <main className="verified-map verified-map--center" role="status">
        <RefreshCw aria-hidden className="verified-map__spin" />
        <p>正在读取 Verified Knowledge Map…</p>
      </main>
    );
  }
  if (readState === "error" || !envelope || !snapshot) {
    return (
      <main className="verified-map verified-map--center" role="alert">
        <ShieldAlert aria-hidden />
        <h1>知识图暂时不可用</h1>
        <p>{error || "没有读取到知识图数据"}</p>
      </main>
    );
  }

  const cycleBlocked = Boolean(
    snapshot.diagnostics.cycles.length
    || snapshot.diagnostics.cycle_diagnostics_truncated,
  );
  const renderBlocked = !envelope.display.renderable
    || snapshot.status === "FAILED"
    || cycleBlocked;

  return (
    <main className="verified-map" data-testid="verified-knowledge-map">
      <header className="verified-map__header">
        <div>
          <span className="verified-map__eyebrow">VERIFIED KNOWLEDGE MAP</span>
          <h1>知识之间为什么这样连接</h1>
          <p>已验证关系、不确定关系和证据来源分层显示。</p>
        </div>
        <div className="verified-map__badges">
          {envelope.display.is_demo && (
            <span className="verified-map__demo" data-testid="demo-data-badge">
              <Database aria-hidden /> DEMO DATA · Java / 数据库
            </span>
          )}
          <span className={`verified-map__status is-${snapshot.status.toLowerCase()}`}>
            {snapshot.status}
          </span>
        </div>
      </header>

      {snapshot.status === "PARTIAL" && (
        <section className="verified-map__banner is-partial" role="status">
          <AlertTriangle aria-hidden />
          <div>
            <strong>资料尚未完全验证</strong>
            <span>当前地图只代表已经完成验证的部分，请勿视为完整资料。</span>
          </div>
        </section>
      )}

      {renderBlocked && (
        <section className="verified-map__blocked" role="alert">
          <ShieldAlert aria-hidden />
          <h2>{cycleBlocked
            ? "发现知识依赖冲突，需要检查关系"
            : envelope.display.user_message}</h2>
          <p>为避免把错误依赖展示成正常学习路径，本图已停止渲染。</p>
          <dl>
            <div><dt>循环诊断</dt><dd>{snapshot.diagnostics.cycles.length}</dd></div>
            <div><dt>拒绝关系</dt><dd>{snapshot.diagnostics.rejected_relations.length}</dd></div>
            <div>
              <dt>待解析提示</dt>
              <dd>{snapshot.diagnostics.unresolved_relation_hints.length}</dd>
            </div>
          </dl>
        </section>
      )}

      {!renderBlocked && snapshot.nodes.length === 0 && (
        <section className="verified-map__empty" role="status">
          <GitBranch aria-hidden />
          <h2>还没有可显示的知识节点</h2>
          <p>上传并验证资料后，这里会出现可追溯的知识关系。</p>
        </section>
      )}

      {!renderBlocked && snapshot.nodes.length > 0 && (
        <>
          <section className="verified-map__toolbar" aria-label="知识图筛选与布局">
            <div className="verified-map__filter-tabs" role="group" aria-label="快捷筛选">
              {FILTER_LABELS.map(([mode, label]) => (
                <button
                  key={mode}
                  type="button"
                  className={filters.mode === mode ? "is-active" : ""}
                  onClick={() => setFilters((current) => ({ ...current, mode }))}
                >
                  {label}
                </button>
              ))}
            </div>
            <label>
              来源
              <select
                aria-label="来源"
                value={filters.source}
                onChange={(event) => setFilters((current) => ({
                  ...current,
                  source: event.target.value,
                }))}
              >
                <option value="">全部来源</option>
                {sourceFilters.map((source) => (
                  <option value={source} key={source}>{source}</option>
                ))}
              </select>
            </label>
            <label>
              验证状态
              <select
                aria-label="验证状态"
                value={filters.verificationStatus}
                onChange={(event) => setFilters((current) => ({
                  ...current,
                  verificationStatus: event.target.value as
                    KnowledgeMapFilters["verificationStatus"],
                }))}
              >
                <option value="all">全部状态</option>
                <option value="VERIFIED_COVERED">已验证</option>
                <option value="VERIFIED_REPAIR_CANDIDATE">修复后验证</option>
              </select>
            </label>
            <div className="verified-map__layout-actions">
              <button
                type="button"
                onClick={() => void flowRef.current?.fitView({ padding: 0.16, duration: 260 })}
              >
                <Focus aria-hidden /> 适应视图
              </button>
              <button
                type="button"
                onClick={() => setLayoutGeneration((value) => value + 1)}
              >
                <RefreshCw aria-hidden /> 重新布局
              </button>
            </div>
          </section>

          <section className="verified-map__legend" aria-label="关系图例">
            <span className="is-prerequisite">前置依赖</span>
            <span className="is-supports">支持</span>
            <span className="is-related">相关</span>
            <span className="is-uncertain">可能前置 · 未验证</span>
          </section>

          {snapshot.nodes.length >= 250 && (
            <p className="verified-map__large-note" role="note">
              当前为大型知识图（{snapshot.nodes.length} 个节点）。布局约 {flowGraph.layoutMs.toFixed(1)} ms，
              映射约 {flowGraph.mappingMs.toFixed(1)} ms；缩放、拖动画布和筛选仍可用，实际流畅度取决于设备。
            </p>
          )}

          <div className="verified-map__workspace">
            <section className="verified-map__canvas" aria-label="知识图画布">
              {typeof ResizeObserver === "undefined" ? (
                <div className="verified-map__fallback" role="status">
                  当前浏览器缺少图形尺寸观察能力，已保留可访问节点列表。
                  <ul>
                    {filteredGraph.nodes.slice(0, 100).map((node) => (
                      <li key={node.concept_id}>
                        <button type="button" onClick={() => setSelectedNodeId(node.concept_id)}>
                          {node.canonical_name}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <ReactFlow
                  nodes={flowGraph.nodes}
                  edges={flowGraph.edges}
                  fitView
                  minZoom={0.08}
                  maxZoom={2.2}
                  panOnDrag
                  zoomOnScroll
                  zoomOnPinch
                  nodesDraggable={false}
                  nodesConnectable={false}
                  onInit={(instance) => { flowRef.current = instance; }}
                  onNodeClick={(_event, node) => setSelectedNodeId(node.id)}
                  aria-label="Verified Knowledge Map 图形"
                >
                  <Background gap={22} size={1} color="#d4ded9" />
                  <Controls showInteractive={false} />
                  <MiniMap
                    pannable
                    zoomable
                    nodeColor={(node) => (
                      (node.data as { node?: { criticality?: string } })
                        ?.node?.criticality === "CRITICAL"
                        ? "#b43b42"
                        : "#318567"
                    )}
                  />
                </ReactFlow>
              )}
            </section>

            <aside className="verified-map__detail" aria-label="概念详情面板">
              {!selectedDetail ? (
                <div className="verified-map__detail-empty">
                  <CheckCircle2 aria-hidden />
                  <h2>选择一个知识节点</h2>
                  <p>点击图中节点查看验证状态、证据和上下游关系。</p>
                </div>
              ) : (
                <>
                  <span className="verified-map__detail-kicker">CONCEPT DETAIL</span>
                  <h2>{selectedDetail.concept.canonical_name}</h2>
                  <dl className="verified-map__facts">
                    <div>
                      <dt>置信度</dt>
                      <dd>{Math.round(selectedDetail.concept.confidence * 100)}%</dd>
                    </div>
                    <div><dt>验证状态</dt><dd>{selectedDetail.concept.verification_status}</dd></div>
                    <div><dt>关键程度</dt><dd>{selectedDetail.concept.criticality}</dd></div>
                    <div><dt>证据数量</dt><dd>{selectedDetail.evidence_count}</dd></div>
                  </dl>
                  <section>
                    <h3>资料来源</h3>
                    <ul>
                      {selectedDetail.source_references.map((source) => (
                        <li key={source.evidence_id}>
                          <b>{source.source_reference}</b>
                          <small>{source.evidence_id}</small>
                          {source.href
                            ? <a href={source.href}>打开原资料</a>
                            : <button type="button" disabled title="等待原资料路由接入">原资料跳转待接入</button>}
                        </li>
                      ))}
                    </ul>
                  </section>
                  <section>
                    <h3>修复谱系</h3>
                    {selectedDetail.repair_lineage.length ? (
                      <ul>{selectedDetail.repair_lineage.map((item) => (
                        <li key={item.patch_id}>{item.classification} · {item.verifier_version}</li>
                      ))}</ul>
                    ) : <p>无修复记录，来自原始验证结果。</p>}
                  </section>
                  <div className="verified-map__relations">
                    <section>
                      <h3>前置概念</h3>
                      {selectedDetail.prerequisites.length
                        ? (
                          <ul>{selectedDetail.prerequisites.map((item) => (
                            <li key={item.concept_id}>{item.canonical_name}</li>
                          ))}</ul>
                        )
                        : <p>无直接前置概念。</p>}
                    </section>
                    <section>
                      <h3>下游依赖</h3>
                      {selectedDetail.dependents.length
                        ? (
                          <ul>{selectedDetail.dependents.map((item) => (
                            <li key={item.concept_id}>{item.canonical_name}</li>
                          ))}</ul>
                        )
                        : <p>暂无直接下游依赖。</p>}
                    </section>
                  </div>
                </>
              )}
            </aside>
          </div>
        </>
      )}
    </main>
  );
}
