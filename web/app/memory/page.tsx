"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { elderFetch } from "../auth";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type Memory = { id: string; content: string; scope: string; authorized: boolean; role_key: string | null; source_conversation_id: string; source_text: string; confidence: number; created_at: string; expires_at: string | null };

export default function MemoryPage() {
  const [items, setItems] = useState<Memory[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    try {
      const response = await elderFetch("/api/memories");
      if (!response.ok) throw new Error("暂时读取不到记忆，请稍后再试。");
      const data: { items: Memory[] } = await response.json(); setItems(data.items); setError("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "暂时读取不到记忆，请稍后再试。"); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function remove(id: string) {
    if (!window.confirm("要忘掉这条记忆吗？之后聊天时就不会再用到。")) return;
    try {
      const response = await elderFetch(`/api/memories/${id}`, { method: "DELETE" });
      if (!response.ok) throw new Error("这次没有删掉，请稍后再试。");
      setItems((old) => old.filter((item) => item.id !== id));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "这次没有删掉，请稍后再试。"); }
  }
  return <main className="memory-page"><header><Link href="/">← 回到聊天</Link><span>MiniClaw</span></header><h1>我的记忆</h1><p className="memory-intro">这里只保存您明确同意记下的偏好和称呼。每条都能查看来源，也可以随时删除。</p>
    {loading ? <p>正在读取…</p> : error ? <p className="memory-error" role="alert">{error}</p> : items.length === 0 ? <p className="memory-empty">现在还没有保存的记忆。</p> : <section className="memory-list">{items.map((item) => <article key={item.id}><div className="memory-title"><strong>{item.content}</strong><button onClick={() => void remove(item.id)}>忘掉这条</button></div><p className="memory-scope">{item.scope === "family_shared" ? item.authorized ? "家庭共享" : "家庭共享（授权已撤回）" : `仅${item.role_key === "granddaughter" ? "孙女" : "女儿"}角色使用`} · {new Date(item.created_at).toLocaleString("zh-CN")}</p><details><summary>这句话从哪里来</summary><blockquote>{item.source_text || "原话暂时不可用"}</blockquote><small>来源会话：{item.source_conversation_id}</small></details></article>)}</section>}
  </main>;
}
