"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { elderFetch, ensureSession } from "../auth";

type AccessData = { family_access: { id: string; display_name: string; phone: string; created_at: string }[]; devices: { id: string; label: string; created_at: string; current: boolean }[] };

export default function AccessPage() {
  const [data, setData] = useState<AccessData>({ family_access: [], devices: [] });
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { const response = await elderFetch("/api/elder/access"); const body = await response.json(); if (!response.ok) throw new Error(body.detail ?? "暂时无法读取授权信息。"); setData(body); setError(""); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "暂时无法读取授权信息。"); }
  }, []);
  useEffect(() => { void ensureSession("elder").then((ok) => { if (ok) void load(); else setError("请先在长辈设备上完成配对。"); }); }, [load]);
  async function revoke(path: string, label: string) {
    if (!window.confirm(`确定撤销“${label}”的访问吗？撤销后会立即失效。`)) return;
    const response = await elderFetch(path, { method: "POST" });
    if (!response.ok) { const body = await response.json().catch(() => ({})); setError(body.detail ?? "撤销失败。"); return; }
    await load();
  }
  return <main className="memory-page"><header><Link href="/">← 返回陪伴</Link><strong>我的授权</strong></header><h1>谁能看我的资料</h1><p className="memory-intro">只有这里列出的家属可以查看已授权的家庭资料和共享记忆。您可以随时取消授权。陌生设备也可以在这里解绑。</p>{error && <p className="memory-error" role="alert">{error}</p>}
    <section><h2>已授权的家属</h2>{data.family_access.map((item) => <article className="family-card compact" key={item.id}><div><strong>{item.display_name}</strong><p>{item.phone}</p><small>绑定于 {new Date(item.created_at).toLocaleString("zh-CN")}</small></div><button className="light-button danger-button" onClick={() => void revoke(`/api/elder/access/${item.id}/revoke`, item.display_name)}>取消授权</button></article>)}{!data.family_access.length && <p className="memory-empty">目前没有绑定家属。</p>}</section>
    <section><h2>已配对的长辈设备</h2>{data.devices.map((item) => <article className="family-card compact" key={item.id}><div><strong>{item.label}{item.current ? "（当前设备）" : ""}</strong><p>配对于 {new Date(item.created_at).toLocaleString("zh-CN")}</p></div><button className="light-button danger-button" onClick={() => void revoke(`/api/elder/devices/${item.id}/revoke`, item.label)}>解绑设备</button></article>)}</section>
  </main>;
}
