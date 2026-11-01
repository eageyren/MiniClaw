"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { elderFetch } from "../../auth";

type Trace = { request_id: string; route: string; status_code: number | null; started_at: string; finished_at: string | null; duration_ms: number | null; spans: { name: string; duration_ms: number; outcome: string; metadata: Record<string, unknown>; created_at: string }[] };
export default function TracePage() {
  const [trace, setTrace] = useState<Trace | null>(null); const [error, setError] = useState("");
  useEffect(() => { const id = localStorage.getItem("miniclaw.last_request_id"); if (!id) { setError("还没有请求记录。先进行一次语音交流，再回到这里查看。"); return; }
    void elderFetch(`/api/family/traces/${id}`).then(async (response) => { const body = await response.json(); if (!response.ok) throw new Error(body.detail ?? "请求记录读取失败。"); setTrace(body); }).catch((cause) => setError(cause instanceof Error ? cause.message : "请求记录读取失败。"));
  }, []);
  return <main className="debug-page"><header><Link href="/">← 返回 MiniClaw</Link><span>本地请求追踪</span></header><h1>一次请求的处理时间线</h1><p className="debug-note">仅记录阶段、耗时和工具名称，不保存语音原文或密钥。</p>{error && <p className="debug-error">{error}</p>}{trace && <><p>请求 ID：<code>{trace.request_id}</code></p><p>{trace.route} · HTTP {trace.status_code} · {trace.duration_ms ?? 0} ms</p><ol className="trace-timeline">{trace.spans.map((span, index) => <li key={`${span.created_at}-${index}`}><strong>{span.name}</strong><span>{span.duration_ms} ms · {span.outcome}</span><small>{Object.entries(span.metadata).map(([key, value]) => `${key}: ${String(value)}`).join(" · ")}</small></li>)}</ol></>}</main>;
}
