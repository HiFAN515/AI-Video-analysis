export type TaskStatus = "queued" | "running" | "paused" | "completed" | "failed" | "canceled";

export interface VideoInfo {
  id: number;
  platform: string;
  remote_id: string;
  page: number;
  url: string;
  title: string;
  uploader: string;
  cover_url: string;
  duration_sec: number;
  description: string;
  published_at: string | null;
  created_at: string;
}

export interface TranscriptMeta {
  id: string;
  language: string;
  duration_sec: number;
  segment_count: number;
  speaker_count: number;
  asr_backend: string;
  asr_model: string;
  created_at: string;
}

export interface TaskOptions {
  bgm_separation: boolean;
  denoise: boolean;
  denoise_strength: number;
  diarization: boolean;
  max_speakers: number;
  language: string;
  asr_model: string | null;
  asr_backend: string | null;
  delete_audio: boolean;
}

export interface Task {
  id: string;
  status: TaskStatus;
  stage: string;
  progress: number;
  message: string;
  options: Partial<TaskOptions>;
  error: string | null;
  audio_available: boolean;
  enhanced_available: boolean;
  audio_deleted: boolean;
  delete_audio_after: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  video: VideoInfo;
  transcript: TranscriptMeta | null;
}

export interface TaskEvent {
  id: number;
  created_at: string;
  stage: string;
  level: "info" | "warn" | "error" | string;
  message: string;
  progress: number | null;
}

export interface TaskDetail extends Task {
  events: TaskEvent[];
}

export interface Segment {
  idx: number;
  start_ms: number;
  end_ms: number;
  speaker: string;
  text: string;
}

export interface Transcript extends TranscriptMeta {
  task_id: string;
  text: string;
  segments: Segment[];
}

export interface Analysis {
  id: string;
  transcript_id: string;
  kind: string;
  title: string;
  content: string;
  model: string;
  created_at: string;
}

export interface Message {
  id: number;
  role: "user" | "assistant" | "system" | string;
  content: string;
  model: string;
  created_at: string;
}

export interface Conversation {
  id: string;
  transcript_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message: string;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

export interface LibraryItem {
  task_id: string;
  transcript_id: string;
  title: string;
  uploader: string;
  cover_url: string;
  duration_sec: number;
  speaker_count: number;
  segment_count: number;
  created_at: string;
  excerpt: string;
}

export interface LibraryStats {
  tasks: number;
  transcripts: number;
  analyses: number;
  conversations: number;
  speakers: number;
  audio_files: number;
  audio_bytes: number;
  total_duration_sec: number;
}

export interface ProbeResult {
  url: string;
  platform: string;
  remote_id: string;
  page: number;
  title: string;
  uploader: string;
  cover_url: string;
  duration_sec: number;
  description: string;
  published_at: string | null;
  existing_tasks: Task[];
}

export interface LLMSettings {
  base_url: string;
  model: string;
  temperature: number;
  api_key_set: boolean;
  api_key_hint: string;
}

export interface ASRSettings {
  backend: string;
  model: string;
  device: string;
  compute_type: string;
  language: string;
  cloud_base_url: string;
  cloud_model: string;
  cloud_api_key_set: boolean;
  cloud_api_key_hint: string;
}

export interface AudioDefaults {
  bgm_separation: boolean;
  denoise: boolean;
  diarization: boolean;
  max_speakers: number;
  threshold: number;
  delete_audio: boolean;
  device: "auto" | "cpu" | "cuda";
}

export interface LLMProfile {
  id: string;
  name: string;
  base_url: string;
  model: string;
  temperature: number;
  api_key_set: boolean;
  api_key_hint: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Settings {
  llm: LLMSettings;
  asr: ASRSettings;
  audio: AudioDefaults;
  paths: Record<string, string>;
}

export interface Health {
  status: string;
  version: string;
  ffmpeg: boolean;
  demucs: boolean;
  asr_backend: string;
  llm_configured: boolean;
  diarization_ready: boolean;
  features: Record<string, boolean>;
  counts: Record<string, number>;
}

export interface ASRModel {
  id: string;
  label: string;
  size?: number;
  ready?: boolean;
}

export interface ASRModels {
  current: string;
  models: ASRModel[];
  local: ASRModel[];
}

const API = "/api";

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try {
      const data = await response.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* ignore */
    }
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  health: () => request<Health>("/system/health"),
  info: () => request<Record<string, unknown>>("/system/info"),
  asrModels: () => request<ASRModels>("/system/asr-models"),
  cleanup: () => request<{ message: string }>("/system/cleanup", { method: "POST" }),
  probe: (url: string) => request<ProbeResult>("/videos/probe", { method: "POST", body: JSON.stringify({ url }) }),
  tasks: (): Promise<{ items: Task[]; total: number }> => request("/tasks?limit=100"),
  task: (id: string) => request<TaskDetail>(`/tasks/${id}`),
  createTask: (url: string, options: Partial<TaskOptions>) =>
    request<Task>("/tasks", { method: "POST", body: JSON.stringify({ url, options }) }),
  pauseTask: (id: string) => request<Task>(`/tasks/${id}/pause`, { method: "POST" }),
  resumeTask: (id: string) => request<Task>(`/tasks/${id}/resume`, { method: "POST" }),
  cancelTask: (id: string) => request<Task>(`/tasks/${id}/cancel`, { method: "POST" }),
  retryTask: (id: string) => request<Task>(`/tasks/${id}/retry`, { method: "POST" }),
  deleteTask: (id: string) => request<{ message: string }>(`/tasks/${id}`, { method: "DELETE" }),
  deleteAudio: (id: string) => request<Task>(`/tasks/${id}/audio`, { method: "DELETE" }),
  transcript: (id: string) => request<Transcript>(`/transcripts/${id}`),
  analyses: (id: string) => request<Analysis[]>(`/transcripts/${id}/analyses`),
  conversations: (id: string) => request<Conversation[]>(`/transcripts/${id}/conversations`),
  createConversation: (id: string, title = "") =>
    request<Conversation>(`/transcripts/${id}/conversations`, { method: "POST", body: JSON.stringify({ title }) }),
  conversation: (id: string) => request<ConversationDetail>(`/conversations/${id}`),
  deleteConversation: (id: string) => request<{ message: string }>(`/conversations/${id}`, { method: "DELETE" }),
  deleteAnalysis: (id: string) => request<{ message: string }>(`/analyses/${id}`, { method: "DELETE" }),
  settings: () => request<Settings>("/settings"),
  updateSettings: (patch: Record<string, unknown>) =>
    request<Settings>("/settings", { method: "PUT", body: JSON.stringify(patch) }),
  llmProfiles: () => request<LLMProfile[]>("/llm-profiles"),
  saveLLMProfile: (payload: { name?: string; base_url: string; model: string; api_key?: string; temperature?: number }) =>
    request<LLMProfile>("/llm-profiles", { method: "POST", body: JSON.stringify(payload) }),
  saveCurrentLLMProfile: () => request<LLMProfile>("/llm-profiles/save-current", { method: "POST" }),
  activateLLMProfile: (id: string) =>
    request<Settings>(`/llm-profiles/${id}/activate`, { method: "POST" }),
  testLLMProfile: (id: string) =>
    request<{ ok: boolean; message: string; latency_ms: number | null }>(`/llm-profiles/${id}/test`, { method: "POST" }),
  deleteLLMProfile: (id: string) => request<{ message: string }>(`/llm-profiles/${id}`, { method: "DELETE" }),
  testLLM: (payload: { base_url?: string; model?: string; api_key?: string }) =>
    request<{ ok: boolean; message: string; latency_ms: number | null }>("/settings/test-llm", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  library: (q: string, limit = 40, offset = 0) =>
    request<{ items: LibraryItem[]; total: number }>(
      `/library?q=${encodeURIComponent(q)}&limit=${limit}&offset=${offset}`,
    ),
  stats: () => request<LibraryStats>("/library/stats"),
};

export function coverUrl(url?: string | null): string {
  if (!url) return "";
  return `${API}/media/cover?url=${encodeURIComponent(url)}`;
}

export function audioUrl(taskId: string, variant: "enhanced" | "source" = "enhanced") {
  return `${API}/tasks/${taskId}/audio?variant=${variant}`;
}

export function exportUrl(transcriptId: string, format: "md" | "txt" | "srt" | "json") {
  return `${API}/transcripts/${transcriptId}/export?format=${format}`;
}

export function downloadUrl(path: string) {
  return `${API}${path}`;
}

export interface SSEHandlers {
  onDelta?: (text: string) => void;
  onReasoning?: (text: string) => void;
  onEvent?: (event: Record<string, unknown>) => void;
  signal?: AbortSignal;
}

async function postSSE(path: string, body: unknown, handlers: SSEHandlers): Promise<void> {
  const response = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: handlers.signal,
  });
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try {
      const data = await response.json();
      detail = data.detail || detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(detail, response.status);
  }
  if (!response.body) throw new Error("浏览器不支持流式响应");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload) continue;
      let event: Record<string, unknown>;
      try {
        event = JSON.parse(payload);
      } catch {
        continue;
      }
      if (typeof event.reasoning === "string") handlers.onReasoning?.(event.reasoning as string);
      if (typeof event.delta === "string") handlers.onDelta?.(event.delta as string);
      handlers.onEvent?.(event);
    }
  }
}

export const stream = {
  analysis: (
    transcriptId: string,
    payload: { kind: string; instructions?: string; title?: string },
    handlers: SSEHandlers,
  ) => postSSE(`/transcripts/${transcriptId}/analyses/stream`, payload, handlers),
  chat: (conversationId: string, content: string, handlers: SSEHandlers) =>
    postSSE(`/conversations/${conversationId}/messages/stream`, { content }, handlers),
};

export { ApiError };
