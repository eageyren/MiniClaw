"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ensureSession, familyFetch } from "../../auth";

type Usage = { week: { daily: { date: string; conversations: number; assistant_turns: number; voice_turns: number }[]; conversation_count: number; assistant_turns: number; reminders: Record<string, number> }; daily_cost: { date: string; estimated_usd: number }[]; monthly_cost: { estimated_usd: number; input_tokens: number; output_tokens: number; stt_minutes: number; tts_characters: number }; monthly_cost_limit_usd: number; cost_alert: boolean };
type Delivery = { items: { title: string; status: string; triggered_at: string }[]; device_alerts: { label: string; status: string }[] };

export default function FamilyUsagePage() {
  const [usage, setUsage] = useState<Usage | null>(null); const [delivery, setDelivery] = useState<Delivery | null>(null); const [error, setError] = useState("");
  useEffect(() => { void ensureSession("family").then(async (ok) => {
    if (!ok) { setError("请先登录家属账号。"); return; }
    try {
      const [u, d] = await Promise.all([familyFetch("/api/family/usage"), familyFetch("/api/family/reminders/unacknowledged")]);
      const ub = await u.json(); const db = await d.json();
      if (!u.ok || !d.ok) throw new Error(ub.detail ?? db.detail ?? "使用情况暂时读取不到。");
      setUsage(ub); setDelivery(db);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "使用情况暂时读取不到。"); }
  }); }, []);
  const reminders = usage?.week.reminders;
  function downloadReport() {
    if (!usage) return;
    const rows = [
      ["日期", "对话数", "陪伴回复", "语音交流"],
      ...usage.week.daily.map((day) => [day.date, day.conversations, day.assistant_turns, day.voice_turns]),
      [],
      ["本月费用估算", "美元"],
      ["累计", usage.monthly_cost.estimated_usd.toFixed(2)],
      ["提醒结果", "数量"],
      ["已确认", reminders?.acknowledged ?? 0],
      ["未确认", reminders?.unacknowledged ?? 0],
      ["已升级通知", reminders?.escalated ?? 0],
      ["已忽略", reminders?.ignored ?? 0],
      ["投递异常", reminders?.failed ?? 0],
    ];
    const csv = "\uFEFF" + rows.map((row) => row.map((cell) => `"${String(cell ?? "").replaceAll('"', '""')}"`).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = `miniclaw-family-usage-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click(); URL.revokeObjectURL(url);
  }
  return <main className="family-usage"><header><Link href="/family">← 家庭设置</Link><span>MiniClaw 家庭使用情况</span></header><h1>这一周的陪伴情况</h1><p className="family-help">这里只展示家庭关心的使用概况和需要留意的提醒，不显示聊天内容。</p>{error && <p className="family-error">{error}</p>}
    {usage && <><button className="light-button" onClick={downloadReport}>导出本周使用情况</button><div className="usage-cards"><article><strong>{usage.week.conversation_count}</strong><span>开始过的对话</span></article><article><strong>{usage.week.assistant_turns}</strong><span>陪伴回复</span></article><article><strong>{reminders?.acknowledged ?? 0}</strong><span>已确认提醒</span></article><article><strong>{(reminders?.unacknowledged ?? 0) + (reminders?.escalated ?? 0)}</strong><span>还需要留意</span></article></div>
      <section><h2>每天的使用情况</h2><div className="usage-days">{usage.week.daily.map((day) => <article key={day.date}><time>{new Date(`${day.date}T12:00:00`).toLocaleDateString("zh-CN", { weekday: "short", month: "numeric", day: "numeric" })}</time><span>{day.conversations} 段对话</span><span>{day.voice_turns} 次语音交流</span></article>)}</div></section>
      <section><h2>提醒与设备</h2>{delivery?.device_alerts.map((alert, index) => <p className="family-error" key={index}>{alert.label}：{alert.status}。请协助检查网络和通知权限。</p>)}{delivery?.items.map((item, index) => <article className="family-card compact" key={`${item.triggered_at}-${index}`}><div><strong>{item.title}</strong><p>{new Date(item.triggered_at).toLocaleString("zh-CN")}</p></div><span className="status-pill">{item.status === "escalated" ? "已升级通知" : "尚未确认"}</span></article>)}{!delivery?.items.length && !delivery?.device_alerts.length && <p className="family-empty">目前没有未确认提醒或设备异常。</p>}</section>
      <section><h2>本月费用估算</h2><article className={`family-card cost-card ${usage.cost_alert ? "cost-alert" : ""}`}><strong>约 ${usage.monthly_cost.estimated_usd.toFixed(2)} 美元</strong><p>{usage.monthly_cost_limit_usd > 0 ? `单家庭月度上限：$${usage.monthly_cost_limit_usd.toFixed(2)}` : "尚未设置月度费用上限。"}</p>{usage.cost_alert && <p role="alert">费用估算已达到设置的月度上限。</p>}</article><div className="usage-days">{usage.daily_cost.map((day) => <article key={day.date}><time>{day.date}</time><span>当天约 ${day.estimated_usd.toFixed(2)} 美元</span></article>)}</div></section></>}
  </main>;
}
