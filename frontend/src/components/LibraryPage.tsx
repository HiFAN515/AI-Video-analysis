import { useEffect, useState } from "react";
import { api, coverUrl, LibraryItem, LibraryStats } from "../api";
import { formatDate, formatDuration, formatBytes } from "../utils";
import { EmptyState, Icon, Spinner } from "./ui";

export default function LibraryPage({ onOpenTask }: { onOpenTask: (taskId: string) => void }) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState<LibraryStats | null>(null);

  useEffect(() => {
    api.stats().then(setStats).catch(() => undefined);
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    const timer = window.setTimeout(() => {
      api
        .library(query)
        .then((data) => {
          if (!active) return;
          setItems(data.items);
          setTotal(data.total);
        })
        .catch(() => undefined)
        .finally(() => active && setLoading(false));
    }, 260);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [query]);

  const hours = stats ? stats.total_duration_sec / 3600 : 0;
  const durationText = stats
    ? stats.total_duration_sec >= 3600
      ? `${hours.toFixed(1)}h`
      : formatDuration(stats.total_duration_sec)
    : "–";

  return (
    <div className="library">
      <div className="stats-grid">
        <div className="stat-card">
          <div className="label">
            <Icon name="file" size={13} /> 已存文稿
          </div>
          <div className="value">{stats?.transcripts ?? "–"}</div>
          <div className="sub">共处理 {stats?.tasks ?? 0} 个任务</div>
        </div>
        <div className="stat-card">
          <div className="label">
            <Icon name="clock" size={13} /> 内容总时长
          </div>
          <div className="value">{durationText}</div>
          <div className="sub">转录音频累计时长</div>
        </div>
        <div className="stat-card">
          <div className="label">
            <Icon name="users" size={13} /> 识别说话人
          </div>
          <div className="value">{stats?.speakers ?? "–"}</div>
          <div className="sub">跨全部文稿累计</div>
        </div>
        <div className="stat-card">
          <div className="label">
            <Icon name="sparkles" size={13} /> AI 沉淀
          </div>
          <div className="value">{(stats?.analyses ?? 0) + (stats?.conversations ?? 0)}</div>
          <div className="sub">
            {stats?.analyses ?? 0} 条分析 · {stats?.conversations ?? 0} 段讨论
          </div>
        </div>
      </div>

      <div className="library-toolbar">
        <div className="search-box">
          <Icon name="search" size={14} />
          <input
            className="input"
            placeholder="全文检索文稿、标题、UP 主…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            spellCheck={false}
          />
        </div>
        <span className="hint">{loading ? "检索中…" : `找到 ${total} 篇文稿`}</span>
        {stats && (
          <span className="hint" style={{ marginLeft: "auto" }}>
            <Icon name="database" size={12} /> 音频占用 {formatBytes(stats.audio_bytes)}
          </span>
        )}
      </div>

      {loading && items.length === 0 ? (
        <div className="loading-row">
          <Spinner size={18} /> 正在检索资料库…
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon="library"
          title={query ? "没有匹配的文稿" : "资料库还是空的"}
          description={
            query
              ? "换一个关键词试试，检索范围包括完整转录文本、标题和 UP 主。"
              : "完成第一个视频解析后，转录文稿、AI 分析与讨论都会沉淀在这里。"
          }
        />
      ) : (
        <div className="library-grid">
          {items.map((item) => (
            <button key={item.transcript_id} className="library-card" onClick={() => onOpenTask(item.task_id)}>
              <div className="cover">
                {item.cover_url ? (
                  <img src={coverUrl(item.cover_url)} alt="" loading="lazy" referrerPolicy="no-referrer" />
                ) : (
                  <div style={{ display: "grid", placeItems: "center", height: "100%", color: "#c4b5fd" }}>
                    <Icon name="play" size={22} />
                  </div>
                )}
              </div>
              <h3>{item.title}</h3>
              <div className="meta">
                <span>{item.uploader || "未知作者"}</span>
                <span>·</span>
                <span>{formatDuration(item.duration_sec)}</span>
                {item.speaker_count > 0 && (
                  <>
                    <span>·</span>
                    <span>{item.speaker_count} 位说话人</span>
                  </>
                )}
                <span>·</span>
                <span>{formatDate(item.created_at)}</span>
              </div>
              <div className="excerpt">{item.excerpt}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
