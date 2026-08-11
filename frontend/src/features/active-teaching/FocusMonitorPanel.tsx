import { useCallback, useEffect, useRef, useState } from "react";

import {
  finishFocusMonitoring,
  focusMonitoringHistory,
  recordFocusMonitoringEvent,
  startFocusMonitoring,
  type FocusMonitoringSession,
} from "./api";
import {
  createBrowserFaceDetector,
  FocusAttentionTracker,
  type BrowserFaceDetector,
  type FocusAttentionReason,
  type FocusAttentionTransition,
} from "./focusMonitoring";


type FocusMonitorPanelProps = {
  teachingSessionId: string;
  detectorFactory?: () => Promise<BrowserFaceDetector>;
  thresholds?: ConstructorParameters<typeof FocusAttentionTracker>[1];
};

const reasonLabels: Record<FocusAttentionReason, string> = {
  face_absent: "画面中暂时没有检测到人",
  page_hidden: "学习页面切到了后台",
  window_blurred: "学习窗口暂时失去焦点",
  attention_restored: "已回到学习状态",
};

function newClientEventId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `focus-${crypto.randomUUID()}`;
  }
  return `focus-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function durationLabel(seconds: number) {
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`;
}

export function FocusMonitorPanel({
  teachingSessionId,
  detectorFactory = createBrowserFaceDetector,
  thresholds,
}: FocusMonitorPanelProps) {
  const [mode, setMode] = useState<
    "idle" | "consent" | "starting" | "active" | "stopping" | "error"
  >("idle");
  const [status, setStatus] = useState("尚未开启");
  const [error, setError] = useState("");
  const [history, setHistory] = useState<FocusMonitoringSession[]>([]);
  const [previewVisible, setPreviewVisible] = useState(true);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const detectorRef = useRef<BrowserFaceDetector | null>(null);
  const focusSessionRef = useRef<FocusMonitoringSession | null>(null);
  const trackerRef = useRef<FocusAttentionTracker | null>(null);
  const intervalRef = useRef<number | null>(null);
  const eventQueueRef = useRef(Promise.resolve());
  const endingRef = useRef(false);

  const loadHistory = useCallback(async () => {
    try {
      setHistory(await focusMonitoringHistory(teachingSessionId, 5));
    } catch {
      // Focus history is supplementary and must never block the lesson.
    }
  }, [teachingSessionId]);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  const stopLocalCapture = useCallback(() => {
    if (intervalRef.current !== null) {
      window.clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    detectorRef.current?.close();
    detectorRef.current = null;
    for (const track of streamRef.current?.getTracks() || []) track.stop();
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
  }, []);

  const queueTransition = useCallback((transition: FocusAttentionTransition) => {
    const focusId = focusSessionRef.current?.id;
    if (!focusId) return;
    setStatus(reasonLabels[transition.reason]);
    eventQueueRef.current = eventQueueRef.current
      .catch(() => undefined)
      .then(async () => {
        await recordFocusMonitoringEvent(focusId, {
          client_event_id: newClientEventId(),
          event_type: transition.event_type,
          reason: transition.reason,
        });
      })
      .catch(() => {
        setStatus("关键状态暂未同步，结束时仍会生成本地时长记录");
      });
  }, []);

  const beginSampling = useCallback(() => {
    trackerRef.current = new FocusAttentionTracker(performance.now(), thresholds);
    intervalRef.current = window.setInterval(() => {
      const video = videoRef.current;
      const detector = detectorRef.current;
      const tracker = trackerRef.current;
      if (!video || !detector || !tracker || video.readyState < 2) return;
      let facePresent = false;
      try {
        facePresent = detector.hasFace(video, performance.now());
      } catch {
        return;
      }
      const transition = tracker.sample({
        at: performance.now(),
        facePresent,
        pageVisible: document.visibilityState === "visible",
        windowFocused: document.hasFocus(),
      });
      if (transition) queueTransition(transition);
    }, 750);
  }, [queueTransition, thresholds]);

  const start = useCallback(async () => {
    setMode("starting");
    setError("");
    endingRef.current = false;
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("当前浏览器不支持摄像头访问，请使用最新版 Chrome 或 Edge。");
      }
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: 320 },
          height: { ideal: 240 },
          frameRate: { ideal: 5, max: 10 },
          facingMode: "user",
        },
        audio: false,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      detectorRef.current = await detectorFactory();
      focusSessionRef.current = await startFocusMonitoring(teachingSessionId);
      setMode("active");
      setStatus("正在本机检测 · 先给你 8 秒准备");
      beginSampling();
    } catch (caught: any) {
      stopLocalCapture();
      focusSessionRef.current = null;
      setError(
        caught?.name === "NotAllowedError"
          ? "你没有授予摄像头权限；专注模式没有启动，也没有保存任何记录。"
          : caught?.message || "专注模式启动失败，请检查摄像头是否被其他应用占用。",
      );
      setMode("error");
    }
  }, [beginSampling, detectorFactory, stopLocalCapture, teachingSessionId]);

  const finish = useCallback(async (
    reason: "user_finished" | "component_unmounted" = "user_finished",
  ) => {
    if (endingRef.current) return;
    endingRef.current = true;
    const focusId = focusSessionRef.current?.id;
    stopLocalCapture();
    if (!focusId) {
      setMode("idle");
      endingRef.current = false;
      return;
    }
    setMode("stopping");
    try {
      await eventQueueRef.current.catch(() => undefined);
      const summary = await finishFocusMonitoring(focusId, reason);
      focusSessionRef.current = null;
      setHistory((current) => [
        summary,
        ...current.filter((item) => item.id !== summary.id),
      ].slice(0, 5));
      setStatus("本次专注记录已保存");
      setMode("idle");
    } catch (caught: any) {
      setError(caught?.message || "结束记录暂时没有同步，请稍后重试。");
      setMode("error");
    } finally {
      endingRef.current = false;
    }
  }, [stopLocalCapture]);

  useEffect(() => () => {
    stopLocalCapture();
    const focusId = focusSessionRef.current?.id;
    if (!focusId || endingRef.current) return;
    endingRef.current = true;
    void fetch(
      `/api/active-teaching/focus-monitoring/${encodeURIComponent(focusId)}/finish`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "component_unmounted" }),
        keepalive: true,
      },
    );
  }, [stopLocalCapture]);

  return (
    <section className="focus-monitor" data-testid="focus-monitor-panel">
      <div className="focus-monitor__head">
        <div>
          <span>本机专注陪伴</span>
          <b>摄像头专注模式</b>
        </div>
        {mode === "active" ? (
          <button
            data-testid="focus-monitor-finish"
            onClick={() => void finish()}
            type="button"
          >
            结束并保存
          </button>
        ) : (
          <button
            data-testid="focus-monitor-open-consent"
            disabled={mode === "starting" || mode === "stopping"}
            onClick={() => {
              setMode("consent");
              setError("");
            }}
            type="button"
          >
            开启专注模式
          </button>
        )}
      </div>

      {mode === "consent" && (
        <div className="focus-monitor__consent" data-testid="focus-monitor-consent">
          <b>开启前请确认</b>
          <p>
            画面只在当前浏览器中用于检测“人在画面内、页面在前台”。
            <strong> 不录屏、不录音、不上传图片或视频。</strong>
          </p>
          <p>后端只保存离开/恢复等关键节点和本次结束总结，不保存连续操作日志。</p>
          <div>
            <button
              className="is-secondary"
              onClick={() => setMode("idle")}
              type="button"
            >
              取消
            </button>
            <button
              data-testid="focus-monitor-confirm"
              onClick={() => void start()}
              type="button"
            >
              同意并开启摄像头
            </button>
          </div>
        </div>
      )}

      {(mode === "starting" || mode === "active" || mode === "stopping") && (
        <div className="focus-monitor__live">
          <video
            aria-label="专注模式本机摄像头预览"
            className={previewVisible ? "" : "is-hidden"}
            muted
            playsInline
            ref={videoRef}
          />
          <div>
            <span className={`focus-monitor__dot ${mode === "active" ? "is-active" : ""}`} />
            <b>{mode === "starting" ? "正在加载本地检测器…" : status}</b>
            <small>检测本身不消耗 AI API；只有状态转折会同步。</small>
            {mode === "active" && (
              <button
                className="is-link"
                onClick={() => setPreviewVisible((value) => !value)}
                type="button"
              >
                {previewVisible ? "隐藏预览" : "显示预览"}
              </button>
            )}
          </div>
        </div>
      )}

      {error && (
        <p className="focus-monitor__error" data-testid="focus-monitor-error">
          {error}
        </p>
      )}

      {history[0]?.status === "completed" && (
        <div className="focus-monitor__latest" data-testid="focus-monitor-latest">
          <span>上次专注</span>
          <b>
            专注 {history[0].focus_rate}% · {durationLabel(history[0].focused_seconds)}
          </b>
          <small>
            离开 {history[0].distraction_count} 次 · {history[0].summary}
          </small>
        </div>
      )}

      {history.length > 1 && (
        <details className="focus-monitor__history">
          <summary>更早记录（{history.length - 1}）</summary>
          {history.slice(1).map((item) => (
            <div key={item.id}>
              <b>{new Date(item.started_at).toLocaleString()}</b>
              <span>
                专注 {item.focus_rate}% · {durationLabel(item.focused_seconds)}
                {" · "}离开 {item.distraction_count} 次
              </span>
              <small>{item.summary}</small>
            </div>
          ))}
        </details>
      )}
    </section>
  );
}
