"use client";
import { useEffect, useState } from "react";
import { elderFetch } from "../auth";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type Conversation = { id: string; title: string };
type Snapshot = { system_prompt: string; system_prompt_tokens: number; summary: string; summary_tokens: number; raw_messages: { role: string; content: string; tokens: number }[]; raw_tokens: number; tool_results: { name: string; result: unknown; queried_at: string; duration_ms: number }[]; tool_result_tokens: number; budget_tokens: number };

export default function ContextDebugPage() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { void elderFetch("/api/conversations").then((r) => r.json()).then((rows: Conversation[]) => { setConversations(rows); const requested = new URLSearchParams(window.location.search).get("conversation_id"); setConversationId(requested || rows[0]?.id || ""); }).catch(() => setError("无法读取会话列表。")); }, []);
  useEffect(() => { if (!conversationId) return; void elderFetch(`/api/debug/conversations/${conversationId}/context`).then(async (r) => { const data = await r.json(); if (!r.ok) throw new Error(data.detail || "调试接口不可用"); setSnapshot(data); setError(""); }).catch((e) => setError(e.message)); }, [conversationId]);
  return <main className="debug-page"><header><a href="/">← 返回 MiniClaw</a><span>仅供本地开发使用</span></header><h1>对话上下文</h1><p className="debug-note">查看下一轮调用会使用的角色提示词、滚动摘要和原文历史。生产环境此页面和 API 均关闭。</p><label>会话<select value={conversationId} onChange={(e) => setConversationId(e.target.value)}>{conversations.map((c) => <option key={c.id} value={c.id}>{c.title} · {c.id.slice(0, 8)}</option>)}</select></label>{error && <p className="debug-error">{error}</p>}{snapshot && <><div className="debug-cards"><div><strong>{snapshot.system_prompt_tokens}</strong><span>系统提示词 tokens</span></div><div><strong>{snapshot.summary_tokens}</strong><span>滚动摘要 tokens</span></div><div><strong>{snapshot.raw_tokens}</strong><span>保留原文 tokens</span></div><div><strong>{snapshot.tool_result_tokens}</strong><span>工具结果 tokens</span></div><div><strong>{snapshot.budget_tokens}</strong><span>上下文预算 tokens</span></div></div><section><h2>系统提示词</h2><pre>{snapshot.system_prompt}</pre></section><section><h2>滚动摘要</h2><pre>{snapshot.summary || "（尚未生成摘要）"}</pre></section><section><h2>保留的对话原文</h2>{snapshot.raw_messages.map((m, i) => <article key={`${i}-${m.role}`}><small>{m.role} · {m.tokens} tokens</small><p>{m.content}</p></article>)}</section><section><h2>工具结果</h2><p>{snapshot.tool_results.length ? "" : "尚无工具调用记录。"}</p>{snapshot.tool_results.map((tool, i) => <article key={i}><small>{tool.name} · {tool.queried_at} · {tool.duration_ms} ms</small><pre>{JSON.stringify(tool.result, null, 2)}</pre></article>)}</section></>}</main>;
}
