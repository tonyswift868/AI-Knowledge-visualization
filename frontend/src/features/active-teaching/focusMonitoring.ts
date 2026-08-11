export type FocusAttentionReason =
  | "face_absent"
  | "page_hidden"
  | "window_blurred"
  | "attention_restored";

export type FocusAttentionTransition = {
  event_type: "attention_lost" | "attention_restored";
  reason: FocusAttentionReason;
};

export type FocusAttentionSample = {
  at: number;
  facePresent: boolean;
  pageVisible: boolean;
  windowFocused: boolean;
};

export type FocusThresholds = {
  initialGraceMs: number;
  faceAbsentMs: number;
  pageHiddenMs: number;
  windowBlurredMs: number;
  restoredMs: number;
};

const DEFAULT_THRESHOLDS: FocusThresholds = {
  initialGraceMs: 8_000,
  faceAbsentMs: 12_000,
  pageHiddenMs: 3_000,
  windowBlurredMs: 5_000,
  restoredMs: 3_000,
};

export class FocusAttentionTracker {
  private readonly startedAt: number;
  private readonly thresholds: FocusThresholds;
  private state: "focused" | "distracted" = "focused";
  private conditionSince = new Map<FocusAttentionReason, number>();
  private restoredSince: number | null = null;

  constructor(startedAt: number, thresholds: Partial<FocusThresholds> = {}) {
    this.startedAt = startedAt;
    this.thresholds = { ...DEFAULT_THRESHOLDS, ...thresholds };
  }

  sample(value: FocusAttentionSample): FocusAttentionTransition | null {
    this.updateCondition("page_hidden", !value.pageVisible, value.at);
    this.updateCondition("window_blurred", !value.windowFocused, value.at);
    this.updateCondition("face_absent", !value.facePresent, value.at);

    const activeReason = this.activeReason(value.at);
    if (this.state === "focused") {
      this.restoredSince = null;
      if (
        value.at - this.startedAt >= this.thresholds.initialGraceMs
        && activeReason
      ) {
        this.state = "distracted";
        return { event_type: "attention_lost", reason: activeReason };
      }
      return null;
    }

    if (activeReason) {
      this.restoredSince = null;
      return null;
    }
    if (this.restoredSince === null) {
      this.restoredSince = value.at;
      return null;
    }
    if (value.at - this.restoredSince >= this.thresholds.restoredMs) {
      this.state = "focused";
      this.restoredSince = null;
      return {
        event_type: "attention_restored",
        reason: "attention_restored",
      };
    }
    return null;
  }

  private updateCondition(
    reason: FocusAttentionReason,
    active: boolean,
    at: number,
  ) {
    if (active) {
      if (!this.conditionSince.has(reason)) this.conditionSince.set(reason, at);
    } else {
      this.conditionSince.delete(reason);
    }
  }

  private activeReason(at: number): FocusAttentionReason | null {
    const candidates: Array<[FocusAttentionReason, number]> = [
      ["page_hidden", this.thresholds.pageHiddenMs],
      ["window_blurred", this.thresholds.windowBlurredMs],
      ["face_absent", this.thresholds.faceAbsentMs],
    ];
    for (const [reason, threshold] of candidates) {
      const since = this.conditionSince.get(reason);
      if (since !== undefined && at - since >= threshold) return reason;
    }
    return null;
  }
}

export type BrowserFaceDetector = {
  hasFace(video: HTMLVideoElement, at: number): boolean;
  close(): void;
};

export async function createBrowserFaceDetector(): Promise<BrowserFaceDetector> {
  const { FaceDetector, FilesetResolver } = await import("@mediapipe/tasks-vision");
  const vision = await FilesetResolver.forVisionTasks(
    "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.35/wasm",
  );
  const options = {
    baseOptions: {
      modelAssetPath:
        "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite",
      delegate: "GPU" as const,
    },
    runningMode: "VIDEO" as const,
    minDetectionConfidence: 0.5,
  };
  let detector;
  try {
    detector = await FaceDetector.createFromOptions(vision, options);
  } catch {
    detector = await FaceDetector.createFromOptions(vision, {
      ...options,
      baseOptions: {
        ...options.baseOptions,
        delegate: "CPU",
      },
    });
  }
  return {
    hasFace(video: HTMLVideoElement, at: number) {
      return detector.detectForVideo(video, at).detections.length > 0;
    },
    close() {
      detector.close();
    },
  };
}
