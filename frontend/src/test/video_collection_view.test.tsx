import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  create: vi.fn(),
  detail: vi.fn(),
  manifest: vi.fn(),
  episode: vi.fn(),
  transcript: vi.fn(),
  summary: vi.fn(),
  job: vi.fn(),
  control: vi.fn(),
  summaries: vi.fn(),
  activate: vi.fn(),
}));

vi.mock("../features/active-teaching/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../features/active-teaching/api")>();
  return {
    ...original,
    createVideoCollection: api.create,
    readVideoCollection: api.detail,
    readVideoCollectionManifest: api.manifest,
    readVideoCollectionEpisode: api.episode,
    startVideoCollectionTranscript: api.transcript,
    startVideoCollectionSummary: api.summary,
    readVideoCollectionJob: api.job,
    controlVideoCollectionJob: api.control,
    readVideoCollectionSummaries: api.summaries,
    activateVideoCollectionMaterial: api.activate,
  };
});

import VideoCollectionView from "../features/active-teaching/VideoCollectionView";

const collection = {
  id: "vc-1",
  collection_id: "vc-1",
  title: "线性代数长课程",
  canonical_url: "https://space.bilibili.com/100/lists/200?type=season",
  provider: "bilibili",
  status: "ready",
  reported_count: 1000,
  enumerated_count: 1000,
  selected_count: 1000,
  manifest_revision: 1,
};

const coverage = {
  denominator: 1000,
  ready: 800,
  needs_review: 20,
  failed: 10,
  skipped: 0,
  processing: 0,
  unprocessed: 170,
  coverage_ratio: .8,
  complete: false,
};

const episodes = (offset = 0) => Array.from({ length: 50 }, (_, index) => ({
  id: `episode-${offset + index + 1}`,
  episode_id: `episode-${offset + index + 1}`,
  title: `课程第 ${offset + index + 1} 集`,
  ordinal: offset + index + 1,
  duration_seconds: 600,
  selected: true,
  status: index === 2 ? "failed" : "ready",
}));

const manifest = (page = 1) => ({
  collection_id: "vc-1",
  manifest_revision: 1,
  page_size: 50,
  page_index: page - 1,
  page,
  previous_cursor: page > 1 ? "cursor-prev" : null,
  next_cursor: page < 20 ? "cursor-next" : null,
  has_more: page < 20,
  total_count: 1000,
  items: episodes((page - 1) * 50),
});

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  api.detail.mockResolvedValue({ schema: "mentorflow.video-collection.v1", collection, coverage, jobs: [] });
  api.manifest.mockResolvedValue(manifest(1));
  api.summaries.mockResolvedValue({ items: [] });
});

afterEach(cleanup);

describe("VideoCollectionView", () => {
  it("prefills an explicitly selected Bilibili collection without starting retrieval", async () => {
    const sourceUrl = "https://space.bilibili.com/100/lists/200?type=season";
    render(<VideoCollectionView workspaceId="ws-1" providerSessionId={null} initialSourceUrl={sourceUrl} />);
    expect(await screen.findByTestId("video-collection-url")).toHaveValue(sourceUrl);
    expect(api.create).not.toHaveBeenCalled();
  });

  it("requires explicit metadata consent and never asks a Provider while enumerating", async () => {
    api.create.mockResolvedValue({
      schema: "mentorflow.video-collection.v1",
      collection: { ...collection, reported_count: null, enumerated_count: 0, selected_count: 0 },
      job: { id: "job-enum", job_id: "job-enum", collection_id: "vc-1", job_type: "enumerate", status: "queued", progress: {}, result: {}, failure: {} },
    });
    render(<StrictMode><VideoCollectionView workspaceId="ws-1" providerSessionId={null} /></StrictMode>);
    const createButton = screen.getByTestId("video-collection-create");
    expect(createButton).toBeDisabled();
    fireEvent.change(screen.getByTestId("video-collection-url"), { target: { value: "https://space.bilibili.com/100/lists/200?type=season" } });
    expect(createButton).toBeDisabled();
    fireEvent.click(screen.getByTestId("video-collection-retrieval-consent"));
    fireEvent.click(createButton);
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(1));
    expect(api.create.mock.calls[0][1]).toMatchObject({
      source_url: "https://space.bilibili.com/100/lists/200?type=season",
      user_confirmed_retrieval: true,
      max_episodes: 1000,
      page_size: 50,
    });
    expect(api.summary).not.toHaveBeenCalled();
  });

  it("restores progress and renders only one 50-episode server page", async () => {
    localStorage.setItem("mentorflow_video_collection_v1:ws-1", "vc-1");
    api.manifest.mockImplementation(async (_ws: string, _id: string, cursor?: string | null) => cursor === "cursor-next" ? manifest(2) : manifest(1));
    render(<StrictMode><VideoCollectionView workspaceId="ws-1" providerSessionId={null} /></StrictMode>);
    await waitFor(() => expect(screen.getByText("课程第 1 集")).toBeInTheDocument());
    expect(screen.getByTestId("video-collection-manifest").querySelectorAll("li")).toHaveLength(50);
    expect(screen.queryByText("课程第 51 集")).not.toBeInTheDocument();
    expect(screen.getByText("800 / 1000")).toBeInTheDocument();
    expect(screen.getByText(/部分覆盖，未伪装完整|需要单独确认/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(screen.getByText("课程第 51 集")).toBeInTheDocument());
    expect(api.manifest).toHaveBeenLastCalledWith("ws-1", "vc-1", "cursor-next", 50);
  });

  it("keeps summary unavailable without a verified real Provider", async () => {
    localStorage.setItem("mentorflow_video_collection_v1:ws-1", "vc-1");
    render(<VideoCollectionView workspaceId="ws-1" providerSessionId={null} />);
    await screen.findByText("课程第 1 集");
    fireEvent.click(screen.getByLabelText("我确认使用当前模型生成摘要"));
    expect(screen.getByTestId("video-collection-summarize")).toBeDisabled();
    expect(screen.getByText(/没有已验证的真实模型连接/)).toBeInTheDocument();
    expect(api.summary).not.toHaveBeenCalled();
  });

  it("adds an existing course summary only after an explicit anchor/support action", async () => {
    localStorage.setItem("mentorflow_video_collection_v1:ws-1", "vc-1");
    api.summaries.mockResolvedValue({ items: [{ id: "summary-1", scope_type: "collection", summary_text: "课程摘要" }] });
    api.activate.mockResolvedValue({ collection_id: "vc-1", role: "support", material: { id: "material-course" }, coverage });
    const onMaterialActivated = vi.fn();
    render(<VideoCollectionView workspaceId="ws-1" providerSessionId="ps-real" onMaterialActivated={onMaterialActivated} />);
    const support = await screen.findByTestId("video-collection-activate-support");
    expect(api.activate).not.toHaveBeenCalled();
    fireEvent.click(support);
    await waitFor(() => expect(api.activate).toHaveBeenCalledWith("ws-1", "vc-1", "support"));
    expect(onMaterialActivated).toHaveBeenCalledWith(expect.objectContaining({ material: { id: "material-course" } }), "support");
  });
});
