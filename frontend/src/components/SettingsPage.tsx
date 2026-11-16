import { useEffect, useState } from "react";
import { api, ASRModels, Health, LibraryStats, LLMProfile, Settings } from "../api";
import { formatBytes } from "../utils";
import { ConfirmDialog, Icon, Segmented, Spinner, Switch, useToast } from "./ui";

interface SystemInfo {
  version: string;
  python: string;
  paths: Record<string, string>;
  disk: { total: number; used: number; free: number };
  asr: Record<string, string>;
  queue: { max_workers: number };
}

export default function SettingsPage({
  settings,
  health,
  onSaved,
  onRefreshHealth,
}: {
  settings: Settings;
  health: Health | null;
  onSaved: (settings: Settings) => void;
  onRefreshHealth: () => void;
}) {
  const toast = useToast();
  const [baseUrl, setBaseUrl] = useState(settings.llm.base_url);
  const [model, setModel] = useState(settings.llm.model);
  const [temperature, setTemperature] = useState(settings.llm.temperature);
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [asrBackend, setAsrBackend] = useState(settings.asr.backend);
  const [asrModel, setAsrModel] = useState(settings.asr.model);
  const [asrDevice, setAsrDevice] = useState(settings.asr.device);
  const [asrCompute, setAsrCompute] = useState(settings.asr.compute_type);
  const [asrLanguage, setAsrLanguage] = useState(settings.asr.language);
  const [cloudBase, setCloudBase] = useState(settings.asr.cloud_base_url);
  const [cloudModel, setCloudModel] = useState(settings.asr.cloud_model);
  const [cloudKey, setCloudKey] = useState("");
  const [bgm, setBgm] = useState(settings.audio.bgm_separation);
  const [denoise, setDenoise] = useState(settings.audio.denoise);
  const [diarization, setDiarization] = useState(settings.audio.diarization);
  const [maxSpeakers, setMaxSpeakers] = useState(settings.audio.max_speakers);
  const [threshold, setThreshold] = useState(settings.audio.threshold);
  const [deleteAudio, setDeleteAudio] = useState(settings.audio.delete_audio);
  const [audioDevice, setAudioDevice] = useState<"auto" | "cpu" | "cuda">(settings.audio.device || "auto");
  const [profiles, setProfiles] = useState<LLMProfile[]>([]);
  const [profileBusy, setProfileBusy] = useState(false);
  const [deleteProfile, setDeleteProfile] = useState<LLMProfile | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string; latency?: number | null } | null>(null);
  const [asrModels, setAsrModels] = useState<ASRModels | null>(null);
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [stats, setStats] = useState<LibraryStats | null>(null);
  const [cleaning, setCleaning] = useState(false);

  useEffect(() => {
    setBaseUrl(settings.llm.base_url);
    setModel(settings.llm.model);
    setTemperature(settings.llm.temperature);
    setAsrBackend(settings.asr.backend);
    setAsrModel(settings.asr.model);
    setAsrDevice(settings.asr.device);
    setAsrCompute(settings.asr.compute_type);
    setAsrLanguage(settings.asr.language);
    setCloudBase(settings.asr.cloud_base_url);
    setCloudModel(settings.asr.cloud_model);
    setBgm(settings.audio.bgm_separation);
    setDenoise(settings.audio.denoise);
    setDiarization(settings.audio.diarization);
    setMaxSpeakers(settings.audio.max_speakers);
    setThreshold(settings.audio.threshold);
    setDeleteAudio(settings.audio.delete_audio);
    setAudioDevice(settings.audio.device || "auto");
  }, [settings]);

  const refreshProfiles = () => {
    api.llmProfiles().then(setProfiles).catch(() => undefined);
  };

  useEffect(() => {
    api.asrModels().then(setAsrModels).catch(() => undefined);
    api.info().then((data) => setInfo(data as unknown as SystemInfo)).catch(() => undefined);
    api.stats().then(setStats).catch(() => undefined);
    refreshProfiles();
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      const patch: Record<string, unknown> = {
        llm_base_url: baseUrl,
        llm_model: model,
        llm_temperature: temperature,
        asr_backend: asrBackend,
        asr_model: asrModel,
        asr_device: asrDevice,
        asr_compute_type: asrCompute,
        asr_language: asrLanguage,
        cloud_asr_base_url: cloudBase,
        cloud_asr_model: cloudModel,
        bgm_separation: bgm,
        denoise,
        diarization,
        diarization_max_speakers: maxSpeakers,
        diarization_threshold: threshold,
        default_delete_audio: deleteAudio,
        audio_device: audioDevice,
      };
      if (apiKey.trim()) patch.llm_api_key = apiKey.trim();
      if (cloudKey.trim()) patch.cloud_asr_api_key = cloudKey.trim();
      const updated = await api.updateSettings(patch);
      onSaved(updated);
      setApiKey("");
      setCloudKey("");
      refreshProfiles();
      toast.push("设置已保存，并记录到模型配置列表", "success");
      onRefreshHealth();
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "保存失败", "error");
    } finally {
      setSaving(false);
    }
  };

  const clearKey = async (which: "llm" | "cloud") => {
    try {
      const patch = which === "llm" ? { clear_llm_api_key: true } : { clear_cloud_asr_api_key: true };
      const updated = await api.updateSettings(patch);
      onSaved(updated);
      toast.push("API Key 已清除", "success");
      onRefreshHealth();
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "操作失败", "error");
    }
  };

  const testLLM = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.testLLM({
        base_url: baseUrl,
        model,
        api_key: apiKey.trim() || undefined,
      });
      setTestResult({ ok: result.ok, message: result.message, latency: result.latency_ms });
    } catch (err) {
      setTestResult({ ok: false, message: err instanceof Error ? err.message : "测试失败" });
    } finally {
      setTesting(false);
    }
  };

  const cleanup = async () => {
    setCleaning(true);
    try {
      const result = await api.cleanup();
      toast.push(result.message, "success");
      api.stats().then(setStats).catch(() => undefined);
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "清理失败", "error");
    } finally {
      setCleaning(false);
    }
  };

  return (
    <div className="settings">
      <section className="panel settings-section">
        <div className="settings-section-head">
          <span className="ico">
            <Icon name="sparkles" size={17} />
          </span>
          <div style={{ flex: 1 }}>
            <h2>大模型（AI 解析与讨论）</h2>
            <p>兼容 OpenAI Chat Completions 接口，可填 DeepSeek、OpenAI、通义千问、Moonshot、Ollama 等。</p>
          </div>
          {settings.llm.api_key_set ? (
            <span className="badge-ok">已配置 Key</span>
          ) : (
            <span className="badge-warn">未配置 Key</span>
          )}
        </div>
        <div className="current-config">
          <span className="ico">
            <Icon name="link" size={15} />
          </span>
          <div>
            <div className="current-config-title">
              当前生效：{settings.llm.model}
              {settings.llm.api_key_set ? (
                <span className="badge-ok">Key 已保存</span>
              ) : (
                <span className="badge-warn">未配置 Key</span>
              )}
            </div>
            <div className="current-config-meta">
              {settings.llm.base_url} · {settings.llm.api_key_hint || "无 Key"} · 温度 {settings.llm.temperature}
            </div>
          </div>
        </div>

        <div className="form-grid">
          <div className="form-field">
            <label>Base URL</label>
            <input
              className="input"
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="https://api.deepseek.com/v1"
              spellCheck={false}
            />
          </div>
          <div className="form-field">
            <label>模型名称</label>
            <input
              className="input"
              value={model}
              onChange={(event) => setModel(event.target.value)}
              placeholder="deepseek-chat"
              spellCheck={false}
            />
          </div>
          <div className="form-field full">
            <label>
              API Key
              {settings.llm.api_key_set && (
                <span className="hint" style={{ fontSize: 10.5 }}>
                  当前：{settings.llm.api_key_hint}（留空则不修改）
                </span>
              )}
            </label>
            <div className="key-row">
              <input
                className="input"
                type={showKey ? "text" : "password"}
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder={settings.llm.api_key_set ? "已保存，输入新 Key 可替换" : "sk-..."}
                spellCheck={false}
                autoComplete="off"
              />
              <button className="icon-btn" onClick={() => setShowKey((value) => !value)} title="显示/隐藏">
                <Icon name={showKey ? "eyeOff" : "eye"} size={15} />
              </button>
              {settings.llm.api_key_set && (
                <button className="btn ghost sm" onClick={() => clearKey("llm")}>
                  清除
                </button>
              )}
            </div>
          </div>
          <div className="form-field">
            <label>温度（0 更稳定，1 更发散）</label>
            <input
              className="input"
              type="number"
              min={0}
              max={2}
              step={0.1}
              value={temperature}
              onChange={(event) => setTemperature(Number(event.target.value))}
            />
          </div>
          <div className="form-field">
            <label>连通性测试</label>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn ghost" onClick={testLLM} disabled={testing}>
                {testing ? <Spinner size={14} /> : <Icon name="link" size={14} />}
                测试连接
              </button>
              {testResult && (
                <span
                  style={{
                    fontSize: 11,
                    color: testResult.ok ? "var(--success)" : "var(--danger)",
                    alignSelf: "center",
                  }}
                >
                  {testResult.ok
                    ? `连接成功${testResult.latency ? ` · ${testResult.latency}ms` : ""}`
                    : testResult.message}
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="form-actions">
          <button className="btn primary" onClick={save} disabled={saving}>
            {saving ? <Spinner size={15} /> : <Icon name="check" size={15} />}
            {saving ? "保存中…" : "保存全部设置"}
          </button>
          <span className="hint">LLM / ASR / 音频默认值会一起保存。</span>
        </div>

        <div className="profile-block">
          <div className="profile-block-head">
            <div>
              <h3>已保存的模型配置</h3>
              <p>保存设置时会自动记录 Base URL、模型和 Key；点击「使用」即可切换。</p>
            </div>
            <button className="btn ghost sm" onClick={refreshProfiles}>
              <Icon name="refresh" size={13} />
              刷新
            </button>
          </div>
          {profiles.length === 0 ? (
            <p className="hint" style={{ padding: "10px 2px" }}>
              还没有保存的配置。填写上方 API Key 并保存后，这里会显示可切换的模型清单。
            </p>
          ) : (
            <div className="profile-list">
              {profiles.map((profile) => (
                <div key={profile.id} className={`profile-item ${profile.is_active ? "active" : ""}`}>
                  <div className="profile-main">
                    <div className="profile-name">
                      {profile.name}
                      {profile.is_active && <span className="badge-ok">当前使用</span>}
                    </div>
                    <div className="profile-meta">
                      {profile.model} · {profile.base_url} · {profile.api_key_hint || "无 Key"} · 温度{" "}
                      {profile.temperature}
                    </div>
                  </div>
                  <div className="profile-actions">
                    <button
                      className="btn ghost sm"
                      disabled={profileBusy || profile.is_active}
                      onClick={async () => {
                        setProfileBusy(true);
                        try {
                          const updated = await api.activateLLMProfile(profile.id);
                          onSaved(updated);
                          refreshProfiles();
                          toast.push(`已切换到 ${profile.model}`, "success");
                        } catch (err) {
                          toast.push(err instanceof Error ? err.message : "切换失败", "error");
                        } finally {
                          setProfileBusy(false);
                        }
                      }}
                    >
                      使用
                    </button>
                    <button
                      className="btn ghost sm"
                      disabled={profileBusy}
                      onClick={async () => {
                        setProfileBusy(true);
                        try {
                          const result = await api.testLLMProfile(profile.id);
                          toast.push(
                            result.ok
                              ? `连接成功${result.latency_ms ? ` · ${result.latency_ms}ms` : ""}`
                              : result.message,
                            result.ok ? "success" : "error",
                          );
                        } catch (err) {
                          toast.push(err instanceof Error ? err.message : "测试失败", "error");
                        } finally {
                          setProfileBusy(false);
                        }
                      }}
                    >
                      测试
                    </button>
                    <button
                      className="btn subtle sm"
                      disabled={profileBusy}
                      title="删除配置"
                      onClick={() => setDeleteProfile(profile)}
                    >
                      <Icon name="trash" size={13} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      <section className="panel settings-section">
        <div className="settings-section-head">
          <span className="ico">
            <Icon name="mic" size={17} />
          </span>
          <div style={{ flex: 1 }}>
            <h2>语音识别</h2>
            <p>本地 faster-whisper（推荐，数据不出机）或兼容 OpenAI 的云端识别接口。</p>
          </div>
          <span className={asrBackend === "local" ? "badge-ok" : "badge-warn"}>
            {asrBackend === "local" ? "本地识别" : "云端识别"}
          </span>
        </div>

        <div style={{ marginBottom: 14 }}>
          <Segmented
            value={asrBackend}
            onChange={setAsrBackend}
            options={[
              { value: "local", label: "本地 faster-whisper" },
              { value: "cloud", label: "云端 API" },
            ]}
          />
        </div>

        {asrBackend === "local" ? (
          <div className="form-grid">
            <div className="form-field">
              <label>识别模型</label>
              <select
                className="input"
                value={asrModel}
                onChange={(event) => setAsrModel(event.target.value)}
              >
                {(asrModels?.models || [{ id: asrModel, label: asrModel }]).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>设备</label>
              <select className="input" value={asrDevice} onChange={(event) => setAsrDevice(event.target.value)}>
                <option value="auto">自动（有 GPU 用 GPU）</option>
                <option value="cpu">CPU</option>
                <option value="cuda">CUDA（需 NVIDIA GPU）</option>
              </select>
            </div>
            <div className="form-field">
              <label>计算精度</label>
              <select
                className="input"
                value={asrCompute}
                onChange={(event) => setAsrCompute(event.target.value)}
              >
                <option value="int8">int8（CPU 最快）</option>
                <option value="float16">float16（GPU）</option>
                <option value="float32">float32（最准、最慢）</option>
              </select>
            </div>
            {asrDevice === "cuda" && !health?.features?.cuda && (
              <p className="hint warn form-field full">
                当前未检测到可用 CUDA，语音识别会自动回退到 CPU int8；GPU 可用后无需改代码，选 CUDA 即可。
              </p>
            )}
            <div className="form-field">
              <label>默认语言</label>
              <select
                className="input"
                value={asrLanguage}
                onChange={(event) => setAsrLanguage(event.target.value)}
              >
                <option value="auto">自动检测</option>
                <option value="zh">中文</option>
                <option value="en">English</option>
                <option value="ja">日本語</option>
              </select>
            </div>
            {String(asrModel).includes("large") && (
              <p className="hint form-field full" style={{ color: "var(--warn)" }}>
                注意：large 系列在 CPU 上需要数 GB 内存；8GB 显存 / 16GB 内存的机器建议使用 small。
                后端会在可用内存低于 10GB 时自动降级为 small。
              </p>
            )}
            {asrModels && asrModels.local.length > 0 && (
              <div className="form-field full">
                <label>已下载到本地的模型</label>
                <div className="hint">
                  {asrModels.local.map((item) => (
                    <span key={item.id} style={{ marginRight: 12 }}>
                      {item.id}
                      {item.size ? ` · ${formatBytes(item.size)}` : ""}
                      {item.ready ? " · 可用" : " · 未完成"}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="form-grid">
            <div className="form-field">
              <label>Base URL</label>
              <input
                className="input"
                value={cloudBase}
                onChange={(event) => setCloudBase(event.target.value)}
                placeholder="https://api.openai.com/v1"
                spellCheck={false}
              />
            </div>
            <div className="form-field">
              <label>模型</label>
              <input
                className="input"
                value={cloudModel}
                onChange={(event) => setCloudModel(event.target.value)}
                placeholder="whisper-1"
                spellCheck={false}
              />
            </div>
            <div className="form-field full">
              <label>
                API Key
                {settings.asr.cloud_api_key_set && (
                  <span className="hint" style={{ fontSize: 10.5 }}>
                    当前：{settings.asr.cloud_api_key_hint}（留空则不修改）
                  </span>
                )}
              </label>
              <div className="key-row">
                <input
                  className="input"
                  type="password"
                  value={cloudKey}
                  onChange={(event) => setCloudKey(event.target.value)}
                  placeholder={settings.asr.cloud_api_key_set ? "已保存，输入新 Key 可替换" : "sk-..."}
                  spellCheck={false}
                  autoComplete="off"
                />
                {settings.asr.cloud_api_key_set && (
                  <button className="btn ghost sm" onClick={() => clearKey("cloud")}>
                    清除
                  </button>
                )}
              </div>
            </div>
            <p className="hint form-field full">
              云端识别会压缩音频为 16kHz 单声道 MP3 后上传，长视频可能因服务商大小限制失败。Groq、SiliconFlow 等兼容接口均可使用。
            </p>
          </div>
        )}
      </section>

      <section className="panel settings-section">
        <div className="settings-section-head">
          <span className="ico">
            <Icon name="cpu" size={17} />
          </span>
          <div>
            <h2>音频预处理默认值</h2>
            <p>新建任务时可单独覆盖。背景音乐抑制使用 Demucs，说话人分离使用 sherpa-onnx。</p>
          </div>
        </div>
        <div className="option-row">
          <div className="label">
            默认开启背景音乐抑制
            <small>htdemucs 分离人声，CPU 上较慢但提升识别率</small>
          </div>
          <Switch checked={bgm} onChange={setBgm} />
        </div>
        <div className="option-row">
          <div className="label">
            默认开启降噪
            <small>noisereduce 频谱门限</small>
          </div>
          <Switch checked={denoise} onChange={setDenoise} />
        </div>
        <div className="option-row">
          <div className="label">
            默认开启说话人分离
            <small>首次使用会下载约 40MB 离线模型</small>
          </div>
          <Switch checked={diarization} onChange={setDiarization} />
        </div>
        <div className="form-grid" style={{ marginTop: 12 }}>
          <div className="form-field">
            <label>说话人数量（0 = 自动）</label>
            <select
              className="input"
              value={maxSpeakers}
              onChange={(event) => setMaxSpeakers(Number(event.target.value))}
            >
              <option value={0}>自动判断</option>
              {[1, 2, 3, 4, 5, 6].map((value) => (
                <option key={value} value={value}>
                  {value} 人
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label>聚类阈值（越高越容易拆成更多说话人）</label>
            <input
              className="input"
              type="number"
              min={0.1}
              max={0.95}
              step={0.05}
              value={threshold}
              onChange={(event) => setThreshold(Number(event.target.value))}
            />
          </div>
          <div className="form-field full">
            <label>人声分离设备（Demucs）</label>
            <select
              className="input"
              value={audioDevice}
              onChange={(event) => setAudioDevice(event.target.value as "auto" | "cpu" | "cuda")}
            >
              <option value="auto">自动（有 GPU 时使用 GPU）</option>
              <option value="cpu">CPU</option>
              <option value="cuda">CUDA（8GB 显存可跑 htdemucs）</option>
            </select>
            <span className="hint">
              降噪是 CPU 频谱算法，不是神经网络；GPU 主要加速 Demucs 人声分离和 faster-whisper 识别。
              {health?.features?.cuda ? " 当前检测到可用 CUDA。" : " 当前未检测到可用 CUDA，将使用 CPU。"}
            </span>
            {!health?.features?.cuda && health?.features?.gpu_present && (
              <span className="hint warn">
                检测到 NVIDIA 驱动，但当前进程无法访问 CUDA 设备（常见于容器/沙箱）。
                在宿主机终端运行 <code>bash scripts/start.sh</code> 即可启用 GPU。
              </span>
            )}
          </div>
        </div>
        <div className="option-row" style={{ marginTop: 8 }}>
          <div className="label">
            默认解析后删除音频
            <small>只保留文稿、AI 分析与讨论，节省磁盘</small>
          </div>
          <Switch checked={deleteAudio} onChange={setDeleteAudio} />
        </div>
        <div className="form-actions">
          <button className="btn primary" onClick={save} disabled={saving}>
            {saving ? <Spinner size={15} /> : <Icon name="check" size={15} />}
            {saving ? "保存中…" : "保存全部设置"}
          </button>
          <button className="btn ghost" onClick={onRefreshHealth}>
            <Icon name="refresh" size={14} />
            刷新运行状态
          </button>
        </div>
      </section>

      <section className="panel settings-section">
        <div className="settings-section-head">
          <span className="ico">
            <Icon name="database" size={17} />
          </span>
          <div>
            <h2>存储与运行环境</h2>
            <p>所有数据保存在本地 SQLite 与文件目录中，API Key 也仅存于本机数据库。</p>
          </div>
        </div>
        {info && (
          <>
            <div className="info-list">
              <div className="info-row">
                <span className="k">数据目录</span>
                <span className="v" title={info.paths.data}>
                  {info.paths.data}
                </span>
              </div>
              <div className="info-row">
                <span className="k">模型目录</span>
                <span className="v" title={info.paths.models}>
                  {info.paths.models}
                </span>
              </div>
              <div className="info-row">
                <span className="k">磁盘可用</span>
                <span className="v">
                  {formatBytes(info.disk.free)} / {formatBytes(info.disk.total)}
                </span>
              </div>
              <div className="info-row">
                <span className="k">ffmpeg</span>
                <span className="v">{health?.ffmpeg ? "可用" : "未安装"}</span>
              </div>
              <div className="info-row">
                <span className="k">Demucs 人声分离</span>
                <span className="v">{health?.demucs ? "可用" : "未安装（将跳过）"}</span>
              </div>
              <div className="info-row">
                <span className="k">说话人分离模型</span>
                <span className="v">{health?.diarization_ready ? "已就绪" : "首次使用时下载"}</span>
              </div>
            </div>
            <div className="form-actions">
              <button className="btn ghost" onClick={cleanup} disabled={cleaning}>
                {cleaning ? <Spinner size={14} /> : <Icon name="trash" size={14} />}
                清理无主音频文件
              </button>
              {stats && (
                <span className="hint">
                  文稿 {stats.transcripts} 篇 · 分析 {stats.analyses} 条 · 讨论 {stats.conversations} 段 · 音频{" "}
                  {formatBytes(stats.audio_bytes)}
                </span>
              )}
            </div>
          </>
        )}
      </section>

      <section className="panel settings-section">
        <div className="settings-section-head">
          <span className="ico">
            <Icon name="library" size={17} />
          </span>
          <div>
            <h2>技术选型与开源项目</h2>
            <p>
              本项目在动手前调研了同类开源项目，并复用成熟的音频 / 语音 / 大模型组件，而非从零实现。
            </p>
          </div>
        </div>
        <div className="about-links">
          <a className="about-link" href="https://github.com/JefferyHcool/BiliNote" target="_blank" rel="noreferrer">
            <Icon name="external" size={12} /> BiliNote · 视频笔记
          </a>
          <a className="about-link" href="https://github.com/KKKZOZ/bilibili2text" target="_blank" rel="noreferrer">
            <Icon name="external" size={12} /> bilibili2text · 字幕/ASR
          </a>
          <a className="about-link" href="https://github.com/yt-dlp/yt-dlp" target="_blank" rel="noreferrer">
            <Icon name="external" size={12} /> yt-dlp
          </a>
          <a className="about-link" href="https://github.com/adefossez/demucs" target="_blank" rel="noreferrer">
            <Icon name="external" size={12} /> Demucs
          </a>
          <a className="about-link" href="https://github.com/SYSTRAN/faster-whisper" target="_blank" rel="noreferrer">
            <Icon name="external" size={12} /> faster-whisper
          </a>
          <a className="about-link" href="https://github.com/k2-fsa/sherpa-onnx" target="_blank" rel="noreferrer">
            <Icon name="external" size={12} /> sherpa-onnx
          </a>
          <a className="about-link" href="https://github.com/turian/noisereduce" target="_blank" rel="noreferrer">
            <Icon name="external" size={12} /> noisereduce
          </a>
        </div>
        <p className="hint" style={{ marginTop: 12 }}>
          VideoMind v{info?.version || health?.version || "1.0.0"} · 本地优先 · 数据与讨论保存在你自己的机器上。
        </p>
      </section>

      <ConfirmDialog
        open={Boolean(deleteProfile)}
        title="删除这条模型配置？"
        message={`「${deleteProfile?.name || ""}」将从配置列表中移除；不会删除本机其他设置。`}
        confirmText="删除"
        danger
        onConfirm={async () => {
          if (!deleteProfile) return;
          try {
            await api.deleteLLMProfile(deleteProfile.id);
            refreshProfiles();
            toast.push("配置已删除", "success");
          } catch (err) {
            toast.push(err instanceof Error ? err.message : "删除失败", "error");
          } finally {
            setDeleteProfile(null);
          }
        }}
        onCancel={() => setDeleteProfile(null)}
      />
    </div>
  );
}
