import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ActiveTeachingChainView } from "../features/active-teaching/ActiveTeachingChainView";

const mapFor = (revision: number, id = `revision-${revision}`, extra: any = {}) => ({
  id,
  revision,
  status: "ready",
  nodes: [{ id: `node-${revision}`, label: `节点 ${revision}`, layer: "material", kind: "concept", evidence: [] }],
  edges: [],
  ...extra,
});

const snapshotFor = (receipt: any = {}) => ({
  workspace_id: "scope-workspace",
  materials: [
    { id: "material-anchor", original_filename: "课程讲义.md", size_bytes: 10, sha256: "a".repeat(64), chunk_count: 1, status: "ready" },
    { id: "material-support", original_filename: "配套笔记.md", size_bytes: 10, sha256: "b".repeat(64), chunk_count: 1, status: "ready" },
  ],
  receipt: receipt === null ? null : {
    learning_goal: "掌握资料",
    current_level: "beginner",
    start_date: "2026-08-21",
    deadline: "2026-12-31",
    study_weekdays: [0, 1, 2, 3, 4, 5, 6],
    fixed_rest_weekdays: [],
    unavailable_dates: [],
    special_date_minutes: {},
    allocation_mode: "system",
    review_rounds: 2,
    preferred_learning_style: "主动讲解",
    teacher_persona: "",
    teacher_persona_id: "",
    learner_interests: "",
    notes: "",
    material_ids: ["material-anchor", "material-support"],
    anchor_material_id: "material-anchor",
    supporting_material_ids: ["material-support"],
    scope_policy: { mode: "single_anchor", additive: true },
    ...receipt,
  },
  knowledge_graph: { knowledge_points: [], edges: [] },
  plan: { id: "plan-current", tasks: [], calendar: [] },
  session: null,
});

function installFetch(options: {
  snapshot?: any;
  staged?: boolean;
  blockers?: any[];
  stagedCandidates?: any[];
} = {}) {
  let activeRevision = 1;
  let stagedRevisionId = "staged-revision";
  let stagedCandidates = options.stagedCandidates || [];
  const calls: Array<[string, RequestInit | undefined]> = [];
  const snapshot = options.snapshot || snapshotFor();
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    calls.push([String(url), init]);
    const endpoint = String(url);
    let payload: any = {};
    if (endpoint.endsWith("/snapshot")) payload = snapshot;
    else if (endpoint.includes("/workspaces?")) payload = { items: [] };
    else if (endpoint.endsWith("/teacher-personas")) payload = { items: [] };
    else if (endpoint.endsWith("/problems")) payload = { items: [] };
    else if (endpoint.endsWith("/material-capabilities")) payload = {};
    else if (endpoint.endsWith("/materials/url") && init?.method === "POST") {
      const body = JSON.parse(String(init.body || "{}"));
      payload = String(body.url || "").includes("textbook")
        ? { id: "textbook-material", material_ids: ["textbook-material"], status: "ingested" }
        : { id: "notes-material", material_ids: ["notes-material"], status: "ingested" };
    }
    else if (endpoint.includes("/knowledge-map/candidates") && init?.method === "POST") {
      stagedRevisionId = "staged-child";
      stagedCandidates = [];
      payload = { revision_id: stagedRevisionId, revision: 3, map_status: "staged" };
    }
    else if (endpoint.includes("/knowledge-map/candidates")) {
      const revision = new URL(endpoint, "http://localhost").searchParams.get("revision");
      payload = { candidates: revision === "staged-revision" ? stagedCandidates : [] };
    }
    else if (endpoint.includes("/knowledge-map?revision=active")) payload = mapFor(activeRevision);
    else if (endpoint.includes("/knowledge-map?revision=staged-revision")) payload = mapFor(2, "staged-revision");
    else if (endpoint.includes("/knowledge-map?revision=staged-child")) payload = mapFor(3, "staged-child", { activation_blockers: [] });
    else if (endpoint.endsWith("/knowledge-map/jobs") && init?.method === "POST") {
      payload = options.staged
        ? {
            id: "scope-job",
            status: "ready",
            activation_required: true,
            current_revision_id: "revision-1",
            staged_revision_id: "staged-revision",
            activation_blockers: options.blockers || [],
            diff: { nodes_added: ["node-2"], nodes_removed: [] },
          }
        : { id: "scope-job", status: "ready", revision_id: "revision-1" };
    } else if (endpoint.includes("/knowledge-map/jobs/scope-job")) {
      payload = options.staged
        ? { id: "scope-job", status: "ready", activation_required: true, current_revision_id: "revision-1", staged_revision_id: "staged-revision", activation_blockers: options.blockers || [], diff: { nodes_added: ["node-2"] } }
        : { id: "scope-job", status: "ready", revision_id: "revision-1" };
    } else if (endpoint.includes("/knowledge-map/revisions/") && endpoint.endsWith("/activate")) {
      activeRevision = 3;
      payload = { activation_required: false, current_revision_id: stagedRevisionId };
    } else if (endpoint.endsWith("/mastery-plans/preview") && init?.method === "POST") {
      payload = { preview_id: "preview-after-activation", revision: 2, days: [], tasks: [], activation_blockers: [], extension_draft: {} };
    } else if (endpoint.endsWith("/receipt") && init?.method === "PUT") {
      payload = { ok: true };
    }
    return Promise.resolve({ ok: true, json: async () => payload } as Response);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

function callsFor(calls: Array<[string, RequestInit | undefined]>, suffix: string, method?: string) {
  return calls.filter(([url, init]) => url.endsWith(suffix) && (!method || init?.method === method));
}

describe("knowledge-map scope anchor and staged activation", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("sends ordered anchor/support roles and rebuilds only after an anchor switch", async () => {
    const { calls } = installFetch();
    render(<ActiveTeachingChainView workspaceId="scope-workspace" />);
    await screen.findByTestId("active-material-anchor-material-support");
    fireEvent.click(screen.getByTestId("active-material-anchor-material-support"));
    expect(screen.getByTestId("active-map-scope")).toHaveTextContent("会重建知识地图");
    fireEvent.click(screen.getByTestId("active-save-receipt"));
    await waitFor(() => expect(callsFor(calls, "/receipt", "PUT").length).toBeGreaterThan(0));
    const receiptBody = JSON.parse(String(callsFor(calls, "/receipt", "PUT").at(-1)?.[1]?.body));
    expect(receiptBody).toMatchObject({
      material_ids: ["material-support", "material-anchor"],
      anchor_material_id: "material-support",
      supporting_material_ids: ["material-anchor"],
      scope_policy: expect.objectContaining({ mode: "single_anchor", additive: true }),
    });
    const jobBody = JSON.parse(String(callsFor(calls, "/knowledge-map/jobs", "POST").at(-1)?.[1]?.body));
    expect(jobBody).toMatchObject({
      material_ids: ["material-support", "material-anchor"],
      anchor_material_id: "material-support",
      supporting_material_ids: ["material-anchor"],
    });
  });

  it("keeps foundation-only changes out of the knowledge-map job", async () => {
    const { calls } = installFetch({ snapshot: snapshotFor({ material_ids: ["material-anchor", "material-support"] }) });
    render(<ActiveTeachingChainView workspaceId="scope-workspace" />);
    fireEvent.change(await screen.findByTestId("active-foundation"), { target: { value: "advanced" } });
    fireEvent.click(screen.getByTestId("active-save-receipt"));
    await waitFor(() => expect(callsFor(calls, "/receipt", "PUT").length).toBeGreaterThan(0));
    expect(callsFor(calls, "/knowledge-map/jobs", "POST")).toHaveLength(0);
  });

  it("uses the first ordered material as the legacy anchor when role fields are absent", async () => {
    installFetch({ snapshot: snapshotFor({ anchor_material_id: undefined, supporting_material_ids: undefined, scope_policy: undefined }) });
    render(<ActiveTeachingChainView workspaceId="scope-workspace" />);
    expect(await screen.findByTestId("active-map-scope")).toHaveTextContent("当前纲领：课程讲义.md");
    expect(screen.getByTestId("active-map-scope")).toHaveTextContent("辅助资料 1 份");
  });

  it("keeps current map and plan while staged, then activates with expected current and previews", async () => {
    const { calls } = installFetch({ staged: true });
    render(<ActiveTeachingChainView workspaceId="scope-workspace" />);
    fireEvent.click(await screen.findByTestId("active-material-anchor-material-support"));
    fireEvent.click(screen.getByTestId("active-save-receipt"));
    expect(await screen.findByTestId("active-staged-map")).toHaveTextContent("staged revision staged-revision");
    expect(screen.getByTestId("active-staged-map")).toHaveTextContent("nodes_added");
    expect(callsFor(calls, "/mastery-plans/preview", "POST")).toHaveLength(0);
    expect(screen.getByTestId("active-map-scope")).toHaveTextContent("当前 revision 1");
    fireEvent.click(screen.getByTestId("active-staged-activate"));
    await waitFor(() => expect(callsFor(calls, "/knowledge-map/revisions/staged-revision/activate", "POST").length).toBe(1));
    const activationBody = JSON.parse(String(callsFor(calls, "/knowledge-map/revisions/staged-revision/activate", "POST")[0][1]?.body));
    expect(activationBody.expected_current_revision_id).toBe("revision-1");
    await waitFor(() => expect(callsFor(calls, "/mastery-plans/preview", "POST").length).toBe(1));
  });

  it("renders staged activation blockers and disables activation", async () => {
    const { calls } = installFetch({ staged: true, blockers: [{ reason: "需要审核" }] });
    render(<ActiveTeachingChainView workspaceId="scope-workspace" />);
    fireEvent.click(await screen.findByTestId("active-material-anchor-material-support"));
    fireEvent.click(screen.getByTestId("active-save-receipt"));
    expect(await screen.findByTestId("active-staged-blockers")).toHaveTextContent("需要审核");
    expect(screen.getByTestId("active-staged-activate")).toBeDisabled();
    expect(callsFor(calls, "/mastery-plans/preview", "POST")).toHaveLength(0);
  });

  it("reviews a staged UUID candidate into a staged child and enables activation only after the server clears blockers", async () => {
    const candidate = {
      id: "scope-candidate",
      candidate_type: "scope_conflict",
      status: "pending",
      payload: { reason: "辅助资料中的新概念不纳入当前纲领计划" },
    };
    const { calls } = installFetch({
      staged: true,
      stagedCandidates: [candidate],
      blockers: [{ id: "scope-candidate", candidate_type: "scope_conflict", payload: candidate.payload }],
    });
    render(<ActiveTeachingChainView workspaceId="scope-workspace" />);
    fireEvent.click(await screen.findByTestId("active-material-anchor-material-support"));
    fireEvent.click(screen.getByTestId("active-save-receipt"));
    const staged = await screen.findByTestId("active-staged-map");
    expect(staged).toHaveTextContent("staged revision staged-revision");
    expect(staged).toHaveTextContent("确认保持纲领范围");
    expect(screen.getByTestId("active-staged-activate")).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "确认保持纲领范围" }));
    await waitFor(() => expect(callsFor(calls, "/knowledge-map/candidates/scope-candidate/decision", "POST").length).toBe(1));
    const decisionBody = JSON.parse(String(callsFor(calls, "/knowledge-map/candidates/scope-candidate/decision", "POST")[0][1]?.body));
    expect(decisionBody).toMatchObject({
      decision: "confirm",
      expected_revision_id: "staged-revision",
      expected_revision: 2,
    });
    await waitFor(() => expect(screen.getByTestId("active-staged-map")).toHaveTextContent("staged revision staged-child"));
    expect(calls.some(([url]) => url.includes("/knowledge-map?revision=staged-child"))).toBe(true);
    expect(screen.getByTestId("active-map-scope")).toHaveTextContent("当前 revision 1");
    expect(screen.getByTestId("active-staged-clear")).toHaveTextContent("服务器清除");
    expect(screen.getByTestId("active-staged-activate")).toBeEnabled();

    fireEvent.click(screen.getByTestId("active-staged-activate"));
    await waitFor(() => expect(callsFor(calls, "/knowledge-map/revisions/staged-child/activate", "POST").length).toBe(1));
    const activationBody = JSON.parse(String(callsFor(calls, "/knowledge-map/revisions/staged-child/activate", "POST")[0][1]?.body));
    expect(activationBody.expected_current_revision_id).toBe("revision-1");
  });

  it("preserves planner handoff anchor/support roles through material import", async () => {
    localStorage.clear();
    localStorage.setItem("mentorflow_planner_handoff_v1", JSON.stringify({
      goal: "掌握讲义主题",
      resources: [
        { candidate_id: "textbook-candidate", media_kind: "document", url: "https://example.test/textbook", title: "课程主讲义" },
        { candidate_id: "notes-candidate", media_kind: "document", url: "https://example.test/notes", title: "配套笔记" },
      ],
      anchor_resource: { candidate_id: "textbook-candidate", media_kind: "document", url: "https://example.test/textbook", title: "课程主讲义" },
    }));
    const { calls } = installFetch({ snapshot: snapshotFor(null) });
    render(<ActiveTeachingChainView workspaceId="scope-workspace" createRequest={1} compactPlannerFlow />);
    const handoffButton = await screen.findByRole("button", { name: "开始整理已选资料并生成计划" });
    expect(handoffButton).toBeEnabled();
    fireEvent.click(handoffButton);
    // The handoff is intentionally tested through the same URL import path as
    // the production flow; wait for the receipt to be emitted after imports.
    await waitFor(() => expect(callsFor(calls, "/receipt", "PUT").length).toBeGreaterThan(0));
    const body = JSON.parse(String(callsFor(calls, "/receipt", "PUT").at(-1)?.[1]?.body));
    expect(body.anchor_material_id).toBe("textbook-material");
    expect(body.supporting_material_ids).toContain("notes-material");
    expect(body.material_ids.slice(0, 1)).toEqual(["textbook-material"]);
    expect(body.material_ids).toEqual(expect.arrayContaining(["material-anchor", "material-support", "notes-material"]));
  });
});
