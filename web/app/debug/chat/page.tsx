"use client";
import Link from "next/link";
import { FormEvent, useEffect, useRef, useState } from "react";
import { elderFetch, ensureSession, storeSession } from "../../auth";

type Line = { role: "user" | "assistant"; content: string };

export default function DevelopmentTextChat() {
  const [ready, setReady] = useState(false);
  const [conversationId, setConversationId] = useState("");
  const [lines, setLines] = useState<Line[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<{ confirmation_id: string; recite_text: string } | null>(null);
  const abort = useRef<AbortController | null>(null);

  async function startDevelopmentSession() {
    const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/auth/dev-session`, { method: "POST" });
    if (!response.ok) return false;
    storeSession("elder", await response.json());
    return true;
  }

  useEffect(() => {
    if (process.env.NEXT_PUBLIC_APP_ENV !== "development") return;
    let active = true;
    void (async () => {
      if (!(await ensureSession("elder"))) {
        if (!(await startDevelopmentSession())) { setError("请先在长辈端完成设备配对。"); return; }
      }
      let list = await elderFetch("/api/conversations");
      if (list.status === 401 && await startDevelopmentSession()) list = await elderFetch("/api/conversations");
      if (!list.ok) throw new Error("暂时读取不到对话。");
      const conversations = await list.json();
      const row = conversations[0] ?? await (await elderFetch("/api/conversations", { method: "POST" })).json();
      if (!active) return;
      setConversationId(row.id);
      const history = await elderFetch(`/api/conversations/${row.id}/messages`);
      if (history.ok && active) setLines((await history.json()).map((item: Line) => ({ role: item.role, content: item.content })));
      setReady(true);
    })().catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : "文本调试页暂时不可用。"); });
    return () => { active = false; abort.current?.abort(); };
  }, []);

  async function decide(accepted: boolean) {
    if (!pending) return;
    try {
      const response = await elderFetch(`/api/confirmations/${pending.confirmation_id}/decision`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accepted, trigger: "button" }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail ?? "这次选择没有送到。");
      if (result.status === "confirmation_required") {
        setPending({ confirmation_id: result.confirmation_id, recite_text: result.recite_text });
      } else {
        setPending(null);
        setLines((old) => [...old, { role: "assistant", content: result.answer ?? "这次确认已处理。" }]);
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : "这次选择没有送到。"); }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || !conversationId || busy || pending) return;
    setDraft(""); setError(""); setBusy(true);
    setLines((old) => [...old, { role: "user", content: text }, { role: "assistant", content: "" }]);
    abort.current = new AbortController();
    try {
      const response = await elderFetch(`/api/conversations/${conversationId}/chat`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: text }), signal: abort.current.signal,
      });
      if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail ?? "网络不太好，请重试。"); }
      if (!response.body) throw new Error("暂时收不到回复，请稍后再试。");
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
      const handleFrame = (frame: string) => {
        const data = frame.split("\n").find((line) => line.startsWith("data: "))?.slice(6);
        if (!data) return;
        const item = JSON.parse(data);
        if (item.type === "token") setLines((old) => old.map((line, index) => index === old.length - 1 ? { ...line, content: line.content + item.content } : line));
        if (item.type === "confirmation") {
          setLines((old) => old.map((line, index) => index === old.length - 1 ? { ...line, content: item.content.recite_text } : line));
          setPending({ confirmation_id: item.content.confirmation_id, recite_text: item.content.recite_text });
        }
        if (item.type === "error") setError(item.content);
      };
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        const frames = buffer.split("\n\n"); buffer = frames.pop() ?? "";
        frames.forEach(handleFrame);
        if (done) break;
      }
      if (buffer.trim()) handleFrame(buffer);
    } catch (cause) {
      if (!(cause instanceof DOMException && cause.name === "AbortError")) setError(cause instanceof Error ? cause.message : "网络不太好，请重试。");
    } finally { setBusy(false); abort.current = null; }
  }

  if (process.env.NEXT_PUBLIC_APP_ENV !== "development") return <main className="debug-chat"><h1>此页面仅在开发模式开放</h1><Link href="/">返回长辈端</Link></main>;
  return <main className="debug-chat">
    <header><Link href="/">← 长辈端</Link><h1>开发用文本对话</h1><Link href="/debug">上下文调试</Link></header>
    <p>仅用于 S1 文本验收，不是长辈日常入口。文字记录会保存在当前家庭会话中。</p>
    <section className="debug-chat-lines" aria-live="polite">{lines.map((line, index) => <article key={`${index}-${line.role}`} className={line.role}><strong>{line.role === "user" ? "您" : "MiniClaw"}</strong><p>{line.content || (busy && index === lines.length - 1 ? "正在回复…" : "")}</p></article>)}</section>
    {pending && <section className="confirmation-card"><h2>请确认这项操作</h2><p>{pending.recite_text}</p><div className="confirmation-buttons"><button className="accept" onClick={() => void decide(true)}>是这样的</button><button className="reject" onClick={() => void decide(false)}>不对，我重说</button></div></section>}
    {error && <p className="family-error" role="alert">{error}</p>}
    <form onSubmit={(event) => void submit(event)}><textarea value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="输入一条测试消息" disabled={!ready || busy || Boolean(pending)} maxLength={4000}/><button className="primary-button" disabled={!ready || busy || Boolean(pending) || !draft.trim()}>发送</button></form>
  </main>;
}
