import { useEffect, useRef, useState } from "react";
import { api, audioUrl, coverUrl, Health, TaskDetail } from "../api";
import {
  formatClock,
  formatDate,
  formatDateTime,
  formatDuration,
  STAGE_DESC,
  STAGE_LABEL,
  STAGE_ORDER,
  TASK_STATUS_LABEL,
} from "../utils";
import AnalysisPanel from "./AnalysisPanel";
import ChatPanel from "./ChatPanel";
import TranscriptView from "./TranscriptView";
import {
  ConfirmDialog,
  EmptyState,
  Icon,
  Segmented,
  Spinner,
  StatusBadge,
  useToast,
} from "./ui";

const STAGE_END: Record<string, number> = {
  download: 20,
  extract: 27,
  separate: 49,
  denoise: 59,
  normalize: 63,
  diarize: 76,
  transcribe: 95,
  finalize: 100,
};

type Tab = "transcript" | "analysis" | "chat" | "logs";
type ConfirmKind = "retry" | "deleteTask" | "deleteAudio" | "terminate" | null;

export default function TaskDetailView({
  task,
  health,
  onRefresh,
  onDeleted,
  onOpenSettings,
}: {
  task: TaskDetail;
  health: Health | null;
  onRefresh: () => void;
  onDeleted: () => void;
  onOpenSettings: () => void;
}) {
  const toast = useToast();
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const [variant, setVariant] = useState<"enhanced" | "source">(
    task.enhanced_available ? "enhanced" : "source",
  );
  const [tab, setTab] = useState<Tab>(task.transcript ? "transcript" : "logs");
  const [confirm, setConfirm] = useState<ConfirmKind>(null);
  const [showAllLogs, setShowAllLogs] = useState(false);
  const [busy, setBusy] = useState(false);
  const pendingSeek = useRef<number | null>(null);
  const lastTranscriptId = useRef<string | null>(task.transcript?.id || null);

  useEffect(() => {
    if (task.enhanced_available) {
      setVariant("enhanced");
    } else {
      setVariant("source");
    }
  }, [task.enhanced_available, task.id]);

  useEffect(() => {
    if (task.transcript && !lastTranscriptId.current) {
      setTab("transcript");
    }
    lastTranscriptId.current = task.transcript?.id || null;
  }, [task.transcript]);

  useEffect(() => {
    if (!task.audio_available) {
      setPlaying(false);
      setDuration(0);
      setCurrent(0);
    }
  }, [task.audio_available]);

  const seek = (seconds: number) => {
    if (!task.audio_available) {
      toast.push("音频已被删除，仅保留文稿", "info");
      return;
    }
    const audio = audioRef.current;
    if (!audio) return;
    pendingSeek.current = seconds;
    audio.currentTime = seconds;
    setCurrent(seconds);
    void audio.play().catch(() => undefined);
  };

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) {
      void audio.play().catch(() => toast.push("无法播放音频", "error"));
    } else {
      audio.pause();
    }
  };

  const handlePause = async () => {
    setBusy(true);
    try {
      await api.pauseTask(task.id);
      toast.push("已请求暂停，将在当前步骤结束后生效", "info");
      onRefresh();
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "暂停失败", "error");
    } finally {
      setBusy(false);
    }
  };

  const handleResume = async () => {
    setBusy(true);
    try {
      await api.resumeTask(task.id);
      toast.push("任务继续处理", "success");
      onRefresh();
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "继续失败", "error");
    } finally {
      setBusy(false);
    }
  };

  const doConfirm = async () => {
    const action = confirm;
    setConfirm(null);
    setBusy(true);
    try {
      if (action === "retry") {
        await api.retryTask(task.id);
        toast.push("任务已重新加入队列", "success");
        setTab("logs");
        onRefresh();
      } else if (action === "deleteTask") {
        await api.deleteTask(task.id);
        toast.push("任务已删除", "success");
        onDeleted();
      } else if (action === "deleteAudio") {
        await api.deleteAudio(task.id);
        toast.push("音频已删除，文稿与分析保留", "success");
        onRefresh();
      } else if (action === "terminate") {
        await api.cancelTask(task.id);
        toast.push("任务已终止", "success");
        onRefresh();
      }
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "操作失败", "error");
    } finally {
      setBusy(false);
    }
  };

  const active = task.status === "running" || task.status === "queued";
  const paused = task.status === "paused";
  const progress = Math.max(0, Math.min(100, task.progress));
  const currentStageLabel = STAGE_LABEL[task.stage] || task.stage;
  const events = task.events || [];
  const recentEvents = showAllLogs ? events.slice(-40) : events.slice(-5);
  const totalSpeakers = task.transcript?.speaker_count || 0;

  return (
    <>
      <div className="task-view">
        <section className="panel task-head">
          <div className="task-cover">
            {task.video.cover_url ? (
              <img
                src={coverUrl(task.video.cover_url)}
                alt=""
                referrerPolicy="no-referrer"
                loading="lazy"
              />
            ) : (
              <div style={{ display: "grid", placeItems: "center", height: "100%", color: "#c4b5fd" }}>
                <Icon name="play" size={28} />
              </div>
            )}
            <span className="cover-duration">{formatDuration(task.video.duration_sec)}</span>
          </div>

          <div className="task-head-main">
            <div className="task-kicker">
              <StatusBadge status={task.status} />
              <span className="status-chip" style={{ height: 24 }}>
                <span className="dot" />
                {task.video.platform === "bilibili" ? "Bilibili" : task.video.platform}
              </span>
              {task.audio_deleted && (
                <span className="status-chip" style={{ height: 24 }}>
                  已删除音频
                </span>
              )}
            </div>
            <h1 className="task-title">{task.video.title || "未命名视频"}</h1>
            <div className="task-meta">
              <span>{task.video.uploader || "未知作者"}</span>
              <span className="sep">·</span>
              <span>{formatDate(task.created_at)}</span>
              <span className="sep">·</span>
              <span>{formatDuration(task.video.duration_sec)}</span>
              {totalSpeakers > 0 && (
                <>
                  <span className="sep">·</span>
                  <span>{totalSpeakers} 位说话人</span>
                </>
              )}
              {task.transcript && (
                <>
                  <span className="sep">·</span>
                  <span>{task.transcript.segment_count} 段文稿</span>
                </>
              )}
            </div>

            <div className="task-actions">
              <a
                className="btn ghost sm"
                href={task.video.url}
                target="_blank"
                rel="noreferrer noopener"
              >
                <Icon name="external" size={13} />
                原视频
              </a>
              {active && (
                <button className="btn ghost sm" onClick={handlePause} disabled={busy}>
                  <Icon name="pause" size={12} />
                  暂停
                </button>
              )}
              {paused && (
                <button className="btn primary sm" onClick={handleResume} disabled={busy}>
                  <Icon name="play" size={12} />
                  继续
                </button>
              )}
              {(active || paused) && (
                <button className="btn ghost sm" onClick={() => setConfirm("terminate")} disabled={busy}>
                  <Icon name="stop" size={12} />
                  终止
                </button>
              )}
              {!active && task.status !== "completed" && !paused && (
                <button className="btn ghost sm" onClick={() => setConfirm("retry")} disabled={busy}>
                  <Icon name="refresh" size={13} />
                  重试
                </button>
              )}
              {paused && (
                <button className="btn subtle sm" onClick={() => setConfirm("retry")} disabled={busy}>
                  <Icon name="refresh" size={13} />
                  重新开始
                </button>
              )}
              {task.status === "completed" && (
                <button className="btn ghost sm" onClick={() => setConfirm("retry")} disabled={busy}>
                  <Icon name="refresh" size={13} />
                  重新解析
                </button>
              )}
              <button
                className="btn subtle sm"
                onClick={() => setConfirm("deleteTask")}
                disabled={busy}
                title="删除任务及文稿"
              >
                <Icon name="trash" size={13} />
              </button>
            </div>
          </div>
        </section>

        {task.status === "failed" && (
          <div className="error-panel">
            <Icon name="alert" size={18} />
            <div style={{ flex: 1 }}>
              <h4>处理失败</h4>
              <div>{task.error || task.message || "未知错误"}</div>
              <div className="hint" style={{ marginTop: 6 }}>
                已下载的音频会被保留，重试时不会重复下载。
              </div>
            </div>
            <button className="btn danger sm" onClick={() => setConfirm("retry")}>
              重试
            </button>
          </div>
        )}

        {(active || paused || task.status === "failed") && (
          <section className="panel progress-card">
            <div className="progress-top">
              <span className="stage-label">
                {active ? currentStageLabel : paused ? "已暂停" : "上次处理停在"}
              </span>
              <span className="percent">{progress}%</span>
            </div>
            <div className="progress-message">{task.message || "等待开始…"}</div>
            <div className="progress-track">
              <div
                className={`progress-fill ${paused ? "paused" : ""}`}
                style={{ width: `${progress}%` }}
              />
            </div>
            <div className="stage-steps">
              {STAGE_ORDER.map((stage) => {
                const done = task.status === "completed" || progress >= (STAGE_END[stage] || 0);
                const isActive = task.stage === stage && (active || paused);
                const skipped =
                  (stage === "separate" && task.options?.bgm_separation === false) ||
                  (stage === "denoise" && task.options?.denoise === false) ||
                  (stage === "diarize" && task.options?.diarization === false);
                return (
                  <div
                    key={stage}
                    className={`stage-step ${done ? "done" : ""} ${isActive ? "active" : ""}`}
                    title={STAGE_DESC[stage]}
                  >
                    <div className="bar" />
                    <div className="name">
                      {STAGE_LABEL[stage]}
                      {skipped && !done ? "（跳过）" : ""}
                    </div>
                  </div>
                );
              })}
            </div>

            <div className="log-drawer">
              {recentEvents.length === 0 && <div>暂无日志</div>}
              {recentEvents.map((event) => (
                <div key={event.id} className={`log-line ${event.level}`}>
                  <span className="t">{formatDateTime(event.created_at).slice(11)}</span>
                  <span>{event.message}</span>
                </div>
              ))}
              {events.length > 5 && (
                <button
                  className="btn subtle sm"
                  style={{ marginTop: 4, paddingLeft: 0 }}
                  onClick={() => setShowAllLogs((value) => !value)}
                >
                  {showAllLogs ? "收起日志" : `查看全部 ${events.length} 条日志`}
                </button>
              )}
            </div>
          </section>
        )}

        {task.audio_available ? (
          <section className="panel audio-bar">
            <audio
              ref={audioRef}
              src={audioUrl(task.id, variant)}
              preload="metadata"
              onLoadedMetadata={(event) => {
                const audio = event.currentTarget;
                setDuration(audio.duration || 0);
                if (pendingSeek.current !== null) {
                  audio.currentTime = pendingSeek.current;
                  pendingSeek.current = null;
                }
              }}
              onTimeUpdate={(event) => setCurrent(event.currentTarget.currentTime)}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onEnded={() => setPlaying(false)}
              onError={() => undefined}
            />
            <button className="audio-play" onClick={togglePlay} title={playing ? "暂停" : "播放"}>
              <Icon name={playing ? "pause" : "play"} size={16} />
            </button>
            <div className="audio-main">
              <div className="audio-time-row">
                <span>{formatClock(current)}</span>
                <span>{task.enhanced_available ? "增强人声（降噪 / 去背景音乐）" : "原始音轨"}</span>
                <span>{formatClock(duration)}</span>
              </div>
              <input
                className="audio-seek"
                type="range"
                min={0}
                max={duration || 0}
                step={0.1}
                value={Math.min(current, duration || 0)}
                style={{ ["--seek" as string]: `${duration ? (current / duration) * 100 : 0}%` }}
                onChange={(event) => {
                  const value = Number(event.target.value);
                  if (audioRef.current) audioRef.current.currentTime = value;
                  setCurrent(value);
                }}
              />
            </div>
            <div className="audio-side">
              {task.enhanced_available && task.audio_available && (
                <Segmented
                  size="sm"
                  value={variant}
                  onChange={(value) => {
                    if (audioRef.current) {
                      pendingSeek.current = audioRef.current.currentTime;
                    }
                    setVariant(value);
                  }}
                  options={[
                    { value: "enhanced", label: "增强" },
                    { value: "source", label: "原始" },
                  ]}
                />
              )}
              <button
                className="icon-btn"
                title="删除音频"
                onClick={() => setConfirm("deleteAudio")}
              >
                <Icon name="trash" size={14} />
              </button>
            </div>
          </section>
        ) : (
          task.status === "completed" && (
            <section className="panel" style={{ padding: "12px 16px", display: "flex", alignItems: "center", gap: 9, color: "var(--muted)", fontSize: 12 }}>
              <Icon name="volume" size={15} />
              音频已删除，文稿、AI 分析与讨论仍然保留。需要重听可以重新解析视频。
            </section>
          )
        )}

        <section className="panel" style={{ overflow: "hidden" }}>
          <div style={{ padding: 10, borderBottom: "1px solid var(--border)" }}>
            <div className="tabbar" style={{ border: 0, background: "transparent", padding: 0 }}>
              <button
                className={tab === "transcript" ? "active" : ""}
                onClick={() => setTab("transcript")}
              >
                <Icon name="file" size={14} />
                转录文稿
                {task.transcript && <span className="count">{task.transcript.segment_count}</span>}
              </button>
              <button
                className={tab === "analysis" ? "active" : ""}
                onClick={() => setTab("analysis")}
              >
                <Icon name="sparkles" size={14} />
                AI 解析
              </button>
              <button className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>
                <Icon name="message" size={14} />
                讨论
              </button>
              <button className={tab === "logs" ? "active" : ""} onClick={() => setTab("logs")}>
                <Icon name="clock" size={14} />
                处理日志
                <span className="count">{events.length}</span>
              </button>
            </div>
          </div>

          <div className="tab-content">
            {tab === "transcript" &&
              (task.transcript ? (
                <TranscriptView transcriptId={task.transcript.id} onSeek={seek} />
              ) : (
                <EmptyState
                  icon="mic"
                  title={active ? "正在生成转录文稿" : "还没有转录文稿"}
                  description={
                    active
                      ? "语音识别完成后会自动出现在这里，可以先查看处理日志了解进度。"
                      : "点击上方「重新解析」重新处理视频。"
                  }
                />
              ))}

            {tab === "analysis" &&
              (task.transcript ? (
                <AnalysisPanel transcriptId={task.transcript.id} />
              ) : (
                <EmptyState
                  icon="sparkles"
                  title="AI 解析尚未就绪"
                  description="需要先完成语音识别，才能对内容进行审校、摘要和观点提炼。"
                />
              ))}

            {tab === "chat" &&
              (task.transcript ? (
                <ChatPanel transcriptId={task.transcript.id} />
              ) : (
                <EmptyState
                  icon="message"
                  title="讨论尚未就绪"
                  description="转录完成后，你可以基于文稿与 AI 讨论视频内容。"
                />
              ))}

            {tab === "logs" && (
              <div style={{ padding: 16 }}>
                {events.length === 0 && (
                  <EmptyState icon="clock" title="暂无日志" description="任务开始处理后，这里会记录每一步。" />
                )}
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {[...events].reverse().map((event) => (
                    <div key={event.id} className={`log-line ${event.level}`} style={{ fontSize: 11.5 }}>
                      <span className="t">{formatDateTime(event.created_at)}</span>
                      <span style={{ color: "var(--faint)", minWidth: 64 }}>
                        {STAGE_LABEL[event.stage] || event.stage}
                      </span>
                      <span>{event.message}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </section>

        {!health?.llm_configured && (
          <div className="hint" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Icon name="alert" size={13} />
            AI 解析与讨论需要先配置大模型 API Key。
            <button className="btn ghost sm" onClick={onOpenSettings}>
              前往设置
            </button>
          </div>
        )}

        {health && !health.diarization_ready && task.options?.diarization && (
          <div className="hint" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Icon name="users" size={13} />
            首次使用说话人分离会自动下载约 40MB 的离线模型。
          </div>
        )}
      </div>

      <ConfirmDialog
        open={confirm !== null}
        title={
          confirm === "deleteTask"
            ? "删除这个任务？"
            : confirm === "deleteAudio"
              ? "删除音频文件？"
              : confirm === "terminate"
                ? "终止正在处理的任务？"
                : "重新解析视频？"
        }
        message={
          confirm === "deleteTask"
            ? "文稿、AI 分析、讨论记录和音频都会永久删除。"
            : confirm === "deleteAudio"
              ? "只会删除音频文件，文稿、AI 分析与讨论都会保留，之后无法重听。"
              : confirm === "terminate"
                ? "将立即停止处理；已下载的音频会保留，但不会生成文稿。之后可以重新解析。"
                : "将重新下载/处理音频并覆盖当前文稿；已有的 AI 分析与讨论会一并删除。"
        }
        confirmText={confirm === "retry" ? "重新解析" : confirm === "terminate" ? "终止" : "删除"}
        danger={confirm !== "retry"}
        onConfirm={doConfirm}
        onCancel={() => setConfirm(null)}
      />
    </>
  );
}
