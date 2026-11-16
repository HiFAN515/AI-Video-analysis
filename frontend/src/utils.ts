export function formatDuration(seconds: number): string {
  if (!seconds || !Number.isFinite(seconds)) return "00:00";
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "00:00";
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function formatTime(seconds: number): string {
  return `${formatDuration(seconds)}`;
}

export function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let index = 0;
  let value = bytes;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const now = Date.now();
  const diff = (now - date.getTime()) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  return `${date.getFullYear()}/${String(date.getMonth() + 1).padStart(2, "0")}/${String(
    date.getDate(),
  ).padStart(2, "0")}`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.getFullYear()}/${String(date.getMonth() + 1).padStart(2, "0")}/${String(
    date.getDate(),
  ).padStart(2, "0")} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

export const TASK_STATUS_LABEL: Record<string, string> = {
  queued: "排队中",
  running: "处理中",
  paused: "已暂停",
  completed: "已完成",
  failed: "失败",
  canceled: "已取消",
};

export const STAGE_LABEL: Record<string, string> = {
  queued: "排队",
  prepare: "准备",
  download: "获取音频",
  extract: "提取音轨",
  separate: "分离人声",
  denoise: "降噪",
  normalize: "响度标准化",
  diarize: "说话人分离",
  transcribe: "语音识别",
  finalize: "生成文稿",
  paused: "已暂停",
  done: "完成",
  canceled: "已取消",
};

export const STAGE_ORDER = [
  "download",
  "extract",
  "separate",
  "denoise",
  "normalize",
  "diarize",
  "transcribe",
  "finalize",
];

export const STAGE_DESC: Record<string, string> = {
  download: "yt-dlp 抓取音频",
  extract: "ffmpeg 解码 44.1kHz",
  separate: "Demucs 去除背景音乐",
  denoise: "频谱门限降噪",
  normalize: "EBU R128 响度",
  diarize: "sherpa-onnx 声纹聚类",
  transcribe: "faster-whisper 识别",
  finalize: "对齐时间戳与说话人",
};

export function speakerHue(speaker: string): number {
  let hash = 0;
  for (let i = 0; i < speaker.length; i += 1) {
    hash = (hash * 31 + speaker.charCodeAt(i)) % 360;
  }
  return hash;
}

export function speakerStyle(speaker: string): React.CSSProperties {
  const hue = speakerHue(speaker || "speaker");
  return {
    color: `hsl(${hue} 80% 76%)`,
    background: `hsl(${hue} 70% 60% / 0.12)`,
    borderColor: `hsl(${hue} 70% 60% / 0.28)`,
  };
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function truncate(value: string, max: number): string {
  if (!value) return "";
  return value.length > max ? `${value.slice(0, max)}…` : value;
}
