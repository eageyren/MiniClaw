"use client";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { enablePush, familyFetch, ensureSession, storeSession } from "../auth";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type RoleConfig = { role_key: string; name: string; relationship: string; address: string; catchphrase: string; topics: string[]; style: { average_sentence_chars: number; use_particles: boolean; pace: string }; forbidden_topics: string[]; tool_allowlist: string[]; self_intro: string; version: number };
type Fact = { id: string; fact_type: string; content: string; authorized: boolean };
type Reminder = { id: string; title: string; due_at: string; recurrence: string | null; status: string };
type SharedMemory = { id: string; content: string; created_at: string; source_conversation_id: string; source_text: string };
type Audit = { actor: string; entity_type: string; action: string; before: unknown; after: unknown; created_at: string };
type PermissionRole = { role_key: string; role_id: string; role_name: string; tools: { name: string; description: string; enabled: boolean }[] };
type DeliveryReport = { items: { delivery_id: string; title: string; triggered_at: string; status: string }[]; device_alerts: { label: string; status: string }[] };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await familyFetch(path, init);
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail ?? "暂时无法完成，请稍后再试。"); }
  return response.json();
}

export default function FamilyPage() {
  const [roles, setRoles] = useState<RoleConfig[]>([]); const [facts, setFacts] = useState<Fact[]>([]); const [permissions, setPermissions] = useState<PermissionRole[]>([]);
  const [deliveryReport, setDeliveryReport] = useState<DeliveryReport>({ items: [], device_alerts: [] });
  const [reminders, setReminders] = useState<Reminder[]>([]); const [memories, setMemories] = useState<SharedMemory[]>([]); const [audit, setAudit] = useState<Audit[]>([]);
  const [factType, setFactType] = useState("称呼"); const [factContent, setFactContent] = useState(""); const [factAuthorized, setFactAuthorized] = useState(true);
  const [notice, setNotice] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const [authenticated, setAuthenticated] = useState(false); const [phone, setPhone] = useState(""); const [displayName, setDisplayName] = useState(""); const [otp, setOtp] = useState(""); const [pairingCode, setPairingCode] = useState(""); const [devOtp, setDevOtp] = useState(""); const [authBusy, setAuthBusy] = useState(false);
  const [notifyFamily, setNotifyFamily] = useState(false);

  const load = useCallback(async () => {
    setBusy(true); setError("");
    try {
      const [r, f, rs, ms, a, p, d, push] = await Promise.all([
        request<{ items: RoleConfig[] }>("/api/family/roles"),
        request<{ items: Fact[] }>("/api/family/facts"), request<{ items: Reminder[] }>("/api/family/reminders"),
        request<{ items: SharedMemory[] }>("/api/family/memories"), request<{ items: Audit[] }>("/api/family/audit"), request<{ items: PermissionRole[] }>("/api/access/tool-permissions"), request<DeliveryReport>("/api/family/reminders/unacknowledged"), request<{ enabled: boolean; notify_enabled: boolean }>("/api/push/subscriptions/me"),
      ]);
      setRoles(r.items); setFacts(f.items); setReminders(rs.items); setMemories(ms.items); setAudit(a.items); setPermissions(p.items); setDeliveryReport(d); setNotifyFamily(push.notify_enabled);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "家庭资料暂时读取不到。"); }
    finally { setBusy(false); }
  }, []);
  useEffect(() => { void ensureSession("family").then((ok) => { setAuthenticated(ok); if (ok) void load(); }); }, [load]);
  useEffect(() => {
    if (!authenticated) return;
    const refreshDeliveries = async () => { try { setDeliveryReport(await request<DeliveryReport>("/api/family/reminders/unacknowledged")); } catch { /* manual refresh remains available */ } };
    const timer = setInterval(() => void refreshDeliveries(), 30000); return () => clearInterval(timer);
  }, [authenticated]);
  useEffect(() => { const expired = (event: Event) => { if ((event as CustomEvent).detail === "family") setAuthenticated(false); }; window.addEventListener("miniclaw.session.expired", expired); return () => window.removeEventListener("miniclaw.session.expired", expired); }, []);
  async function sendOtp() {
    try { const response = await fetch(`${API}/api/auth/otp`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ phone_number: phone }) }); const result = await response.json(); if (!response.ok) throw new Error(result.detail ?? "验证码发送失败"); setDevOtp(result.development_code ?? ""); setNotice("验证码已发送，请查看手机短信。"); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "验证码发送失败"); }
  }
  async function authenticate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setAuthBusy(true); setError("");
    try {
      const joining = Boolean(pairingCode.trim()); const path = joining ? "/api/auth/family/pair" : "/api/auth/family/login";
      const body = joining ? { phone_number: phone, otp, pairing_code: pairingCode, display_name: displayName || "家属" } : { phone_number: phone, otp };
      const response = await fetch(`${API}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); const result = await response.json();
      if (!response.ok) throw new Error(result.detail ?? "登录失败"); storeSession("family", result); setAuthenticated(true); await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "登录失败"); }
    finally { setAuthBusy(false); }
  }

  async function saveRole(event: FormEvent<HTMLFormElement>, role: RoleConfig) {
    event.preventDefault(); const data = new FormData(event.currentTarget);
    const { role_key: _roleKey, version: _version, ...baseConfig } = role;
    const config = { ...baseConfig, name: String(data.get("name")), address: String(data.get("address")), catchphrase: String(data.get("catchphrase")), topics: String(data.get("topics")).split(/[，,]/).map((x) => x.trim()).filter(Boolean), self_intro: String(data.get("self_intro")), style: { ...role.style, average_sentence_chars: Number(data.get("average_sentence_chars")), pace: String(data.get("pace")) } };
    try { await request(`/api/family/roles/${role.role_key}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_version: role.version, config }) }); setNotice(`${role.name}角色设置已保存。`); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "保存失败。"); }
  }
  async function setPermission(roleKey: string, toolName: string, enabled: boolean) {
    try { await request("/api/access/tool-permissions", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role_key: roleKey, tool_name: toolName, enabled }) }); setPermissions((old) => old.map((role) => role.role_key === roleKey ? { ...role, tools: role.tools.map((tool) => tool.name === toolName ? { ...tool, enabled } : tool) } : role)); setNotice("工具权限已更新。"); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "权限更新失败。"); }
  }
  async function enableFamilyPush() {
    try { await enablePush("family", notifyFamily); setNotice(notifyFamily ? "家属提醒通知已开启。" : "这台家属设备已订阅推送；提醒升级通知目前关闭。可勾选授权后再开启。 "); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "推送设置失败。"); }
  }
  async function addFact(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!factContent.trim()) return;
    try { await request("/api/family/facts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fact_type: factType, content: factContent, authorized: factAuthorized }) }); setFactContent(""); setNotice("家庭资料已保存。"); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "保存失败。"); }
  }
  async function deleteFact(id: string) {
    if (!window.confirm("确定删除这条家庭资料吗？")) return;
    try { await request(`/api/family/facts/${id}`, { method: "DELETE" }); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "删除失败。"); }
  }
  async function editFact(fact: Fact) {
    const content = window.prompt("修改这条家庭资料", fact.content);
    if (content === null || !content.trim()) return;
    try { await request(`/api/family/facts/${fact.id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fact_type: fact.fact_type, content, authorized: fact.authorized }) }); setNotice("家庭资料已更新。"); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "修改失败。"); }
  }
  async function deleteMemory(id: string) {
    if (!window.confirm("确定删除这条共享记忆吗？")) return;
    try { await request(`/api/family/memories/${id}`, { method: "DELETE" }); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "删除失败。"); }
  }
  async function revokeShared() {
    if (!window.confirm("撤回后，所有角色都不能再使用共享记忆。继续吗？") || !window.confirm("请再确认一次：撤回家庭共享记忆授权？")) return;
    try { await request("/api/family/memories/revoke", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmation_phrase: "确认撤回" }) }); setNotice("家庭共享记忆授权已撤回。"); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "撤回失败。"); }
  }
  async function deleteAll() {
    if (!window.confirm("这会删除聊天记录、提醒和家庭资料。此操作无法撤销。继续吗？") || !window.confirm("最后确认：要清除这个家庭的数据吗？")) return;
    try { await request("/api/family/data/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmation_phrase: "确认删除" }) }); setNotice("家庭数据已清除。"); await load(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "删除失败。"); }
  }
  async function exportData() {
    try { const response = await familyFetch("/api/family/export"); if (!response.ok) throw new Error("导出失败，请稍后再试。"); const url = URL.createObjectURL(await response.blob()); const a = document.createElement("a"); a.href = url; a.download = "miniclaw-family-export.json"; a.click(); URL.revokeObjectURL(url); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "导出失败。"); }
  }
  function preview(role: RoleConfig) { if (!("speechSynthesis" in window)) { setNotice("此设备暂时不能试听语音。"); return; } const line = new SpeechSynthesisUtterance(`${role.address}，${role.catchphrase}`); line.lang = "zh-CN"; speechSynthesis.speak(line); }

  if (!authenticated) return <main className="memory-page"><header><Link href="/">← 长辈端</Link><strong>MiniClaw 家属登录</strong></header><h1>家属登录或绑定</h1><p className="memory-intro">输入手机号接收验证码。首次绑定时，再输入长辈设备上显示的 6 位配对码。</p><form className="family-card family-form-grid" onSubmit={(e) => void authenticate(e)}><label>手机号<input value={phone} onChange={(e) => setPhone(e.target.value)} required inputMode="tel"/></label><label>姓名（首次绑定填写）<input value={displayName} onChange={(e) => setDisplayName(e.target.value)}/></label><label>短信验证码<input value={otp} onChange={(e) => setOtp(e.target.value)} required maxLength={6} inputMode="numeric"/></label><label>长辈配对码（首次绑定填写）<input value={pairingCode} onChange={(e) => setPairingCode(e.target.value)} maxLength={6} inputMode="numeric"/></label><button type="button" className="light-button" onClick={() => void sendOtp()}>发送验证码</button><button className="primary-button" disabled={authBusy}>登录 / 绑定</button>{devOtp && <p className="family-help">开发模式验证码：{devOtp}</p>}{error && <p className="family-error wide">{error}</p>}</form></main>;
  return <main className="family-shell"><aside className="family-nav"><Link href="/" className="family-home">← 老人端</Link><h1>家庭设置</h1><a href="#roles">角色配置</a><a href="#facts">家庭事实</a><a href="#permissions">工具权限</a><a href="#reminders">提醒查看</a><a href="#delivery">提醒送达</a><a href="#memories">共享记忆</a><a href="#data">数据与授权</a><a href="#audit">变更记录</a><Link href="/family/usage">使用情况</Link><small>家属管理端</small></aside><div className="family-content"><header><div><strong>MiniClaw 家属设置</strong><p>修改会立即生效，并留下变更记录。</p></div><button onClick={() => void load()} disabled={busy}>刷新</button></header>
    {notice && <p className="family-notice" role="status">{notice}</p>}{error && <p className="family-error" role="alert">{error}</p>}
    <section id="roles"><h2>角色配置</h2><p className="family-help">称呼、口头表达和话题保存后会在老人下次对话时生效。</p>{roles.map((role) => <form className="family-card" key={role.role_key} onSubmit={(e) => void saveRole(e, role)}><div className="family-card-heading"><h3>{role.name}（{role.relationship}）</h3><button type="button" className="light-button" onClick={() => preview(role)}>试听示例回复</button></div><div className="family-form-grid"><label>显示名称<input name="name" defaultValue={role.name}/></label><label>如何称呼长辈<input name="address" defaultValue={role.address}/></label><label>常用口头表达<input name="catchphrase" defaultValue={role.catchphrase}/></label><label>常聊话题（逗号分隔）<input name="topics" defaultValue={role.topics.join("，")}/></label><label>说话节奏<input name="pace" defaultValue={role.style.pace}/></label><label>平均句长<input type="number" min={4} max={80} name="average_sentence_chars" defaultValue={role.style.average_sentence_chars}/></label><label className="wide">自我介绍<textarea name="self_intro" defaultValue={role.self_intro}/></label></div><p className="family-help">已启用工具：{permissions.find((item) => item.role_key === role.role_key)?.tools.filter((tool) => tool.enabled).map((tool) => tool.name).join("、") || "暂无；请在下方工具权限中配置"}</p><button className="primary-button" type="submit">保存角色设置</button></form>)}</section>
    <section id="permissions"><h2>角色工具权限</h2><p className="family-help">关闭的工具不会传给模型，后端也会拒绝调用。写入操作仍然需要长辈逐次确认。</p>{permissions.map((role) => <div className="family-card" key={`permissions-${role.role_key}`}><h3>{role.role_name}</h3>{role.tools.map((tool) => <label key={tool.name} className="check-label"><input type="checkbox" checked={tool.enabled} disabled={busy} onChange={(event) => void setPermission(role.role_key, tool.name, event.target.checked)}/><span><strong>{tool.name}</strong><br/><small>{tool.description}</small></span></label>)}</div>)}</section>
    <section id="facts"><h2>家庭事实</h2><p className="family-help">仅授权的事实会提供给陪伴角色作为回答依据。</p><form className="family-card family-form-grid" onSubmit={(e) => void addFact(e)}><label>资料类型<input value={factType} onChange={(e) => setFactType(e.target.value)} maxLength={40}/></label><label className="wide">内容<textarea value={factContent} onChange={(e) => setFactContent(e.target.value)} maxLength={1000} required placeholder="例如：长辈喜欢被称为妈妈"/></label><label className="check-label"><input type="checkbox" checked={factAuthorized} onChange={(e) => setFactAuthorized(e.target.checked)}/>授权陪伴角色使用</label><button className="primary-button" type="submit">添加家庭资料</button></form><div className="family-card-list">{facts.map((fact) => <article className="family-card compact" key={fact.id}><div><strong>{fact.fact_type}</strong><p>{fact.content}</p><small>{fact.authorized ? "已授权使用" : "未授权"}</small></div><div className="family-actions"><button className="light-button" onClick={() => void editFact(fact)}>编辑</button><button className="light-button danger-button" onClick={() => void deleteFact(fact.id)}>删除</button></div></article>)}</div></section>
    <section id="reminders"><h2>提醒查看</h2><p className="family-help">此处只读，家属不能替长辈修改提醒。</p><div className="family-card-list">{reminders.map((r) => <article className="family-card compact" key={r.id}><div><strong>{r.title}</strong><p>{new Date(r.due_at).toLocaleString("zh-CN")} · {r.recurrence || "不重复"}</p></div><span className="status-pill">{r.status}</span></article>)}{!reminders.length && <p className="family-empty">暂时没有提醒。</p>}</div></section>
    <section id="delivery"><h2>未确认的提醒与送达</h2><p className="family-help">提醒 5 分钟后仍未确认会列在这里；15 分钟后系统会按您单独开启的授权推送给家属。推送失败的长辈设备会显示异常。</p><div className="family-card"><label className="check-label"><input type="checkbox" checked={notifyFamily} onChange={(event) => setNotifyFamily(event.target.checked)}/>允许向我的设备发送未确认提醒通知（默认关闭）</label><button className="primary-button" onClick={() => void enableFamilyPush()}>保存并开启此设备通知</button></div>{deliveryReport.device_alerts.map((alert, index) => <p className="family-error" key={index}>{alert.label}：{alert.status}，请协助检查网络或通知权限。</p>)}<div className="family-card-list">{deliveryReport.items.map((item) => <article className="family-card compact" key={item.delivery_id}><div><strong>{item.title}</strong><p>{new Date(item.triggered_at).toLocaleString("zh-CN")}</p></div><span className="status-pill">{item.status === "escalated" ? "已升级通知" : "尚未确认"}</span></article>)}{!deliveryReport.items.length && <p className="family-empty">目前没有未确认的提醒。</p>}</div></section>
    <section id="memories"><h2>共享记忆</h2><p className="family-help">这里只显示长辈明确授权全家共享的记忆。角色私有记忆和私人聊天不会出现在家属端。</p><div className="family-card-list">{memories.map((m) => <article className="family-card compact" key={m.id}><div><strong>{m.content}</strong><details><summary>查看来源</summary><blockquote>{m.source_text || "原话暂时不可用"}</blockquote><small>来源会话：{m.source_conversation_id}</small></details></div><button className="light-button danger-button" onClick={() => void deleteMemory(m.id)}>删除</button></article>)}{!memories.length && <p className="family-empty">没有已授权的共享记忆。</p>}</div><button className="light-button danger-button" onClick={() => void revokeShared()}>撤回全部共享记忆授权</button></section>
    <section id="data"><h2>数据与授权</h2><div className="family-card"><p>导出家庭共享资料、提醒和共享记忆；私人聊天和角色私有记忆不会包含在家属导出中。</p><div className="family-actions"><button className="light-button" onClick={() => void exportData()}>下载家庭数据</button><button className="light-button danger-button" onClick={() => void deleteAll()}>删除家庭数据</button></div></div></section>
    <section id="audit"><h2>变更记录</h2><div className="family-card-list">{audit.map((a, i) => <article className="family-card audit-row" key={`${a.created_at}-${i}`}><div><strong>{a.actor}</strong><p>{a.entity_type} · {a.action}</p></div><time>{new Date(a.created_at).toLocaleString("zh-CN")}</time></article>)}</div></section>
  </div></main>;
}
