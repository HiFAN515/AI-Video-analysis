import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  ASRModels,
  Health,
  LibraryStats,
  Settings,
  Task,
  TaskDetail,
} from "./api";
import LibraryPage from "./components/LibraryPage";
import NewTaskPanel from "./components/NewTaskPanel";
import SettingsPage from "./components/SettingsPage";
import TaskDetailView from "./components/TaskDetail";
import TaskList from "./components/TaskList";
import { Icon, Spinner, useToast } from "./components/ui";
import { formatDuration } from "./utils";

type View = "workspace" | "library" | "settings";

export default function App() {
  const toast = useToast();
  const [view, setView] = useState<View>("workspace");
  const [health, setHealth] = useState<Health | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [asrModels, setAsrModels] = useState<ASRModels | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [tasksLoading, setTasksLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [stats, setStats] = useState<LibraryStats | null>(null);
  const selectedRef = useRef<string | null>(null);
  const detailStatusRef = useRef<string | null>(null);

  useEffect(() => {
    selectedRef.current = selectedId;
  }, [selectedId]);

  useEffect(() => {
    detailStatusRef.current = detail?.status ?? null;
  }, [detail]);

  const refresh = useCallback(async () => {
    try {
      const [taskData, healthData, statsData] = await Promise.all([
        api.tasks(),
        api.health(),
        api.stats(),
      ]);
      setTasks(taskData.items);
      setHealth(healthData);
      setStats(statsData);
      const current = selectedRef.current;
      if (current) {
        const data = await api.task(current);
        if (data.id === selectedRef.current) setDetail(data);
      }
    } catch (err) {
      // Keep the UI usable if a transient poll fails.
      console.warn(err);
    } finally {
      setTasksLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([
      api.health(),
      api.settings(),
      api.asrModels(),
      api.tasks(),
      api.stats(),
    ])
      .then(([healthData, settingsData, models, taskData, statsData]) => {
        if (!active) return;
        setHealth(healthData);
        setSettings(settingsData);
        setAsrModels(models);
        setTasks(taskData.items);
        setStats(statsData);
        if (taskData.items.length > 0) {
          setSelectedId(taskData.items[0].id);
        }
      })
      .catch((err) => {
        toast.push(err instanceof Error ? err.message : "无法连接后端服务", "error");
      })
      .finally(() => active && setTasksLoading(false));
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let active = true;
    setDetailLoading(true);
    api
      .task(selectedId)
      .then((data) => {
        if (active) setDetail(data);
      })
      .catch((err) => {
        if (active) toast.push(err instanceof Error ? err.message : "加载任务失败", "error");
      })
      .finally(() => active && setDetailLoading(false));
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  useEffect(() => {
    let timer = 0;
    let cancelled = false;
    const ACTIVE = new Set(["running", "queued", "paused"]);

    const tick = async () => {
      if (!cancelled && !document.hidden) {
        try {
          const data = await api.tasks();
          if (cancelled) return;
          setTasks(data.items);
          const hasActive = data.items.some((task) => ACTIVE.has(task.status));
          const current = selectedRef.current;
          if (current) {
            const item = data.items.find((task) => task.id === current);
            // Fetch details while running, and exactly once when the status changes.
            if (item && (ACTIVE.has(item.status) || detailStatusRef.current !== item.status)) {
              const next = await api.task(current);
              if (!cancelled && next.id === selectedRef.current) {
                setDetail(next);
                detailStatusRef.current = next.status;
              }
            }
          }
          timer = window.setTimeout(tick, hasActive ? 3000 : 15000);
          return;
        } catch {
          /* transient network errors: retry slower */
        }
      }
      if (!cancelled) timer = window.setTimeout(tick, 15000);
    };

    timer = window.setTimeout(tick, 3000);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  const selectTask = (id: string) => {
    setSelectedId(id);
    setView("workspace");
  };

  const handleCreated = (task: Task) => {
    setTasks((prev) => [task, ...prev.filter((item) => item.id !== task.id)]);
    setSelectedId(task.id);
    setView("workspace");
    api.health().then(setHealth).catch(() => undefined);
    api.stats().then(setStats).catch(() => undefined);
  };

  const handleDeleted = () => {
    const id = selectedRef.current;
    setSelectedId(null);
    setDetail(null);
    setTasks((prev) => prev.filter((task) => task.id !== id));
    api.stats().then(setStats).catch(() => undefined);
  };

  const handleSettingsSaved = (updated: Settings) => {
    setSettings(updated);
    api.health().then(setHealth).catch(() => undefined);
  };

  const openSettings = () => setView("settings");

  const content = detail ? (
    <TaskDetailView
      task={detail}
      health={health}
      onRefresh={refresh}
      onDeleted={handleDeleted}
      onOpenSettings={openSettings}
    />
  ) : detailLoading ? (
    <div className="panel" style={{ minHeight: 420 }}>
      <div className="loading-row">
        <Spinner size={18} /> 正在加载任务…
      </div>
    </div>
  ) : (
    <Welcome stats={stats} />
  );

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">
            <Icon name="play" size={17} />
          </span>
          <div>
            <div className="brand-name">VideoMind</div>
            <div className="brand-sub">AI 视频解析工作台</div>
          </div>
        </div>

        <nav className="nav">
          <button className={view === "workspace" ? "active" : ""} onClick={() => setView("workspace")}>
            <Icon name="workspace" size={15} />
            <span>工作台</span>
          </button>
          <button className={view === "library" ? "active" : ""} onClick={() => setView("library")}>
            <Icon name="library" size={15} />
            <span>资料库</span>
          </button>
          <button className={view === "settings" ? "active" : ""} onClick={() => setView("settings")}>
            <Icon name="settings" size={15} />
            <span>设置</span>
          </button>
        </nav>

        <div className="topbar-spacer" />

        <div className="top-status">
          <span className={`status-chip keep ${health?.ffmpeg ? "ok" : "bad"}`}>
            <span className="dot" />
            ffmpeg
          </span>
          <span className={`status-chip ${health?.demucs ? "ok" : "warn"}`}>
            <span className="dot" />
            人声分离
          </span>
          <span className={`status-chip ${health?.features?.cuda ? "ok" : "warn"}`}>
            <span className="dot" />
            {health?.features?.cuda ? "GPU 可用" : health?.features?.gpu_present ? "GPU 受限" : "无 GPU"}
          </span>
          <span className={`status-chip ${health?.llm_configured ? "ok" : "warn"}`}>
            <span className="dot" />
            {health?.llm_configured ? "AI 已就绪" : "AI 未配置"}
          </span>
          <span className="status-chip ok">
            <span className="dot" />
            {health?.asr_backend === "cloud" ? "云端识别" : "本地识别"}
          </span>
        </div>
      </header>

      {view === "workspace" ? (
        <div className="layout">
          <aside className="sidebar">
            <NewTaskPanel
              settings={settings}
              asrModels={asrModels}
              onCreated={handleCreated}
              onSelectTask={selectTask}
            />
            <TaskList
              tasks={tasks}
              selectedId={selectedId}
              loading={tasksLoading}
              onSelect={selectTask}
              onRefresh={refresh}
            />
          </aside>
          <main className="content">{content}</main>
        </div>
      ) : (
        <div className="layout single">
          <main className="content">
            {view === "library" ? (
              <LibraryPage onOpenTask={selectTask} />
            ) : settings ? (
              <SettingsPage
                settings={settings}
                health={health}
                onSaved={handleSettingsSaved}
                onRefreshHealth={() => api.health().then(setHealth).catch(() => undefined)}
              />
            ) : (
              <div className="panel" style={{ minHeight: 320 }}>
                <div className="loading-row">
                  <Spinner size={18} /> 正在加载设置…
                </div>
              </div>
            )}
          </main>
        </div>
      )}
    </div>
  );
}

function Welcome({ stats }: { stats: LibraryStats | null }) {
  const hours = stats ? stats.total_duration_sec / 3600 : 0;
  return (
    <div className="welcome">
      <span className="welcome-badge">
        <Icon name="sparkles" size={13} />
        本地优先 · Bilibili → 可核查的知识
      </span>
      <h1>把视频变成可核查、可讨论的知识</h1>
      <p>
        粘贴一个视频链接，VideoMind 会自动下载音频，依次完成人声分离、降噪、响度标准化、说话人分离与语音识别，
        再用你配置的大模型做事实核查、逻辑审校和观点补充。文稿与讨论永久保存在本地。
      </p>

      <div className="flow">
        <span className="flow-step">
          <span className="num">1</span>链接确认
        </span>
        <span className="flow-arrow">→</span>
        <span className="flow-step">
          <span className="num">2</span>音频规范化
        </span>
        <span className="flow-arrow">→</span>
        <span className="flow-step">
          <span className="num">3</span>说话人转录
        </span>
        <span className="flow-arrow">→</span>
        <span className="flow-step">
          <span className="num">4</span>AI 审校讨论
        </span>
      </div>

      <div className="welcome-stats">
        <div className="welcome-stat">
          <b>{stats?.transcripts ?? 0}</b>
          <span>篇转录文稿</span>
        </div>
        <div className="welcome-stat">
          <b>{hours > 0 ? `${hours.toFixed(1)}h` : stats && stats.total_duration_sec > 0 ? formatDuration(stats.total_duration_sec) : "0 分钟"}</b>
          <span>累计内容时长</span>
        </div>
        <div className="welcome-stat">
          <b>{stats?.analyses ?? 0}</b>
          <span>条 AI 分析</span>
        </div>
        <div className="welcome-stat">
          <b>{stats?.conversations ?? 0}</b>
          <span>段讨论</span>
        </div>
      </div>

      <p style={{ marginTop: 26, fontSize: 12, color: "var(--faint)" }}>
        在左侧粘贴链接，先「读取」预览视频信息，确认后再开始下载解析。
      </p>
    </div>
  );
}
