import { useEffect, useRef, useState } from "react";
import {
  api,
  ASRModels,
  coverUrl,
  ProbeResult,
  Settings,
  Task,
  TaskOptions,
} from "../api";
import { formatDuration, formatDate } from "../utils";
import { Icon, Spinner, Switch, useToast } from "./ui";

const DEFAULT_OPTIONS: TaskOptions = {
  bgm_separation: true,
  denoise: true,
  denoise_strength: 0.85,
  diarization: true,
  max_speakers: 0,
  language: "auto",
  asr_model: null,
  asr_backend: null,
  delete_audio: false,
};

export default function NewTaskPanel({
  settings,
  asrModels,
  onCreated,
  onSelectTask,
}: {
  settings: Settings | null;
  asrModels: ASRModels | null;
  onCreated: (task: Task) => void;
  onSelectTask: (id: string) => void;
}) {
  const toast = useToast();
  const [url, setUrl] = useState("");
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [probing, setProbing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [options, setOptions] = useState<TaskOptions>(DEFAULT_OPTIONS);
  const initialized = useRef(false);

  useEffect(() => {
    if (!settings || initialized.current) return;
    initialized.current = true;
    setOptions((prev) => ({
      ...prev,
      bgm_separation: settings.audio.bgm_separation,
      denoise: settings.audio.denoise,
      diarization: settings.audio.diarization,
      max_speakers: settings.audio.max_speakers,
      delete_audio: settings.audio.delete_audio,
      asr_model: settings.asr.model,
      asr_backend: settings.asr.backend,
      language: settings.asr.language || "auto",
    }));
  }, [settings]);

  const resetProbe = () => {
    setProbe(null);
    setError("");
  };

  const handleProbe = async (event?: React.FormEvent) => {
    event?.preventDefault();
    const value = url.trim();
    if (!value) {
      setError("请先粘贴视频链接");
      return;
    }
    setProbing(true);
    setError("");
    setProbe(null);
    try {
      const result = await api.probe(value);
      setProbe(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取视频信息失败");
    } finally {
      setProbing(false);
    }
  };

  const handleCreate = async () => {
    if (!probe) return;
    setCreating(true);
    setError("");
    try {
      const task = await api.createTask(probe.url, options);
      toast.push("任务已创建，开始处理", "success");
      onCreated(task);
      setUrl("");
      setProbe(null);
      setAdvanced(false);
    } catch (err) {
      const message = err instanceof Error ? err.message : "创建任务失败";
      setError(message);
      toast.push(message, "error");
    } finally {
      setCreating(false);
    }
  };

  return (
    <section className="panel new-task">
      <div className="panel-head">
        <span className="brand-mark" style={{ width: 28, height: 28, borderRadius: 9 }}>
          <Icon name="link" size={15} />
        </span>
        <div>
          <h2>新建解析</h2>
        </div>
        <span className="sub" style={{ marginLeft: "auto" }}>
          Bilibili / 视频链接
        </span>
      </div>
      <div className="panel-body">
        <form onSubmit={handleProbe}>
          <div className="field">
            <div className="url-row">
              <input
                className="input"
                placeholder="粘贴视频链接或 BV 号，如 BV1xx411c7mD"
                value={url}
                onChange={(event) => {
                  setUrl(event.target.value);
                  if (probe) resetProbe();
                }}
                spellCheck={false}
              />
              <button
                type="submit"
                className="btn ghost"
                disabled={probing || !url.trim()}
                title="读取视频信息"
              >
                {probing ? <Spinner size={15} /> : <Icon name="search" size={16} />}
                读取
              </button>
            </div>
            <p className="hint" style={{ marginTop: 7 }}>
              支持 bilibili.com、b23.tv 短链、BV/av 号；YouTube 链接也可以解析。
            </p>
          </div>
        </form>

        {error && <div className="error-text">{error}</div>}

        {probe && (
          <>
            <div className="probe-card">
              <div className="probe-cover">
                {probe.cover_url ? (
                  <img src={coverUrl(probe.cover_url)} alt="" loading="lazy" referrerPolicy="no-referrer" />
                ) : (
                  <div style={{ display: "grid", placeItems: "center", height: "100%", color: "#c4b5fd" }}>
                    <Icon name="play" size={20} />
                  </div>
                )}
              </div>
              <div className="probe-info">
                <div className="probe-title">{probe.title}</div>
                <div className="probe-meta">
                  <span>{probe.uploader || "未知作者"}</span>
                  <span className="sep">·</span>
                  <span>{formatDuration(probe.duration_sec)}</span>
                  {probe.published_at && (
                    <>
                      <span className="sep">·</span>
                      <span>{formatDate(probe.published_at)}</span>
                    </>
                  )}
                </div>
                {probe.existing_tasks.length > 0 && (
                  <button
                    type="button"
                    className="probe-existing"
                    onClick={() => onSelectTask(probe.existing_tasks[0].id)}
                  >
                    <Icon name="clock" size={12} />
                    已解析过 {probe.existing_tasks.length} 次，查看最近一次
                  </button>
                )}
              </div>
            </div>

            <button
              type="button"
              className="options-toggle"
              onClick={() => setAdvanced((value) => !value)}
            >
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <Icon name="settings" size={13} />
                处理选项
              </span>
              <Icon
                name="chevron"
                size={14}
                className="chevron"
                style={{ transform: advanced ? "rotate(180deg)" : "none", transition: "0.2s" }}
              />
            </button>

            {advanced && (
              <div className="options-body">
                <div className="option-row">
                  <div className="label">
                    背景音乐抑制
                    <small>Demucs 分离人声，提升识别准确率</small>
                  </div>
                  <Switch
                    checked={options.bgm_separation}
                    onChange={(value) => setOptions({ ...options, bgm_separation: value })}
                  />
                </div>
                <div className="option-row">
                  <div className="label">
                    降噪
                    <small>频谱门限去除环境底噪</small>
                  </div>
                  <Switch
                    checked={options.denoise}
                    onChange={(value) => setOptions({ ...options, denoise: value })}
                  />
                </div>
                <div className="option-row">
                  <div className="label">
                    说话人分离
                    <small>离线声纹聚类，标注谁在说话</small>
                  </div>
                  <Switch
                    checked={options.diarization}
                    onChange={(value) => setOptions({ ...options, diarization: value })}
                  />
                </div>
                <div className="option-row">
                  <div className="label">
                    完成后删除音频
                    <small>文稿、AI 分析与讨论永久保留</small>
                  </div>
                  <Switch
                    checked={options.delete_audio}
                    onChange={(value) => setOptions({ ...options, delete_audio: value })}
                  />
                </div>

                <div className="option-grid">
                  <label className="mini-field">
                    <span>说话人数</span>
                    <select
                      className="input"
                      value={options.max_speakers}
                      disabled={!options.diarization}
                      onChange={(event) =>
                        setOptions({ ...options, max_speakers: Number(event.target.value) })
                      }
                    >
                      <option value={0}>自动判断</option>
                      {[1, 2, 3, 4, 5, 6].map((value) => (
                        <option key={value} value={value}>
                          {value} 人
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="mini-field">
                    <span>视频语言</span>
                    <select
                      className="input"
                      value={options.language}
                      onChange={(event) => setOptions({ ...options, language: event.target.value })}
                    >
                      <option value="auto">自动检测</option>
                      <option value="zh">中文</option>
                      <option value="en">English</option>
                      <option value="ja">日本語</option>
                      <option value="ko">한국어</option>
                    </select>
                  </label>
                </div>

                {options.asr_backend !== "cloud" && (
                  <label className="mini-field" style={{ marginTop: 8 }}>
                    <span>本地识别模型</span>
                    <select
                      className="input"
                      value={options.asr_model || ""}
                      onChange={(event) =>
                        setOptions({ ...options, asr_model: event.target.value || null })
                      }
                    >
                      {(asrModels?.models || []).map((model) => (
                        <option key={model.id} value={model.id}>
                          {model.label}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {options.asr_backend !== "cloud" && String(options.asr_model || "").includes("large") && (
                  <p className="hint warn" style={{ marginTop: 8 }}>
                    注意：large 模型在 CPU 上需要数 GB 内存，低内存/8GB 显存机器建议使用 small 或 medium。
                  </p>
                )}
                {options.asr_backend === "cloud" && (
                  <p className="hint" style={{ marginTop: 8 }}>
                    当前使用云端识别：{settings?.asr.cloud_model || "whisper-1"}
                  </p>
                )}
              </div>
            )}

            <button
              type="button"
              className="btn primary block new-task-submit"
              onClick={handleCreate}
              disabled={creating}
            >
              {creating ? <Spinner size={16} /> : <Icon name="sparkles" size={16} />}
              {creating ? "创建任务中…" : "开始下载并解析"}
            </button>
            <p className="hint" style={{ marginTop: 8, textAlign: "center" }}>
              确认后才会下载音频。预处理约需视频时长的 0.5–3 倍。
            </p>
          </>
        )}
      </div>
    </section>
  );
}
