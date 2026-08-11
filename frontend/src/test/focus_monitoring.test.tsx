import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FocusMonitorPanel } from "../features/active-teaching/FocusMonitorPanel";
import {
  FocusAttentionTracker,
  type BrowserFaceDetector,
} from "../features/active-teaching/focusMonitoring";


afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("FocusAttentionTracker", () => {
  it("debounces noisy samples and emits only lost/restored transitions", () => {
    const tracker = new FocusAttentionTracker(0, {
      initialGraceMs: 0,
      faceAbsentMs: 1_000,
      pageHiddenMs: 500,
      windowBlurredMs: 800,
      restoredMs: 600,
    });
    expect(tracker.sample({
      at: 0,
      facePresent: false,
      pageVisible: true,
      windowFocused: true,
    })).toBeNull();
    expect(tracker.sample({
      at: 700,
      facePresent: true,
      pageVisible: true,
      windowFocused: true,
    })).toBeNull();
    expect(tracker.sample({
      at: 1_000,
      facePresent: false,
      pageVisible: true,
      windowFocused: true,
    })).toBeNull();
    expect(tracker.sample({
      at: 2_100,
      facePresent: false,
      pageVisible: true,
      windowFocused: true,
    })).toEqual({
      event_type: "attention_lost",
      reason: "face_absent",
    });
    expect(tracker.sample({
      at: 2_200,
      facePresent: false,
      pageVisible: false,
      windowFocused: false,
    })).toBeNull();
    expect(tracker.sample({
      at: 2_900,
      facePresent: true,
      pageVisible: true,
      windowFocused: true,
    })).toBeNull();
    expect(tracker.sample({
      at: 3_600,
      facePresent: true,
      pageVisible: true,
      windowFocused: true,
    })).toEqual({
      event_type: "attention_restored",
      reason: "attention_restored",
    });
  });

  it("prioritizes a hidden learning page over camera absence", () => {
    const tracker = new FocusAttentionTracker(0, {
      initialGraceMs: 0,
      faceAbsentMs: 500,
      pageHiddenMs: 500,
      windowBlurredMs: 500,
      restoredMs: 0,
    });
    tracker.sample({
      at: 0,
      facePresent: false,
      pageVisible: false,
      windowFocused: false,
    });
    expect(tracker.sample({
      at: 600,
      facePresent: false,
      pageVisible: false,
      windowFocused: false,
    })).toEqual({
      event_type: "attention_lost",
      reason: "page_hidden",
    });
  });
});

describe("FocusMonitorPanel", () => {
  it("does not touch the camera before explicit consent and stores no media", async () => {
    const stop = vi.fn();
    const getUserMedia = vi.fn().mockResolvedValue({
      getTracks: () => [{ stop }],
    });
    vi.stubGlobal("navigator", {
      ...navigator,
      mediaDevices: { getUserMedia },
    });
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ items: [] }))
      .mockResolvedValueOnce(jsonResponse({
        id: "focus_1",
        teaching_session_id: "session_1",
        status: "active",
        privacy: {
          processing: "browser_local",
          frames_stored: false,
          video_uploaded: false,
          audio_captured: false,
          event_cap: 120,
        },
      }))
      .mockResolvedValueOnce(jsonResponse({
        id: "focus_1",
        teaching_session_id: "session_1",
        status: "completed",
        started_at: "2026-07-31T08:00:00Z",
        ended_at: "2026-07-31T08:10:00Z",
        total_seconds: 600,
        focused_seconds: 570,
        distracted_seconds: 30,
        focus_rate: 95,
        distraction_count: 1,
        event_count: 2,
        summary: "本次整体很专注。",
        privacy: {
          processing: "browser_local",
          frames_stored: false,
          video_uploaded: false,
          audio_captured: false,
          event_cap: 120,
        },
      }));
    vi.stubGlobal("fetch", fetchMock);
    const detector: BrowserFaceDetector = {
      hasFace: vi.fn().mockReturnValue(true),
      close: vi.fn(),
    };

    render(
      <FocusMonitorPanel
        detectorFactory={async () => detector}
        teachingSessionId="session_1"
      />,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(getUserMedia).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("focus-monitor-open-consent"));
    expect(screen.getByTestId("focus-monitor-consent")).toHaveTextContent(
      "不录屏、不录音、不上传图片或视频",
    );
    expect(getUserMedia).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("focus-monitor-confirm"));
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(1));
    expect(getUserMedia).toHaveBeenCalledWith(expect.objectContaining({
      audio: false,
      video: expect.any(Object),
    }));
    await waitFor(() => expect(screen.getByTestId("focus-monitor-finish")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("focus-monitor-finish"));
    await waitFor(() => expect(screen.getByTestId("focus-monitor-latest")).toHaveTextContent(
      "专注 95% · 9 分 30 秒",
    ));
    expect(stop).toHaveBeenCalledTimes(1);
    expect(detector.close).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("image");
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("video/");
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("audio/");
  });

  it("shows a clear local-only failure when camera permission is denied", async () => {
    const denied = Object.assign(new Error("denied"), { name: "NotAllowedError" });
    const getUserMedia = vi.fn().mockRejectedValue(denied);
    vi.stubGlobal("navigator", {
      ...navigator,
      mediaDevices: { getUserMedia },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ items: [] })));

    render(<FocusMonitorPanel teachingSessionId="session_denied" />);
    fireEvent.click(screen.getByTestId("focus-monitor-open-consent"));
    fireEvent.click(screen.getByTestId("focus-monitor-confirm"));
    await waitFor(() => expect(screen.getByTestId("focus-monitor-error")).toHaveTextContent(
      "没有授予摄像头权限",
    ));
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
