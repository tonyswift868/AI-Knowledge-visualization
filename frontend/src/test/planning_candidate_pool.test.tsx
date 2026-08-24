import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import PlanningCandidatePool, { candidatePageFillTarget, candidateSafety, readCandidatePage } from "../features/planning/PlanningCandidatePool"
import { buildPlanningSearchAnchor, planningDiscoveryMode } from "../features/planning/planningCandidatePoolClient"

const candidates = (count: number) => Array.from({ length: count }, (_, index) => ({
  candidate_id: `candidate-${index + 1}`,
  url: `https://example.test/${index + 1}`,
  title: `资料 ${index + 1}`,
}))

afterEach(cleanup)

describe("PlanningCandidatePool", () => {
  it("targets the next complete page from published, qualified, and held counts", () => {
    expect(candidatePageFillTarget({ pageSize: 3, total: 3, qualifiedCount: 5, heldIncompleteCount: 2 })).toBe(6)
    expect(candidatePageFillTarget({ pageSize: 3, total: 2, shortfallCount: 1 })).toBe(3)
    expect(candidatePageFillTarget({ pageSize: 3, total: 3 }, 3)).toBe(3)
  })

  it("chooses append or replacement from pool state rather than candidate titles", () => {
    expect(planningDiscoveryMode("继续找一些资料", false, false)).toBe("append")
    expect(planningDiscoveryMode("继续找一些资料", false, true)).toBe("append")
    expect(planningDiscoveryMode("继续找一些资料", true, true)).toBe("append")
    expect(planningDiscoveryMode("继续找三份资料，不要替换已选视频", true, true)).toBe("append")
    expect(planningDiscoveryMode("保留已选资料，再补充一些笔记", true, true)).toBe("append")
    expect(planningDiscoveryMode("这批不满意，请换一批", true, true)).toBe("replace_unselected")
    expect(planningDiscoveryMode("这批不满意，请换一批", false, true)).toBe("replace_unselected")
    expect(planningDiscoveryMode("find another batch", false, true)).toBe("replace_unselected")
    expect(planningDiscoveryMode("请你再找几个和视频配套的笔记资料", true, true)).toBe("replace_unselected")
    expect(planningDiscoveryMode("这批不满意，请换一批", true, false)).toBe("append")
  })

  it("shows three cards per page and keeps a selected item pinned while browsing", () => {
    const items = candidates(6)
    const onSelectionChange = vi.fn()
    render(<PlanningCandidatePool candidates={items} selectedKeys={["stable:candidate-1"]} anchorKey="stable:candidate-1" onSelectionChange={onSelectionChange} testId="pool" />)
    expect(screen.getAllByRole("checkbox")).toHaveLength(3)
    expect(screen.getByTestId("pool-pinned")).toHaveTextContent("资料 1")
    fireEvent.click(screen.getByTestId("pool-next"))
    expect(screen.queryByText("资料 1", { selector: "strong" })).not.toBeInTheDocument()
    expect(screen.getByText("资料 4")).toBeInTheDocument()
    expect(screen.getByTestId("pool-pinned")).toHaveTextContent("资料 1")
    fireEvent.click(screen.getByTestId("pool-previous"))
    expect(screen.getByTestId("pool-page-status")).toHaveTextContent("第 1 / 2 页")
  })

  it("supports anchor/support roles and reject undo without losing overflow", () => {
    const items = candidates(4)
    const onSelectionChange = vi.fn()
    const onAnchorChange = vi.fn()
    render(<PlanningCandidatePool candidates={items} selectedKeys={["stable:candidate-1", "stable:candidate-2"]} anchorKey="stable:candidate-1" onSelectionChange={onSelectionChange} onAnchorChange={onAnchorChange} testId="pool" />)
    fireEvent.click(screen.getByRole("button", { name: "设为纲领" }))
    expect(onAnchorChange).toHaveBeenCalledWith("stable:candidate-2")
    fireEvent.click(screen.getByRole("button", { name: "暂不使用 资料 3" }))
    expect(screen.getByTestId("pool-rejected")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /撤销/ }))
    expect(screen.queryByTestId("pool-rejected")).not.toBeInTheDocument()
  })

  it("keeps a role click visible before the parent projection catches up", () => {
    const onAnchorChange = vi.fn()
    const { rerender } = render(<PlanningCandidatePool
      candidates={candidates(2)}
      selectedKeys={["stable:candidate-1", "stable:candidate-2"]}
      anchorKey="stable:candidate-1"
      onSelectionChange={vi.fn()}
      onAnchorChange={onAnchorChange}
      testId="optimistic-role"
    />)
    fireEvent.click(screen.getByRole("button", { name: "设为纲领" }))
    expect(onAnchorChange).toHaveBeenCalledTimes(1)
    // Simulate a delayed parent state update that still renders the old
    // anchor. The click remains effective and does not require a second hit.
    rerender(<PlanningCandidatePool
      candidates={candidates(2)}
      selectedKeys={["stable:candidate-1", "stable:candidate-2"]}
      anchorKey="stable:candidate-1"
      onSelectionChange={vi.fn()}
      onAnchorChange={onAnchorChange}
      testId="optimistic-role"
    />)
    expect(screen.getByRole("button", { name: "纲领（默认）" })).toBeInTheDocument()
  })

  it("hides provider-flagged unsafe and off-topic candidates from selection", () => {
    const items = [
      { ...candidates(1)[0], candidate_id: "safe", title: "线性代数讲义" },
      { ...candidates(1)[0], candidate_id: "unsafe", title: "不安全候选", safety_flags: ["adult_content"] },
      { ...candidates(1)[0], candidate_id: "off-topic", title: "无关候选", off_topic: true },
    ]
    render(<PlanningCandidatePool candidates={items} selectedKeys={[]} onSelectionChange={vi.fn()} testId="safety-pool" />)
    expect(screen.getByRole("checkbox", { name: "选择 线性代数讲义" })).toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "选择 不安全候选" })).not.toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "选择 无关候选" })).not.toBeInTheDocument()
    expect(screen.getByTestId("safety-pool-safety-note")).toHaveTextContent("已排除 2 份")
  })

  it("uses structured safety codes without treating positive reason text as unsafe", () => {
    const items = [
      {
        ...candidates(1)[0],
        candidate_id: "safe-structured",
        title: "主题 A 课程",
        safety_status: "safe",
        safety_reason: "no_strong_unsafe_signal",
        qualification_status: "qualified",
        query_fit: true,
      },
      {
        ...candidates(1)[0],
        candidate_id: "safe-prose",
        title: "主题 B 课程",
        safety_status: "safe",
        safety_reason: "safe because this topic is unrelated to unsafe material",
        qualification_status: "qualified",
        query_fit: true,
      },
      {
        ...candidates(1)[0],
        candidate_id: "unsafe-status",
        title: "主题 C 课程",
        safety_status: "rejected",
        safety_reason: "unsafe:provider_policy",
      },
      {
        ...candidates(1)[0],
        candidate_id: "blocked-qualification",
        title: "主题 D 课程",
        qualification_status: "blocked",
        block_reason: "current_topic_relevance_gate",
      },
      {
        ...candidates(1)[0],
        candidate_id: "blocked-relevance-projection",
        title: "主题 D2 课程",
        blocked: true,
        safety_status: "safe",
        block_reason: "current_topic_relevance_gate",
      },
      {
        ...candidates(1)[0],
        candidate_id: "off-topic",
        title: "主题 E 课程",
        off_topic: true,
      },
      {
        ...candidates(1)[0],
        candidate_id: "unsafe-reason",
        title: "主题 F 课程",
        safety_status: "safe",
        safety_reason: "adult_content",
      },
    ]
    render(<PlanningCandidatePool candidates={items} selectedKeys={[]} pageSize={10} onSelectionChange={vi.fn()} testId="structured-safety" />)
    expect(screen.getByRole("checkbox", { name: "选择 主题 A 课程" })).toBeInTheDocument()
    expect(screen.getByRole("checkbox", { name: "选择 主题 B 课程" })).toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "选择 主题 C 课程" })).not.toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "选择 主题 D 课程" })).not.toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "选择 主题 E 课程" })).not.toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "选择 主题 F 课程" })).not.toBeInTheDocument()
    expect(screen.getByTestId("structured-safety-safety-note")).toHaveTextContent("已排除 5 份")

    expect(candidateSafety({ direct_user_reference: true, off_topic: true, qualification_status: "blocked", block_reason: "current_topic_relevance_gate" })).toEqual({ blocked: false })
    expect(candidateSafety({ blocked: true, safety_status: "safe", block_reason: "current_topic_relevance_gate" })).toEqual({ blocked: true, reason: "off_topic" })
    expect(candidateSafety({ direct_user_reference: true, safety_status: "rejected", safety_reason: "unsafe:provider_policy" })).toEqual({ blocked: true, reason: "unsafe" })
  })

  it("emits deselect before reject for a selected candidate", () => {
    const actions: string[] = []
    render(
      <PlanningCandidatePool
        candidates={candidates(2)}
        selectedKeys={["stable:candidate-1"]}
        anchorKey="stable:candidate-1"
        onSelectionChange={vi.fn()}
        onPoolAction={(action) => actions.push(action)}
        testId="reject-order"
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "暂不使用 资料 1" }))
    expect(actions).toEqual(["deselect", "reject"])
  })

  it("reads additive candidate pages and legacy arrays without truncating", () => {
    const parsed = readCandidatePage({ resources: candidates(6) }, [], 3)
    expect(parsed.candidates).toHaveLength(6)
    expect(parsed.page.pageSize).toBe(3)
    expect(parsed.page.hasMore).toBe(true)
    const additive = readCandidatePage({ candidate_page: { items: candidates(2), page: 1, page_size: 3, has_more: false, total: 5 } })
    expect(additive.page.page).toBe(1)
    expect(additive.page.total).toBe(5)
  })

  it("does not slice an already paged server response a second time", () => {
    const items = candidates(5)
    const parsed = readCandidatePage({
      planning_session_id: "pool-page-test",
      revision: 4,
      candidate_page: {
        items: items.slice(3),
        page: 1,
        page_size: 3,
        total_count: 5,
        has_more: false,
        previous_cursor: "opaque-prev",
      },
    })
    expect(parsed.page.serverPaged).toBe(true)
    expect(parsed.candidates).toHaveLength(2)
    render(<PlanningCandidatePool candidates={parsed.candidates} selectedKeys={[]} candidatePage={parsed.page} onSelectionChange={vi.fn()} testId="server-page" />)
    expect(screen.getByText("资料 4")).toBeInTheDocument()
    expect(screen.getByText("资料 5")).toBeInTheDocument()
    expect(screen.queryByText("资料 1")).not.toBeInTheDocument()
  })

  it("keeps provided direct references pinned without duplicating them into later server pages", () => {
    const direct = {
      candidate_id: "direct-reference",
      url: "https://example.test/direct-reference",
      title: "用户直接链接",
      direct_user_reference: true,
    }
    const fresh = Array.from({ length: 5 }, (_, index) => ({
      candidate_id: `fresh-${index + 1}`,
      url: `https://example.test/fresh-${index + 1}`,
      title: `新资料 ${index + 1}`,
    }))
    const pageOne = readCandidatePage({
      candidate_page: {
        provided_items: [direct],
        items: [direct, ...fresh.slice(0, 2)],
        page_index: 0,
        page_size: 3,
        total_count: 6,
        has_more: true,
        server_paged: true,
      },
    })
    expect(pageOne.page.currentItemIdentities).toEqual([
      "stable:direct-reference",
      "stable:fresh-1",
      "stable:fresh-2",
    ])

    const { rerender } = render(
      <PlanningCandidatePool
        candidates={pageOne.candidates}
        selectedKeys={["stable:direct-reference"]}
        candidatePage={pageOne.page}
        onSelectionChange={vi.fn()}
        testId="direct-page-regression"
      />,
    )
    expect(screen.getAllByRole("listitem")).toHaveLength(3)
    expect(screen.getByTestId("direct-page-regression-card-stable-direct-reference")).toBeInTheDocument()
    expect(screen.getByTestId("direct-page-regression-card-stable-fresh-1")).toBeInTheDocument()
    expect(screen.getByTestId("direct-page-regression-card-stable-fresh-2")).toBeInTheDocument()

    const pageTwo = readCandidatePage({
      candidate_page: {
        provided_items: [direct],
        items: fresh.slice(2),
        page_index: 1,
        page_size: 3,
        total_count: 6,
        has_more: false,
        server_paged: true,
      },
    })
    expect(pageTwo.page.currentItemIdentities).toEqual([
      "stable:fresh-3",
      "stable:fresh-4",
      "stable:fresh-5",
    ])
    rerender(
      <PlanningCandidatePool
        candidates={pageTwo.candidates}
        selectedKeys={["stable:direct-reference"]}
        candidatePage={pageTwo.page}
        onSelectionChange={vi.fn()}
        testId="direct-page-regression"
      />,
    )
    expect(screen.getAllByRole("listitem")).toHaveLength(3)
    expect(screen.queryByTestId("direct-page-regression-card-stable-direct-reference")).not.toBeInTheDocument()
    expect(screen.getByTestId("direct-page-regression-card-stable-fresh-3")).toBeInTheDocument()
    expect(screen.getByTestId("direct-page-regression-card-stable-fresh-4")).toBeInTheDocument()
    expect(screen.getByTestId("direct-page-regression-card-stable-fresh-5")).toBeInTheDocument()
    expect(screen.getByTestId("direct-page-regression-pinned-stable-direct-reference")).toBeInTheDocument()
    expect(screen.getAllByRole("checkbox")).toHaveLength(3)
  })

  it("normalizes server page aliases to zero-based while preserving the server-paged mode", () => {
    const parsed = readCandidatePage({
      planning_session_id: "pool-contract-test",
      candidate_page: {
        items: candidates(3),
        page: 1,
        page_number: 1,
        page_size: 3,
        total_count: 6,
        has_more: true,
        mode: "server_paged",
        server_paged: true,
      },
    })
    expect(parsed.page.page).toBe(0)
    expect(parsed.page.pageIndex).toBe(0)
    expect(parsed.page.mode).toBe("server_paged")
    expect(parsed.page.serverPaged).toBe(true)

    const canonical = readCandidatePage({
      candidate_page: {
        items: candidates(1),
        page: 4,
        page_index: 2,
        page_size: 3,
        mode: "server_paged",
        server_paged: true,
      },
    })
    expect(canonical.page.page).toBe(2)
    expect(canonical.page.pageIndex).toBe(2)
  })

  it("exposes previous opaque cursor to the page callback", () => {
    const onRequestPage = vi.fn()
    const parsed = readCandidatePage({ candidate_page: { items: candidates(2), page: 1, page_size: 3, total: 5, has_more: false, previous_cursor: "opaque-prev" } })
    render(<PlanningCandidatePool candidates={parsed.candidates} selectedKeys={[]} candidatePage={parsed.page} onSelectionChange={vi.fn()} onRequestPage={onRequestPage} testId="cursor-page" />)
    fireEvent.click(screen.getByTestId("cursor-page-previous"))
    expect(onRequestPage).toHaveBeenCalledWith(0, "previous", "opaque-prev")
  })

  it("counts a provided link inside the same three-card page as two new notes", () => {
    const provided = {
      ...candidates(1)[0],
      candidate_id: "provided-bv",
      title: "用户提供的 B 站链接",
      direct_user_reference: true,
    }
    const parsed = readCandidatePage({
      candidate_page: {
        provided_items: [provided],
        items: [provided, ...candidates(2)],
        page_index: 0,
        page_size: 3,
        total_count: 3,
        qualified_candidate_count: 3,
        held_incomplete_count: 0,
        direct_reference_count: 1,
        new_candidate_count: 2,
        server_paged: true,
        has_more: false,
      },
    })
    expect(parsed.page.total).toBe(3)
    expect(parsed.page.qualifiedCount).toBe(3)
    expect(parsed.page.heldIncompleteCount).toBe(0)
    render(
      <PlanningCandidatePool
        candidates={parsed.candidates}
        selectedKeys={[]}
        candidatePage={parsed.page}
        onSelectionChange={vi.fn()}
        testId="strict-pool"
      />,
    )
    expect(screen.queryByTestId("strict-pool-provided")).not.toBeInTheDocument()
    expect(screen.getByTestId("strict-pool-page-status")).toHaveTextContent("共 3 份候选")
    expect(screen.getByTestId("strict-pool-page-status")).toHaveTextContent("1 份由你提供")
    expect(screen.getByTestId("strict-pool-page-status")).toHaveTextContent("2 份新资料")
    expect(screen.getByText("精确引用 · 由你提供，尚未联网核验")).toBeInTheDocument()
    expect(screen.getAllByRole("checkbox")).toHaveLength(3)
  })

  it("builds durable search anchors from exact references, uploads, confidence and objective", () => {
    expect(buildPlanningSearchAnchor({
      objective: "掌握线性代数",
      selectedResources: [{
        candidate_id: "exact-1",
        url: "https://example.test/exact",
        title: "用户原始资料",
        direct_user_reference: true,
      }],
    })).toEqual(expect.objectContaining({ kind: "exact_reference", candidate_id: "exact-1", url: "https://example.test/exact" }))

    const uploaded = buildPlanningSearchAnchor({
      objective: "整理我的笔记",
      uploadedMaterial: { id: "upload-1", filename: "notes.md", content_label: "课程笔记" },
      selectedResources: [{ candidate_id: "candidate-1", confidence: 0.99, title: "公开候选", url: "https://example.test/candidate" }],
    })
    expect(uploaded).toEqual(expect.objectContaining({ kind: "uploaded_material", id: "upload-1", filename: "notes.md", content_label: "课程笔记" }))
    expect(uploaded).not.toHaveProperty("path")

    expect(buildPlanningSearchAnchor({
      objective: "理解排序",
      selectedResources: [
        { candidate_id: "low", confidence: 0.2, title: "低置信度", url: "https://example.test/low" },
        { candidate_id: "high", confidence: 0.9, title: "高置信度", url: "https://example.test/high" },
      ],
    })).toEqual(expect.objectContaining({ kind: "selected_candidate", candidate_id: "high" }))
    expect(buildPlanningSearchAnchor({
      objective: "理解线性代数",
      selectedResources: [{ candidate_id: "bv-1", url: "https://example.test/BV1", title: "B站视频 BV1", direct_user_reference: true }],
    })).toEqual(expect.objectContaining({ kind: "exact_reference", topic: "理解线性代数", search_title: "理解线性代数" }))
  })

  it("states when the published page is short even if an exact reference remains visible", () => {
    const parsed = readCandidatePage({ candidate_page: {
      items: [{ ...candidates(1)[0], direct_user_reference: true }],
      page_index: 0,
      page_size: 3,
      total_count: 1,
      qualified_candidate_count: 3,
      held_incomplete_count: 2,
      page_complete: false,
      server_paged: true,
    } })
    render(<PlanningCandidatePool candidates={parsed.candidates} selectedKeys={[]} candidatePage={parsed.page} onSelectionChange={vi.fn()} testId="short-page" />)
    expect(screen.getByTestId("short-page-page-status")).toHaveTextContent("当前页未补齐")
    expect(screen.getByTestId("short-page-capacity")).toHaveTextContent("仍有 2 份")
  })

  it("distinguishes missing page slots from qualified candidates held for a complete page", () => {
    const parsed = readCandidatePage({ candidate_page: {
      items: [{ ...candidates(1)[0], direct_user_reference: true }],
      page_index: 0,
      page_size: 3,
      total_count: 1,
      qualified_candidate_count: 1,
      shortfall_count: 2,
      held_incomplete_count: 0,
      page_complete: false,
      server_paged: true,
    } })
    expect(parsed.page.shortfallCount).toBe(2)
    expect(parsed.page.heldIncompleteCount).toBe(0)
    render(<PlanningCandidatePool candidates={parsed.candidates} selectedKeys={[]} candidatePage={parsed.page} onSelectionChange={vi.fn()} testId="shortfall-only" />)
    expect(screen.getByTestId("shortfall-only-page-status")).toHaveTextContent("还缺 2 份")
    expect(screen.getByTestId("shortfall-only-capacity")).toHaveTextContent("还缺 2 份（2 个名额）")
    expect(screen.getByTestId("shortfall-only-capacity")).not.toHaveTextContent("仍有 2 份合格候选")
  })

  it("offers an explicit one-click refill after policy filtering", () => {
    const onRequestReplenish = vi.fn()
    const direct = { ...candidates(1)[0], direct_user_reference: true }
    const blocked = {
      ...candidates(1)[0],
      candidate_id: "blocked-mirror",
      title: "Mirror index",
      blocked: true,
      safety_status: "safe",
      block_reason: "independent_companion_quality_gate",
    }
    render(<PlanningCandidatePool
      candidates={[direct, blocked]}
      selectedKeys={[]}
      candidatePage={{ page: 0, pageSize: 3, total: 1, shortfallCount: 2, pageComplete: false }}
      onSelectionChange={vi.fn()}
      onRequestReplenish={onRequestReplenish}
      testId="refill-pool"
    />)

    expect(screen.getByTestId("refill-pool-safety-note")).toHaveTextContent("不会占用推荐名额")
    fireEvent.click(screen.getByTestId("refill-pool-replenish"))
    expect(onRequestReplenish).toHaveBeenCalledTimes(1)
  })

  it("blocks add-page until the selected material count reaches one full page", () => {
    const onRequestAddPage = vi.fn()
    render(<PlanningCandidatePool
      candidates={candidates(3)}
      selectedKeys={["stable:candidate-1", "stable:candidate-2"]}
      selectedMaterialCount={2}
      candidatePage={{ page: 0, pageSize: 3, total: 3, hasMore: false }}
      onSelectionChange={vi.fn()}
      onRequestAddPage={onRequestAddPage}
      testId="add-page-blocked"
    />)

    fireEvent.click(screen.getByTestId("add-page-blocked-add-page"))
    expect(screen.getByTestId("add-page-blocked-action-notice")).toHaveTextContent("请先至少选择 3 份材料，再尝试扩充候选")
    expect(onRequestAddPage).not.toHaveBeenCalled()
  })
})
