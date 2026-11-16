import { useEffect, useRef, useState } from "react";
import { api, Conversation, ConversationDetail, Message, stream } from "../api";
import { formatDate } from "../utils";
import { ConfirmDialog, Icon, Markdown, Spinner, useToast } from "./ui";

const SUGGESTIONS = [
  "这个视频的核心观点是什么？",
  "有哪些事实性错误或存疑之处？",
  "作者遗漏了哪些重要视角？",
  "用 5 句话总结这个视频",
];

export default function ChatPanel({ transcriptId }: { transcriptId: string }) {
  const toast = useToast();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [streamingText, setStreamingText] = useState("");
  const [reasoningText, setReasoningText] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Conversation | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const loadList = async (selectId?: string) => {
    const items = await api.conversations(transcriptId);
    setConversations(items);
    return items;
  };

  useEffect(() => {
    let active = true;
    setLoading(true);
    setMessages([]);
    setActiveId(null);
    setStreamingText("");
    setReasoningText("");
    controllerRef.current?.abort();
    loadList()
      .then(async (items) => {
        if (!active) return;
        if (items.length > 0) {
          const id = items[0].id;
          setActiveId(id);
          const detail = await api.conversation(id);
          if (active) setMessages(detail.messages);
        }
      })
      .catch(() => undefined)
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
      controllerRef.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transcriptId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, streamingText]);

  useEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(150, node.scrollHeight)}px`;
  }, [draft]);

  const ensureConversation = async (): Promise<string> => {
    if (activeId) return activeId;
    const conv = await api.createConversation(transcriptId);
    setConversations((prev) => [conv, ...prev]);
    setActiveId(conv.id);
    setMessages([]);
    return conv.id;
  };

  const switchConversation = async (id: string) => {
    if (busy) {
      toast.push("请先停止当前回复", "info");
      return;
    }
    if (id === activeId) return;
    setActiveId(id);
    setMessages([]);
    setError("");
    setStreamingText("");
    setReasoningText("");
    try {
      const detail = await api.conversation(id);
      setMessages(detail.messages);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载讨论失败");
    }
  };

  const sendText = async (raw?: string) => {
    const text = (raw ?? draft).trim();
    if (!text || busy) return;
    setError("");
    setDraft("");
    setBusy(true);
    setStreamingText("");
    setReasoningText("");

    let conversationId = activeId;
    try {
      conversationId = await ensureConversation();
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建讨论失败");
      setBusy(false);
      return;
    }

    const tempId = -Date.now();
    const optimistic: Message = {
      id: tempId,
      role: "user",
      content: text,
      model: "",
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimistic]);

    const controller = new AbortController();
    controllerRef.current = controller;
    let assistantText = "";

    try {
      await stream.chat(conversationId, text, {
        signal: controller.signal,
        onReasoning: (text) => setReasoningText((prev) => prev + text),
        onDelta: (delta) => {
          assistantText += delta;
          setStreamingText((prev) => prev + delta);
        },
        onEvent: (event) => {
          if (event.user_message) {
            const actual = event.user_message as Message;
            setMessages((prev) => prev.map((item) => (item.id === tempId ? actual : item)));
          }
          if (typeof event.error === "string") {
            setError(event.error);
          }
          if (event.done && event.message) {
            const message = event.message as Message;
            setMessages((prev) => [...prev, message]);
            setStreamingText("");
            setReasoningText("");
          }
        },
      });
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setError(err instanceof Error ? err.message : "发送失败");
      }
    } finally {
      setBusy(false);
      controllerRef.current = null;
      if (assistantText) {
        loadList().catch(() => undefined);
      }
    }
  };

  const stop = () => {
    controllerRef.current?.abort();
    setBusy(false);
    setReasoningText("");
  };

  const newConversation = async () => {
    if (busy) return;
    try {
      const conv = await api.createConversation(transcriptId);
      setConversations((prev) => [conv, ...prev]);
      setActiveId(conv.id);
      setMessages([]);
      setError("");
      setStreamingText("");
      textareaRef.current?.focus();
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "创建失败", "error");
    }
  };

  const removeConversation = async () => {
    if (!deleteTarget) return;
    if (busy) {
      toast.push("请先停止当前回复", "info");
      setDeleteTarget(null);
      return;
    }
    try {
      await api.deleteConversation(deleteTarget.id);
      const rest = conversations.filter((item) => item.id !== deleteTarget.id);
      setConversations(rest);
      if (activeId === deleteTarget.id) {
        if (rest.length > 0) {
          setActiveId(rest[0].id);
          const detail = await api.conversation(rest[0].id);
          setMessages(detail.messages);
        } else {
          setActiveId(null);
          setMessages([]);
        }
      }
      toast.push("讨论已删除", "success");
    } catch (err) {
      toast.push(err instanceof Error ? err.message : "删除失败", "error");
    } finally {
      setDeleteTarget(null);
    }
  };

  if (loading) {
    return (
      <div className="loading-row">
        <Spinner size={18} /> 正在加载讨论…
      </div>
    );
  }

  return (
    <>
      <div className="chat-panel">
        <aside className="chat-side">
          <button className="btn ghost sm new-chat" onClick={newConversation} disabled={busy}>
            <Icon name="plus" size={13} />
            新讨论
          </button>
          <div className="conv-list">
            {conversations.length === 0 && (
              <div className="hint" style={{ padding: 8, fontSize: 10.5 }}>
                还没有讨论记录
              </div>
            )}
            {conversations.map((conv) => (
              <button
                key={conv.id}
                className={`conv-item ${activeId === conv.id ? "active" : ""}`}
                onClick={() => switchConversation(conv.id)}
              >
                <Icon name="message" size={12} />
                <span className="t">{conv.title || "新的讨论"}</span>
                <span
                  className="del"
                  role="button"
                  tabIndex={0}
                  onClick={(event) => {
                    event.stopPropagation();
                    setDeleteTarget(conv);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") setDeleteTarget(conv);
                  }}
                >
                  <Icon name="trash" size={11} />
                </span>
              </button>
            ))}
          </div>
        </aside>

        <div className="chat-main">
          <div className="chat-header">
            <Icon name="message" size={14} />
            <span>{conversations.find((item) => item.id === activeId)?.title || "与 AI 讨论这份文稿"}</span>
            <span className="spacer" />
            <span className="hint" style={{ fontSize: 10.5 }}>
              对话自动保存
            </span>
          </div>

          <div className="chat-messages">
            {messages.length === 0 && !streamingText && (
              <div className="chat-empty">
                <div className="empty-icon" style={{ margin: "0 auto 10px" }}>
                  <Icon name="message" size={22} />
                </div>
                <h3>基于转录文稿提问</h3>
                <p>
                  AI 会引用文稿中的时间戳回答，也会明确告诉你哪些内容视频里没有提到。讨论会永久保存在本地。
                </p>
                <div className="suggestions">
                  {SUGGESTIONS.map((item) => (
                    <button key={item} className="suggestion" onClick={() => sendText(item)}>
                      {item}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((message) => (
              <div
                key={message.id}
                className={`msg ${message.role === "user" ? "user" : "assistant"}`}
              >
                <div className="msg-avatar">
                  <Icon name={message.role === "user" ? "users" : "sparkles"} size={14} />
                </div>
                <div className="msg-bubble">
                  {message.role === "assistant" ? (
                    <Markdown>{message.content}</Markdown>
                  ) : (
                    message.content
                  )}
                  {message.role === "assistant" && message.model && (
                    <div className="hint" style={{ fontSize: 9.5, marginTop: 6, opacity: 0.7 }}>
                      {message.model} · {formatDate(message.created_at)}
                    </div>
                  )}
                </div>
              </div>
            ))}

            {busy && reasoningText && !streamingText && (
              <div className="msg assistant">
                <div className="msg-avatar">
                  <Icon name="brain" size={14} />
                </div>
                <div className="msg-bubble reasoning-bubble">
                  <div className="thinking-head">
                    <Spinner size={12} /> 模型正在思考 · 已输出 {reasoningText.length} 字
                  </div>
                  <details>
                    <summary>查看思考过程</summary>
                    <div className="reasoning-text">{reasoningText}</div>
                  </details>
                </div>
              </div>
            )}

            {streamingText && (
              <div className="msg assistant">
                <div className="msg-avatar">
                  <Icon name="sparkles" size={14} />
                </div>
                <div className="msg-bubble">
                  <Markdown>{streamingText}</Markdown>
                  <span className="streaming-caret" />
                </div>
              </div>
            )}
            {busy && !streamingText && (
              <div className="msg assistant">
                <div className="msg-avatar">
                  <Icon name="sparkles" size={14} />
                </div>
                <div className="msg-bubble">
                  <Spinner size={14} /> 正在思考…
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {error && (
            <div style={{ padding: "0 16px" }}>
              <div className="error-text">{error}</div>
            </div>
          )}

          <div className="chat-input-wrap">
            <div className="chat-input">
              <textarea
                ref={textareaRef}
                placeholder="向 AI 提问，Enter 发送，Shift+Enter 换行"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    sendText();
                  }
                }}
              />
              {busy ? (
                <button className="chat-send" onClick={stop} title="停止生成">
                  <Icon name="stop" size={14} />
                </button>
              ) : (
                <button
                  className="chat-send"
                  onClick={() => sendText()}
                  disabled={!draft.trim()}
                  title="发送"
                >
                  <Icon name="send" size={15} />
                </button>
              )}
            </div>
            <div className="chat-note">回答基于转录文稿，可能存在识别误差，重要事实请自行核对。</div>
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="删除这段讨论？"
        message="对话中的全部消息将被永久删除，无法恢复。"
        confirmText="删除"
        danger
        onConfirm={removeConversation}
        onCancel={() => setDeleteTarget(null)}
      />
    </>
  );
}
