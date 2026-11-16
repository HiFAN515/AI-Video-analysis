import { useMemo, useState } from "react";
import { coverUrl, Task } from "../api";
import { formatDate, formatDuration, TASK_STATUS_LABEL } from "../utils";
import { Icon, Spinner } from "./ui";

export default function TaskList({
  tasks,
  selectedId,
  loading,
  onSelect,
  onRefresh,
}: {
  tasks: Task[];
  selectedId: string | null;
  loading: boolean;
  onSelect: (id: string) => void;
  onRefresh: () => void;
}) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return tasks;
    return tasks.filter(
      (task) =>
        task.video.title.toLowerCase().includes(keyword) ||
        task.video.uploader.toLowerCase().includes(keyword) ||
        (task.transcript?.id || "").includes(keyword),
    );
  }, [tasks, query]);

  return (
    <section className="panel history">
      <div className="history-head">
        <Icon name="clock" size={15} className="muted" />
        <h2>解析记录</h2>
        <span className="history-count">{tasks.length}</span>
        <button className="icon-btn" onClick={onRefresh} title="刷新">
          {loading ? <Spinner size={14} /> : <Icon name="refresh" size={14} />}
        </button>
      </div>
      <div className="history-search">
        <input
          className="input"
          placeholder="搜索标题 / UP 主"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          spellCheck={false}
        />
      </div>
      <div className="history-list">
        {filtered.length === 0 && (
          <div style={{ padding: "30px 12px", textAlign: "center", color: "var(--muted)", fontSize: 12 }}>
            {tasks.length === 0 ? "还没有解析记录" : "没有匹配的记录"}
          </div>
        )}
        {filtered.map((task) => (
          <button
            key={task.id}
            className={`history-item ${selectedId === task.id ? "active" : ""}`}
            onClick={() => onSelect(task.id)}
          >
            <span className={`history-status ${task.status}`} />
            <span className="history-thumb">
              {task.video.cover_url ? (
                <img
                  src={coverUrl(task.video.cover_url)}
                  alt=""
                  loading="lazy"
                  referrerPolicy="no-referrer"
                />
              ) : (
                <span className="placeholder">
                  <Icon name="play" size={14} />
                </span>
              )}
            </span>
            <span className="history-main">
              <span className="history-title">{task.video.title || "未命名视频"}</span>
              <span className="history-meta">
                <span>{TASK_STATUS_LABEL[task.status] || task.status}</span>
                <span>·</span>
                <span>{formatDuration(task.video.duration_sec)}</span>
                <span>·</span>
                <span>{formatDate(task.created_at)}</span>
              </span>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}
