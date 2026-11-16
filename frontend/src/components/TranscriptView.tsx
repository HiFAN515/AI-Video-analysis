import { useEffect, useMemo, useRef, useState } from "react";
import { api, exportUrl, Transcript } from "../api";
import { formatClock, speakerStyle } from "../utils";
import { EmptyState, Icon, Spinner, useToast } from "./ui";

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function Highlight({ text, query }: { text: string; query: string }) {
  if (!query.trim()) return <>{text}</>;
  const parts = text.split(new RegExp(`(${escapeRegExp(query.trim())})`, "gi"));
  return (
    <>
      {parts.map((part, index) =>
        part.toLowerCase() === query.trim().toLowerCase() ? (
          <mark key={index}>{part}</mark>
        ) : (
          <span key={index}>{part}</span>
        ),
      )}
    </>
  );
}

export default function TranscriptView({
  transcriptId,
  onSeek,
}: {
  transcriptId: string;
  onSeek: (seconds: number) => void;
}) {
  const toast = useToast();
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [speaker, setSpeaker] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setSpeaker(null);
    api
      .transcript(transcriptId)
      .then((data) => {
        if (active) setTranscript(data);
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : "加载文稿失败");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [transcriptId]);

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const speakers = useMemo(() => {
    if (!transcript) return [];
    return Array.from(new Set(transcript.segments.map((seg) => seg.speaker).filter(Boolean)));
  }, [transcript]);

  const visible = useMemo(() => {
    if (!transcript) return [];
    const keyword = search.trim().toLowerCase();
    return transcript.segments.filter((seg) => {
      if (speaker && seg.speaker !== speaker) return false;
      if (keyword && !seg.text.toLowerCase().includes(keyword)) return false;
      return true;
    });
  }, [transcript, search, speaker]);

  const copyAll = async () => {
    if (!transcript) return;
    try {
      await navigator.clipboard.writeText(transcript.text);
      toast.push("完整文稿已复制到剪贴板", "success");
    } catch {
      toast.push("复制失败，请检查浏览器权限", "error");
    }
  };

  const download = (format: "md" | "txt" | "srt" | "json") => {
    setMenuOpen(false);
    const anchor = document.createElement("a");
    anchor.href = exportUrl(transcriptId, format);
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  };

  if (loading) {
    return (
      <div className="loading-row">
        <Spinner size={18} /> 正在加载文稿…
      </div>
    );
  }

  if (error) {
    return <EmptyState icon="alert" title="文稿加载失败" description={error} />;
  }

  if (!transcript || transcript.segments.length === 0) {
    return <EmptyState icon="file" title="没有文稿内容" description="识别结果为空，可以尝试重新解析。" />;
  }

  const showSpeakers = transcript.speaker_count > 1;

  return (
    <div className="transcript">
      <div className="transcript-toolbar">
        <div className="search-box">
          <Icon name="search" size={14} />
          <input
            className="input"
            placeholder="在文稿中搜索…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <span className="hint" style={{ fontSize: 11 }}>
          {visible.length} / {transcript.segment_count} 段
        </span>
        <div className="toolbar-spacer" />
        <button className="btn ghost sm" onClick={copyAll} title="复制完整文稿">
          <Icon name="copy" size={14} />
          复制
        </button>
        <div className="dropdown" ref={menuRef}>
          <button className="btn ghost sm" onClick={() => setMenuOpen((value) => !value)}>
            <Icon name="download" size={14} />
            导出
            <Icon name="chevron" size={12} />
          </button>
          {menuOpen && (
            <div className="dropdown-menu">
              <button onClick={() => download("md")}>
                <Icon name="file" size={14} />
                Markdown 文稿
              </button>
              <button onClick={() => download("txt")}>
                <Icon name="file" size={14} />
                纯文本 TXT
              </button>
              <button onClick={() => download("srt")}>
                <Icon name="file" size={14} />
                字幕 SRT
              </button>
              <button onClick={() => download("json")}>
                <Icon name="database" size={14} />
                JSON 数据
              </button>
            </div>
          )}
        </div>
      </div>

      {showSpeakers && speakers.length > 0 && (
        <div className="speaker-filter">
          <button
            className={`speaker-chip ${speaker === null ? "active" : ""}`}
            onClick={() => setSpeaker(null)}
          >
            全部说话人
          </button>
          {speakers.map((name) => (
            <button
              key={name}
              className={`speaker-chip ${speaker === name ? "active" : ""}`}
              onClick={() => setSpeaker(speaker === name ? null : name)}
            >
              <span className="speaker-dot" style={{ background: speakerStyle(name).color }} />
              {name}
            </button>
          ))}
        </div>
      )}

      <div className="segment-list">
        {visible.length === 0 && (
          <div style={{ padding: "30px 0", textAlign: "center", color: "var(--muted)", fontSize: 12 }}>
            没有匹配的片段
          </div>
        )}
        {visible.map((seg) => (
          <div className="segment-row" key={seg.idx}>
            <button
              className="segment-time"
              onClick={() => onSeek(seg.start_ms / 1000)}
              title="跳转到该时间"
            >
              {formatClock(seg.start_ms / 1000)}
            </button>
            <div className="segment-main">
              {showSpeakers && seg.speaker && (
                <span className="segment-speaker" style={speakerStyle(seg.speaker)}>
                  {seg.speaker}
                </span>
              )}
              <div className="segment-text">
                <Highlight text={seg.text} query={search} />
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="transcript-footer">
        <Icon name="mic" size={12} />
        <span>
          {transcript.asr_backend === "cloud" ? "云端识别" : "本地 faster-whisper"} ·{" "}
          {transcript.asr_model.split("/").pop()} · {transcript.language || "自动语言"} ·{" "}
          {transcript.speaker_count > 0 ? `${transcript.speaker_count} 位说话人` : "未做说话人分离"}
        </span>
        <span style={{ marginLeft: "auto" }}>点击时间戳可跳转播放</span>
      </div>
    </div>
  );
}
