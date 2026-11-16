import { useEffect, useRef, useState } from "react";
import { Analysis, api, stream } from "../api";
import { formatDate, truncate } from "../utils";
import { ConfirmDialog, EmptyState, Icon, Markdown, Spinner, useToast } from "./ui";

const KINDS = [
  {
    value: "review",
    icon: "shield" as const,
    label: "内容审校",
    desc: "事实核查、逻辑漏洞、补充视角",
  },
  {
    value: "summary",
    icon: "file" as const,
    label: "结构化摘要",
    desc: "脉络、结论、关键数据",
  },
  {
    value: "keypoints",
    icon: "brain" as const,
    label: "观点提炼",
    desc: "核心观点与可信度标记",
  },
  {
    value: "custom",
    icon: "edit" as const,
    label: "自定义分析",
    desc: "按你的要求分析这份文稿",
  },
];

const KIND_TITLE: Record<string, string> = {
  review: "内容审校",
  summary: "结构化摘要",
  keypoints: "观点提炼",
  custom: "自定义分析",
};

export default function AnalysisPanel({ transcriptId }: { transcriptId: string }) {
  const toast = useToast();
  const [analyses, setAnalyses] = useState<Analysis[]>([]);
  const [current, setCurrent] = useState<Analysis | null>(null);
  const [kind, setKind] = useState("review");
  const [instructions, setInstructions] = useState("");
  const [streamingText, setStreamingText] = useState("");
  const [reasoningText, setReasoningText] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [deleteTarget, setDeleteTarget] = useState<Analysis | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setCurrent(null);
    setAnalyses([]);
    setReasoningText("");
    api
      .analyses(transcriptId)
      .then((items) => {
        if (!active) return;
        setAnalyses(items);
        if (items.length > 0) {
          setCurrent(items[0]);
          setKind(items[0].kind);
        }
      })
      .catch(() => undefined)
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
      controllerRef.current?.abort();
    };
  }, [transcriptId]);

  const generate = async (kindOverride?: string, instructionsOverride?: string) => {
    if (busy) return;
    const runKind = kindOverride || kind;
    const runInstructions = instructionsOverride ?? instructions;
    setBusy(true);
    setError("");
    setStreamingText("");
    setReasoningText("");
    setCurrent(null);
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      await stream.analysis(
        transcriptId,
        { kind: runKind, instructions: runKind === "custom" ? runInstructions : "" },
        {
          signal: controller.signal,
          onDelta: (text) => setStreamingText((prev) => prev + text),
          onReasoning: (text) => setReasoningText((prev) => prev + text),
          onEvent: (event) => {
            if (typeof event.error === "string") {
              setError(event.error);
            }
            if (event.done && event.analysis) {
              const analysis = event.analysis as Analysis;
              setAnalyses((prev) => [analysis, ...prev]);
              setCurrent(analysis);
              setKind(analysis.kind);
              setStreamingText("");
              setReasoningText("");
              toast.push("分析已生成并保存", "success");
            }
          },
        },
      );
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setError(err instanceof Error ? err.message : "生成失败");
      }
    } finally {
      setBusy(false);
      controllerRef.current = null;
    }
  };

  const chooseKind = (value: string) => {
    if (busy) return;
    setKind(value);
    setError("");
    const existing = analyses.find((item) => item.kind === value);
    if (existing) {
      setCurrent(existing);
      setStreamingText("");
      setReasoningText("");
      return;
    }
    setCurrent(null);
    setStreamingText("");
    // Clicking a kind should do what users expect: generate it right away.
    // Custom needs the instruction textarea, so it waits for the button.
    if (value !== "custom") {
      void generate(value);
    }
  };

  useEffect(() => {
    if (!busy) {
      setElapsed(0);
      return;
    }
    setElapsed(0);
    const timer = window.setInterval(() => setElapsed((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);

  const stop = () => {
    controllerRef.current?.abort();
    setBusy(false);
    setReasoningText("");
  };

  const remove = async () => {
    if (!deleteTarget) return;
    if (busy) {
      toast.push("请先停止当前分析", "info");
      setDeleteTarget(null);
      return;
    }
    try {
      await api.deleteAnalysis(deleteTarget.id);
      setAnalyses((prev) => prev.filter((item) => item.id !== deleteTarget.id));
      if (current?.id === deleteTarget.id) setCurrent(null);
      toast.push("分析记录已删除", "success");
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "删除失败", "error");
    } finally {
      setDeleteTarget(null);
    }
  };

  const copy = async () => {
    const text = current?.content || streamingText;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      toast.push("分析内容已复制", "success");
    } catch {
      toast.push("复制失败", "error");
    }
  };

  const content = busy ? streamingText : current?.content || streamingText;
  const selectedKind = KINDS.find((item) => item.value === kind) || KINDS[0];

  if (loading) {
    return (
      <div className="loading-row">
        <Spinner size={18} /> 正在加载分析记录…
      </div>
    );
  }

  return (
    <>
      <div className="analysis-panel">
        <aside className="analysis-side">
          <div className="analysis-kinds">
            {KINDS.map((item) => (
              <button
                key={item.value}
                className={`analysis-kind ${kind === item.value ? "active" : ""}`}
                onClick={() => chooseKind(item.value)}
                disabled={busy}
              >
                <span className="kind-icon">
                  <Icon name={item.icon} size={15} />
                </span>
                <span>
                  <b>{item.label}</b>
                  <small>{item.desc}</small>
                </span>
              </button>
            ))}
          </div>
          <p className="hint" style={{ fontSize: 10.5, lineHeight: 1.5 }}>
            点击类型会直接生成；已有同类结果则打开历史记录。
          </p>
          {analyses.length > 0 && (
            <div className="analysis-history">
              <h4>历史分析</h4>
              {analyses.map((item) => (
                <button
                  key={item.id}
                  className={`analysis-history-item ${current?.id === item.id ? "active" : ""}`}
                  onClick={() => {
                    if (busy) return;
                    setCurrent(item);
                    setKind(item.kind);
                    setStreamingText("");
                  }}
                >
                  <Icon name="file" size={12} />
                  <span className="title">{item.title || KIND_TITLE[item.kind] || "分析"}</span>
                  <span className="date">{formatDate(item.created_at)}</span>
                  <span
                    className="del"
                    role="button"
                    tabIndex={0}
                    onClick={(event) => {
                      event.stopPropagation();
                      setDeleteTarget(item);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") setDeleteTarget(item);
                    }}
                  >
                    <Icon name="trash" size={11} />
                  </span>
                </button>
              ))}
            </div>
          )}
        </aside>

        <div className="analysis-main">
          {kind === "custom" && (
            <div className="analysis-custom">
              <textarea
                className="input"
                placeholder="描述你希望 AI 如何分析，例如：只关注视频中的经济学论点，找出数据引用是否可靠，并补充反例。"
                value={instructions}
                onChange={(event) => setInstructions(event.target.value)}
                disabled={busy}
              />
            </div>
          )}

          <div className="analysis-toolbar">
            <span className="title">{current?.title || selectedKind.label}</span>
            {current && (
              <span className="hint" style={{ fontSize: 10.5 }}>
                {formatDate(current.created_at)} · {current.model}
              </span>
            )}
            <span className="spacer" />
            {content && (
              <button className="btn ghost sm" onClick={copy}>
                <Icon name="copy" size={13} />
                复制
              </button>
            )}
            {busy ? (
              <button className="btn ghost sm" onClick={stop}>
                <Icon name="stop" size={12} />
                停止
              </button>
            ) : (
              <button className="btn primary sm" onClick={() => generate()}>
                <Icon name="sparkles" size={14} />
                {current || streamingText ? "重新生成" : `生成${selectedKind.label}`}
              </button>
            )}
          </div>

          {error && (
            <div className="error-panel">
              <Icon name="alert" size={16} />
              <div>
                <h4>分析失败</h4>
                <div>{error}</div>
              </div>
            </div>
          )}

          {busy && reasoningText && (
            <details className="reasoning-box" open={!content}>
              <summary>
                <Spinner size={12} /> 模型正在思考 · 已等待 {elapsed}s · 思考 {reasoningText.length} 字
              </summary>
              <div className="reasoning-text">{reasoningText}</div>
            </details>
          )}

          <div className={`analysis-output ${content ? "" : "empty"}`}>
            {content ? (
              <>
                <Markdown>{content}</Markdown>
                {busy && <span className="streaming-caret" />}
              </>
            ) : busy ? (
              <div className="analysis-placeholder">
                <Spinner size={22} />
                <h3 style={{ marginTop: 10 }}>
                  {reasoningText ? `模型正在思考「${selectedKind.label}」…` : `正在生成「${selectedKind.label}」…`}
                </h3>
                <p>
                  {reasoningText
                    ? `这是推理型模型的思考过程，已等待 ${elapsed}s；思考结束后会开始输出正式分析，可以展开上方查看。`
                    : `AI 正在通读文稿 / 深度思考，已等待 ${elapsed}s。推理型模型通常需要 30–90 秒才会有第一段正式输出，请不要关闭页面。`}
                </p>
              </div>
            ) : (
              <div className="analysis-placeholder">
                <h3>生成「{selectedKind.label}」</h3>
                <p>
                  {kind === "custom"
                    ? "在上方写下你的分析要求，然后点击生成；AI 会严格基于转录文稿回答。"
                    : "点击下方按钮，AI 会通读全文并生成对应分析；结果会自动保存到历史记录。"}
                </p>
                {kind === "custom" ? (
                  <button
                    className="btn primary"
                    style={{ marginTop: 16 }}
                    onClick={() => generate()}
                    disabled={busy}
                  >
                    <Icon name="sparkles" size={15} />
                    生成自定义分析
                  </button>
                ) : (
                  <button
                    className="btn primary"
                    style={{ marginTop: 16 }}
                    onClick={() => generate()}
                    disabled={busy}
                  >
                    <Icon name="sparkles" size={15} />
                    生成{selectedKind.label}
                  </button>
                )}
                <div className="tips">
                  <div>
                    <Icon name="check" size={13} />
                    事实核查会标注可信 / 存疑 / 可能错误 / 无法核实
                  </div>
                  <div>
                    <Icon name="check" size={13} />
                    引用时间戳，方便回到原视频核对
                  </div>
                  <div>
                    <Icon name="check" size={13} />
                    结果自动保存，可随时回看或删除
                  </div>
                  <div>
                    <Icon name="check" size={13} />
                    长文稿会自动裁剪上下文，请留意提示
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="删除这条分析？"
        message={`「${truncate(deleteTarget?.title || "分析记录", 40)}」将被永久删除。`}
        confirmText="删除"
        danger
        onConfirm={remove}
        onCancel={() => setDeleteTarget(null)}
      />
    </>
  );
}
