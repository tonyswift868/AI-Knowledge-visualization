import { describe, expect, it } from "vitest"

import { isPlannerVideo } from "../features/casual-chat/CasualTeacherChat"
import { isActivePlannerVideo } from "../features/active-teaching/ActiveTeachingChainView"

describe("planning resource media contract", () => {
  it("uses canonical media_kind instead of provenance for video-only gating", () => {
    const article = {
      url: "https://blog.csdn.net/example/article/details/123456",
      title: "线性代数课程学习笔记",
      media_kind: "document",
      source_type: "video_collection",
      media_type: "video/mp4",
    }
    expect(isPlannerVideo(article)).toBe(false)
    expect(isActivePlannerVideo(article)).toBe(false)
  })

  it("retains video and collection semantics from canonical classes", () => {
    expect(isPlannerVideo({
      url: "https://www.bilibili.com/video/BV1ib411t7YR/",
      media_kind: "video",
      source_type: "video_collection",
    })).toBe(true)
    expect(isPlannerVideo({
      url: "https://www.youtube.com/playlist?list=PL123",
      media_kind: "video_collection",
    })).toBe(true)
  })
})
