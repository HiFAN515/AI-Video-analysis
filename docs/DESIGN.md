# VideoMind · 设计方案

> 目标：输入 Bilibili 视频链接 → 下载音频 → 规范化处理 → 说话人分离转录 → AI 内容审校与讨论 → 本地长期存储。

## 1. 需求拆解

| # | 需求 | 落点 |
| --- | --- | --- |
| 1 | 链接 → 下载音频 → 转文本 → AI 解析 | yt-dlp + ffmpeg + faster-whisper + OpenAI 兼容 LLM |
| 2 | Web 交互，确认后再下载，可选删除音频 | React 单页应用；`/videos/probe` 预览；任务级 `delete_audio` |
| 3 | 音频规范化：去 BGM、降噪、说话人分离、ASR | 8 段式流水线，逐级可降级 |
| 4 | AI 判断内容正误、补充观点 | 「内容审校」提示词 + 多轮讨论 |
| 5 | 存储转录与讨论 | SQLite（WAL）+ 本地音频目录 |
| 6 | 先搜 GitHub，不重复造轮子 | 见第 2 节；复用 8 个成熟开源项目 |

## 2. 动手前的开源调研

调研时间：2026-11。数据来自 GitHub / PyPI。

| 项目 | 规模 | 能力 | 与本需求的差距 |
| --- | --- | --- | --- |
| [BiliNote](https://github.com/JefferyHcool/BiliNote) | ~7.7k★, MIT | Bilibili/YouTube 下载、FastAPI + React、Whisper/FunASR/BCut 转写、LLM 笔记、聊天、SQLite | 没有音频规范化链路（去 BGM / 降噪 / 说话人分离），转写是“直接 ASR”；工程庞大，作为 fork 基线改造成本高于重写核心 |
| [bilibili2text](https://github.com/KKKZOZ/bilibili2text) | ~100★, MIT | FastAPI + Vue 3，字幕优先、云端 ASR、LLM 总结、RAG、多格式导出 | 以 B 站字幕为主要来源，无本地音频预处理与说话人分离；强依赖云 ASR |
| [BiliSum](https://github.com/lycohana/BiliSum) | ~676★, MIT | Whisper 转写 + AI 摘要 + 知识库/RAG | 偏摘要与知识库，无去 BGM/降噪/说话人分离流程 |
| [YouDub-webui](https://github.com/liuzhao1225/YouDub-webui) | ~5.8k★, Apache-2.0 | Demucs + Whisper 的完整音视频本地化流水线 | 目标是翻译配音，链路重且带 TTS/视频合成；只借鉴其“分离→识别”的编排思路 |
| [whisperX](https://github.com/m-bain/whisperX) | 高星 | ASR + 强制对齐 + pyannote 说话人分离 | pyannote 需要 HuggingFace Token 与额外授权；依赖偏重 |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | 高星 | CTranslate2 加速的 Whisper，CPU int8 可用 | 只做 ASR，不解决 BGM/噪声/说话人 |
| [Demucs](https://github.com/adefossez/demucs) | 高星 | htdemucs 音源分离，vocals 轨质量好 | 只做分离 |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | 高星 | 离线说话人分割 + 声纹嵌入 + 聚类 | 需要自己拼装与对齐 |
| [noisereduce](https://github.com/turian/noisereduce) | 高星 | 频谱门限降噪，纯 CPU | 只做降噪 |

### 结论：不直接 fork，组合成熟组件

1. 需求 3 是同类项目普遍缺失的一环，fork 后仍要重写音频层；而音频层恰好是最适合独立编排的部分。
2. BiliNote/bilibili2text 的前端与业务模型都很重，裁剪成本高；本需求的数据模型（文稿/分析/讨论）更简单清晰。
3. 转录、分离、说话人、下载都有成熟库，自己写的部分只剩“编排、对齐、存储、交互”，这正是差异化价值。
4. 因此：**用现有项目做组件与交互参考，不重复实现底层模型**。

复用的组件：

```
yt-dlp           下载 / 元数据
ffmpeg           解码、重采样、loudnorm、mp3
Demucs           htdemucs 人声/伴奏分离
noisereduce      频谱门限降噪
sherpa-onnx      pyannote 分割 + 3D-Speaker 声纹 + 快速聚类
faster-whisper   CTranslate2 Whisper（本地）/ OpenAI 兼容云端 ASR
OpenAI 兼容 API  DeepSeek / OpenAI / Qwen / Ollama
FastAPI + SQLite 服务与存储
React + Vite     交互界面
```

## 3. 总体架构

```
┌──────────────────────────── Browser (React) ────────────────────────────┐
│  新建解析   任务进度   文稿（说话人/时间戳/搜索/导出）  AI 解析  讨论  资料库  │
└───────────────▲──────────────────────────────────────────▲──────────────┘
                │ REST / SSE                                │
┌───────────────┴──────────────────────────────────────────┴──────────────┐
│ FastAPI                                                                  │
│  /api/videos/probe   /api/tasks   /api/transcripts   /api/library         │
│  /api/settings       /api/system  /api/conversations                     │
│                                                                          │
│  JobManager (ThreadPool, 默认 1 并发)                                     │
│    └── Pipeline：prepare → download → extract → separate → denoise        │
│                → normalize → diarize → transcribe → finalize             │
│                                                                          │
│  SQLite (WAL)  videos / tasks / task_events / transcripts / segments      │
│                analyses / conversations / messages / settings             │
│  Files         data/audio/<task_id>/source.* + enhanced.mp3               │
└──────────────────────────────────────────────────────────────────────────┘
```

设计原则：

- **本地优先**：默认本地 ASR、本地分离、本地存储；云端 ASR / LLM 只是可选后端。
- **可降级**：任何音频增强步骤失败都只记 warning，继续用上一级音频，不让任务整体失败。
- **确认后下载**：`probe` 只读元数据；创建任务才真正下载。
- **文稿与媒体解耦**：音频可随时删除，文稿、分析、讨论独立存在。
- **幂等重试**：保留已下载的 `source.*`，重试不会重新下载。

## 4. 音频规范化流水线

| 阶段 | 实现 | 关键参数 | 失败策略 |
| --- | --- | --- | --- |
| download | yt-dlp `bestaudio[ext=m4a]/bestaudio` | 代理 / Cookie 可配 | 任务失败，保留下载缓存 |
| extract | ffmpeg → PCM 44.1kHz 双声道 | 固定 44.1k（Demucs 要求） | 失败即失败 |
| separate | Demucs `htdemucs`，取 vocals 轨 | 2 分钟分块 + 1 秒交叉淡化，控制内存 | 回退原音轨 |
| denoise | noisereduce 非平稳谱门限 | `prop_decrease=0.85`，5 分钟分块 | 回退分离后音轨 |
| normalize | ffmpeg `loudnorm I=-16 TP=-1.5 LRA=11` → 16kHz 单声道 | EBU R128 | 回退降噪音轨 |
| diarize | sherpa-onnx：pyannote-3.0 分割 + 3dspeaker eres2net 声纹 + 聚类 | 自动或指定人数，阈值 0.5 | 回退为无说话人标记 |
| transcribe | faster-whisper（本地）或 OpenAI 兼容 `/audio/transcriptions` | int8 / VAD / word timestamps；GPU 时 float16 | 任务失败；CUDA 不可用时回退 CPU |
| finalize | 词级时间戳与说话人 turn 对齐、合并、格式化 | 相邻同说话人 gap ≤0.75s | — |

### 4.0 GPU 加速

- Demucs 通过 `audio_device = auto | cpu | cuda` 选择设备；`auto` 在 `torch.cuda.is_available()` 时用 CUDA。按 60 秒分块（1 秒交叉淡化），8GB 显存可运行 htdemucs；CUDA OOM 或模型加载失败时自动回退 CPU。
- faster-whisper 支持 `device=cuda` + `compute_type=float16`；但 CTranslate2 4.x 的 Linux wheel 按 **CUDA 12** 编译，运行时硬编码 `libcublas.so.12` / `libcudnn.so.9`。如果机器只装了 CUDA 13（`libcublas.so.13`），会报 `libcublas.so.12 not found`。`scripts/_cuda_env.sh` 会把项目内 `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` 的路径加入 `LD_LIBRARY_PATH`；`start.sh` 会自动加载。CUDA 不可用（设备缺失或库不匹配）时回退 CPU int8，并再次执行内存守卫。
- 降噪使用 noisereduce 频谱门限，属于 CPU 信号处理，没有 GPU 版本；它的耗时远小于 Demucs/ASR，因此不构成瓶颈。

### 4.1 分块处理，避免长视频 OOM

1 小时 44.1kHz 双声道 float32 约 1.27GB。`separate_vocals` 每 2 分钟读一段、`apply_model` 后写入输出，并在重叠区做线性 crossfade；降噪同样按 5 分钟分块。内存占用与视频长度无关。

> Demucs 的 `openunmix` 依赖在导入时会 `import torchaudio`，但 htdemucs (`wiener_iters=0`) 并不会调用它。项目用 ffmpeg + soundfile 完成所有音频 I/O，因此 `audio.py` 在 torchaudio 缺失/ABI 不匹配时注入一个占位模块，避免被迫安装与 torch 版本不匹配的 torchaudio。

### 4.2 暂停、继续与终止

- 任务表新增 `pause_requested`、`resume_stage`；状态增加 `paused`。
- 每个阶段结束调用 `checkpoint(next_stage)`；Demucs 每个分块、降噪每个分块、ASR 每个片段都会检查一次控制请求。
- 暂停时记录 `resume_stage`；继续时跳过已完成阶段并复用中间产物（`vocals.wav` / `denoised.wav` / `clean.wav` / `diarization.json` / `asr.json`）。若暂停发生在阶段中间，会删除该阶段的半成品并重做该阶段。
- 终止会清理中间 wav/json，保留 `source.*` 以便重新解析；暂停不会清理，以便快速恢复。
- 下载与 sherpa-onnx 原生分离只能在阶段结束时响应暂停/终止；其余阶段通常在一个分块或一个识别片段内响应。

### 4.3 说话人对齐算法

```
diarization turns:  (start, end, speaker)
ASR segments + word timestamps
  → 每个词与 turns 求时间重叠，取重叠最大的 speaker
  → 词序列按 speaker 变化切分
  → 无词级时间戳时，按 segment 与 turn 的最大重叠判定
  → 相邻且同 speaker、间隔 ≤0.75s、总长 ≤260 字的片段合并
  → speaker 编号按首次出现映射为「说话人 1..N」
```

## 5. 数据模型（SQLite）

```
videos          id, platform, remote_id, page, url, title, uploader, cover, duration, ...
tasks           id(uuid), video_id, status, stage, progress, options(JSON), error,
                audio_path, processed_path, audio_deleted, delete_audio_after, ...
task_events     task_id, stage, level, message, progress, created_at
transcripts     id, task_id(unique), language, backend, model, text, duration,
                segment_count, speaker_count
segments        transcript_id, idx, start_ms, end_ms, speaker, text
analyses        id, transcript_id, kind, title, content, model
conversations   id, transcript_id, title, created_at, updated_at
messages        id, conversation_id, role, content, model
settings        key, value(JSON)   # 运行时配置，API Key 仅存本地
llm_profiles    id, name, base_url, model, api_key, temperature  # 可见的模型/API 档案
```

- 删除任务级联删除文稿 / 分析 / 讨论 / 事件。
- 删除音频只清空文件与路径标记，不影响上述记录。
- 重试会删除旧文稿，因此旧分析与讨论也会级联删除（UI 有明确提示）。

## 6. API 摘要

```
POST /api/videos/probe                     链接 → 标题/封面/时长 + 历史任务
POST /api/tasks                            创建任务（options 控制流水线）
GET  /api/tasks?status=                    列表
GET  /api/tasks/{id}                       详情 + 最近 200 条事件
POST /api/tasks/{id}/pause|resume          暂停 / 继续
POST /api/tasks/{id}/cancel|retry          终止 / 重试
DELETE /api/tasks/{id}                     删除任务
DELETE /api/tasks/{id}/audio               只删音频
GET  /api/tasks/{id}/audio?variant=        试听（支持 Range）
GET  /api/transcripts/{id}                 文稿 + 分段
GET  /api/transcripts/{id}/export?format=   md|txt|srt|json
GET/POST /api/transcripts/{id}/analyses    分析历史 / SSE 流式生成
GET/POST /api/transcripts/{id}/conversations
POST /api/conversations/{id}/messages/stream   SSE 流式对话
GET  /api/library?q=                       全文检索
GET  /api/library/stats
GET/PUT /api/settings                     运行时配置（Key 打码）
POST /api/settings/test-llm               连通性测试
GET/POST /api/llm-profiles                已保存的模型/API 档案
POST /api/llm-profiles/{id}/activate|test 切换 / 测试
DELETE /api/llm-profiles/{id}             删除档案
GET  /api/system/health|info|asr-models
```

SSE 事件格式：`data: {"delta": "..."}` / `{"error": "..."}` / `{"done": true, ...}`。

## 7. 前端交互

- **新建解析**：链接 → 读取（只 probe）→ 封面/标题/时长确认 → 展开处理选项 → 开始。
- **任务页**：阶段进度条、事件日志、音频播放器（增强/原始切换）、字幕式时间戳跳转。
- **AI 解析**：内容审校 / 摘要 / 观点提炼 / 自定义；流式输出；历史分析持久化。
- **讨论**：按文稿多会话；流式回复；建议问题；自动保存。
- **资料库**：跨文稿全文检索（标题 / UP 主 / 正文），统计磁盘与时长。
- **设置**：LLM、ASR、音频默认值、存储信息、开源组件链接。

## 8. 性能（参考实测，CPU-only 测试环境）

| 项目 | 实测 |
| --- | --- |
| 2 分钟 Bilibili 音频下载 | ~2s |
| Demucs htdemucs 分离 30s 音频（含模型加载） | ~15s（≈2× 实时） |
| sherpa-onnx 说话人分离 127s 音频 | ~15s（≈8× 实时） |
| faster-whisper small int8 | 127s 音频 ≈ 21s（≈6× 实时） |
| faster-whisper large-v3 int8 | 127s 音频 ≈ 88s（≈1.4× 实时，约占 3–4GB 内存） |
| 说话人模型首次下载 | 约 46MB / 12s（视网络） |

> GPU 不可用时全部走 CPU；有 NVIDIA GPU 时可将 ASR `device=cuda`、`compute_type=float16`。

### 8.1 内存 / 显存保护（防止把桌面卡死）

| 条件 | 行为 |
| --- | --- |
| CPU 模式，可用内存 < 10GB，选择 large 系列 | 自动降级为 `small`，并在任务日志中说明 |
| CPU 模式，可用内存 < 5GB，选择 medium | 自动降级为 `small` |
| 任意模式，可用内存 < 3GB | 跳过 Demucs 人声分离，保留原音轨继续 |
| CUDA 模式，显存 < 12GB，选择 large 系列 | 自动降级为 `medium`（<7GB 时为 `small`） |
| CUDA 不可用 | 自动回退 CPU + int8 |

large-v3 在 CPU 上约占 3–4GB 内存；在内存较小的笔记本上同时开浏览器、Demucs 与 large-v3 可能触发 OOM。默认 `small`，需要更高准确率时再按机器内存选择 `medium` / `large-v3`。

## 9. 已知限制

1. 超长文稿（>9 万字符）分析时会做头尾裁剪；讨论则用关键词检索选取相关片段。
2. 说话人分离是离线聚类，人数自动估计可能偏差；可在任务选项指定人数。
3. Bilibili 高码率/会员/登录内容可能需要 `cookies.txt`（`VM_COOKIES_FILE`）。
4. ASR 对同音字、专有名词仍会出错；提示词要求 AI 区分“识别错误”和“观点错误”。
5. 无内置用户系统，默认仅监听 `127.0.0.1`；不要直接暴露到公网。

## 10. 后续可做

- WhisperX 强制对齐提升词级时间戳
- 文稿向量化 + RAG（可接入 bge-m3）用于超长视频讨论
- 说话人命名/跨视频声纹识别
- 视频截图与图文笔记
- 任务暂停/恢复、模型下载进度条
- 多用户与鉴权
