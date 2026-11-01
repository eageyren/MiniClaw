"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { elderFetch, enablePush, ensureSession, pollElderPairing, startElderPairing } from "./auth";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type Conversation = { id: string; title: string; role_key?: string | null };
type Role = { role_key: string; name: string; self_intro: string };
type Line = { role: "user" | "assistant"; text: string };
type ReminderDelivery = { delivery_id: string; reminder_id: string; title: string; triggered_at: string; status: string; is_catch_up: boolean };
type VoiceResult = { status: string; message?: string; detail?: string; metric_id?: string; transcript?: string; answer?: string; audio_base64?: string; audio_content_type?: string; confirmation_id?: string; step?: number; total_steps?: number; recite_text?: string; expires_at?: string; high_risk?: boolean };
type Status = "ready" | "listening" | "processing" | "speaking" | "error";

const LABEL: Record<Status, string> = { ready: "可以说话", listening: "正在听…再按一下结束", processing: "我听着呢，马上就好", speaking: "正在说话…", error: "遇到一点问题" };

export default function Home() {
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [roles, setRoles] = useState<Role[]>([]);
  const [selectedRole, setSelectedRole] = useState<string | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [status, setStatus] = useState<Status>("ready");
  const [message, setMessage] = useState("请先选择一位陪伴角色，听完自我介绍后就可以开始聊天。");
  const [busyText, setBusyText] = useState(false);
  const [lastAudio, setLastAudio] = useState<string | null>(null);
  const [metricId, setMetricId] = useState<string | null>(null);
  const [permissionDenied, setPermissionDenied] = useState(false);
  const [pendingConfirmation, setPendingConfirmation] = useState<VoiceResult | null>(null);
  const [confirmationBusy, setConfirmationBusy] = useState(false);
  const [familyChange, setFamilyChange] = useState<string | null>(null);
  const [paired, setPaired] = useState(false);
  const [offlineMode, setOfflineMode] = useState(false);
  const [recoveryMode, setRecoveryMode] = useState(false);
  const [pairingCode, setPairingCode] = useState("");
  const [pairingError, setPairingError] = useState("");
  const [deliveries, setDeliveries] = useState<ReminderDelivery[]>([]);
  const [pushStatus, setPushStatus] = useState("");
  const [installPrompt, setInstallPrompt] = useState<(() => Promise<void>) | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const audioContext = useRef<AudioContext | null>(null);
  const analyser = useRef<AnalyserNode | null>(null);
  const silenceFrame = useRef<number | null>(null);
  const chunks = useRef<Blob[]>([]);
  const pendingBlob = useRef<Blob | null>(null);
  const controller = useRef<AbortController | null>(null);
  const startedAt = useRef(0);
  const voiceCycleStartedAt = useRef<number | null>(null);
  const comfortTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const audioUrl = useRef<string | null>(null);
  const playerRef = useRef<HTMLAudioElement | null>(null);
  const cancelledRecording = useRef(false);
  const announcedDeliveries = useRef(new Set<string>());
  const repeatedDeliveries = useRef(new Set<string>());
  const telemetrySession = useRef("");

  const loadConversation = useCallback(async () => {
    try {
      const roleResponse = await elderFetch("/api/roles");
      if (!roleResponse.ok) throw new Error();
      setRoles(await roleResponse.json());
      const list = await elderFetch("/api/conversations");
      if (!list.ok) throw new Error();
      const rows: Conversation[] = await list.json();
      const chosen = rows[0] ?? await (await elderFetch("/api/conversations", { method: "POST" })).json();
      setConversation(chosen);
      setSelectedRole(chosen.role_key ?? null);
      const changesResponse = await elderFetch("/api/family/changes/latest");
      if (changesResponse.ok) { const change = await changesResponse.json(); if (change?.summary) setFamilyChange(change.summary); }
      const response = await elderFetch(`/api/conversations/${chosen.id}/messages`);
      if (response.ok) {
        const history = await response.json();
        setLines(history.map((item: { role: "user" | "assistant"; content: string }) => ({ role: item.role, text: item.content })));
      }
      const pendingResponse = await elderFetch(`/api/conversations/${chosen.id}/pending-confirmation`);
      if (pendingResponse.ok) {
        const pending: VoiceResult | null = await pendingResponse.json();
        if (pending?.confirmation_id) announceConfirmation({ ...pending, status: "confirmation_required" });
      }
    } catch { setStatus("error"); setMessage("暂时连接不上服务，请稍后再试。"); }
  }, []);

  function playAudio(audioBase64?: string, contentType = "audio/mpeg", onEnd?: () => void, spokenText?: string) {
    if (audioBase64) {
      const bytes = Uint8Array.from(atob(audioBase64), (char) => char.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: contentType }));
      if (audioUrl.current) URL.revokeObjectURL(audioUrl.current);
      audioUrl.current = url; setLastAudio(url);
      const player = new Audio(url); playerRef.current = player; setStatus("speaking");
      player.onended = () => onEnd?.();
      player.onerror = () => { setStatus("error"); setMessage("语音没能播放出来，请看屏幕上的内容。"); };
      void player.play().catch(() => { setStatus("ready"); setMessage("请看屏幕上的确认内容，再点一个按钮。"); });
      return;
    }
    if ("speechSynthesis" in window) {
      const utterance = new SpeechSynthesisUtterance(spokenText ?? pendingConfirmation?.recite_text ?? message); utterance.lang = "zh-CN";
      utterance.onend = () => onEnd?.(); speechSynthesis.speak(utterance); setStatus("speaking");
    } else onEnd?.();
  }

  function announceConfirmation(data: VoiceResult, persistLine = false) {
    setPendingConfirmation(data); setStatus("speaking"); setMessage(data.recite_text ?? "请听清楚后选择一个按钮。");
    if (persistLine && data.recite_text) setLines((old) => [...old, { role: "assistant", text: data.recite_text! }]);
    playAudio(data.audio_base64, data.audio_content_type, () => { setStatus("ready"); setMessage(data.recite_text ?? "请选一个按钮确认。"); }, data.recite_text);
  }

  async function decideReminder(accepted: boolean) {
    if (!pendingConfirmation?.confirmation_id || confirmationBusy) return;
    setConfirmationBusy(true); setStatus("processing"); setMessage("正在处理您的选择…");
    try {
      const response = await elderFetch(`/api/confirmations/${pendingConfirmation.confirmation_id}/decision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ accepted, trigger: "button" }) });
      const result: VoiceResult = await response.json();
      if (!response.ok) {
        if (response.status === 410) { setPendingConfirmation(null); const expired = "那咱们这次先不记，需要的时候再说。"; setStatus("ready"); setMessage(expired); playAudio(undefined, "audio/mpeg", undefined, expired); return; }
        throw new Error(result.detail ?? "网络不太好，选择还没送到，请再试一次。");
      }
      if (result.status === "confirmation_required") { announceConfirmation(result, true); return; }
      setPendingConfirmation(null);
      const answer = result.answer ?? "好的，这次没有记录。";
      setLines((old) => [...old, { role: "assistant", text: answer }]); setMessage(answer);
      playAudio(result.audio_base64, result.audio_content_type, () => { setStatus("ready"); setMessage(answer); }, answer);
    } catch (error) { setStatus("error"); setMessage(error instanceof Error ? error.message : "网络不太好，选择还没送到，请再试一次。"); }
    finally { setConfirmationBusy(false); }
  }

  async function resolveDelivery(deliveryId: string, action: "acknowledge" | "ignore") {
    const response = await elderFetch(`/api/reminders/deliveries/${deliveryId}/${action}`, { method: "POST" });
    if (!response.ok) { setMessage("网络不太好，提醒处理还没送到，请再试一次。"); return; }
    setDeliveries((old) => old.filter((item) => item.delivery_id !== deliveryId));
  }
  async function enableReminderPush() {
    try { await enablePush("elder"); setPushStatus("这台设备已开启提醒通知。"); }
    catch (error) { setPushStatus(error instanceof Error ? error.message : "暂时无法开启通知。"); }
  }
  async function installApp() {
    if (installPrompt) { await installPrompt(); setInstallPrompt(null); }
    else setPushStatus("请使用浏览器菜单选择“添加到主屏幕”或“安装应用”。");
  }
  async function simulateReminder() {
    const response = await elderFetch("/api/reminders/simulate-next", { method: "POST" });
    const result = await response.json();
    if (!response.ok) setMessage(result.detail ?? "暂时没有可以模拟触发的提醒。");
    else setMessage("已模拟触发提醒，请留意本页播报和系统通知。");
  }

  useEffect(() => {
    if ("serviceWorker" in navigator) void navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  }, []);
  useEffect(() => {
    let active = true;
    const check = async () => {
      try {
        const response = await fetch(`${API}/health`, { cache: "no-store" });
        if (active) { setOfflineMode(!response.ok); setRecoveryMode(response.status === 503); }
      } catch { if (active) setOfflineMode(true); }
    };
    void check();
    const timer = setInterval(() => void check(), 15000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  useEffect(() => {
    if (!pendingConfirmation?.expires_at) return;
    const timer = setInterval(() => {
      if (Date.now() >= new Date(pendingConfirmation.expires_at!).getTime()) {
        clearInterval(timer); setPendingConfirmation(null); setStatus("ready");
        const text = "那咱们这次先不记，需要的时候再说。"; setMessage(text);
        if ("speechSynthesis" in window) { const utterance = new SpeechSynthesisUtterance(text); utterance.lang = "zh-CN"; speechSynthesis.speak(utterance); }
      }
    }, 300);
    return () => clearInterval(timer);
  }, [pendingConfirmation]);

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined;
    let cancelled = false;
    void (async () => {
      if (await ensureSession("elder")) { if (!cancelled) { setPaired(true); await loadConversation(); } return; }
      try {
        const stored = localStorage.getItem("miniclaw.elder.pairing_code");
        if (stored) setPairingCode(stored);
        else { const pairing = await startElderPairing(); setPairingCode(pairing.pairing_code); }
        timer = setInterval(() => { void (async () => {
          if (!localStorage.getItem("miniclaw.elder.enrollment")) { const next = await startElderPairing(); if (!cancelled) setPairingCode(next.pairing_code); }
          const result = await pollElderPairing();
          if (result && !cancelled) { if (timer) clearInterval(timer); setPaired(true); setPairingCode(""); void loadConversation(); }
        })().catch((cause) => { if (!cancelled) setPairingError(cause instanceof Error ? cause.message : "配对状态读取失败。"); }); }, 2000);
      } catch (error) { setPairingError(error instanceof Error ? error.message : "暂时无法生成配对码。"); }
    })();
    return () => { cancelled = true; if (timer) clearInterval(timer); stopSilenceMonitor(); stream.current?.getTracks().forEach((track) => track.stop()); controller.current?.abort(); if (audioUrl.current) URL.revokeObjectURL(audioUrl.current); };
  }, [loadConversation]);
  useEffect(() => { const expired = (event: Event) => { if ((event as CustomEvent).detail === "elder") window.location.reload(); }; window.addEventListener("miniclaw.session.expired", expired); return () => window.removeEventListener("miniclaw.session.expired", expired); }, []);
  useEffect(() => {
    const capture = (event: Event) => { event.preventDefault(); const promptable = event as Event & { prompt: () => Promise<void> }; setInstallPrompt(() => promptable.prompt.bind(promptable)); };
    window.addEventListener("beforeinstallprompt", capture); return () => window.removeEventListener("beforeinstallprompt", capture);
  }, []);
  useEffect(() => {
    if (!paired) return;
    telemetrySession.current = crypto.randomUUID();
    let active = true;
    const poll = async () => {
      try {
        const response = await elderFetch("/api/reminders/unacknowledged"); if (!response.ok) return;
        const data: { items: ReminderDelivery[] } = await response.json(); if (!active) return;
        setDeliveries(data.items);
        for (const delivery of data.items) {
          if (!announcedDeliveries.current.has(delivery.delivery_id)) {
            announcedDeliveries.current.add(delivery.delivery_id);
            if ("speechSynthesis" in window) { speechSynthesis.cancel(); const line = new SpeechSynthesisUtterance(`提醒时间到了：${delivery.title}`); line.lang = "zh-CN"; speechSynthesis.speak(line); }
            else setMessage(`提醒时间到了：${delivery.title}`);
            continue;
          }
          if (Date.now() - new Date(delivery.triggered_at).getTime() >= 5 * 60 * 1000 && !repeatedDeliveries.current.has(delivery.delivery_id)) {
            repeatedDeliveries.current.add(delivery.delivery_id);
            if ("speechSynthesis" in window) { speechSynthesis.cancel(); const line = new SpeechSynthesisUtterance(`再提醒您一次：${delivery.title}`); line.lang = "zh-CN"; speechSynthesis.speak(line); }
          }
        }
      } catch { /* the next poll retries; reminder state remains durable on the server */ }
    };
    void poll(); const timer = setInterval(() => void poll(), 15000);
    return () => { active = false; clearInterval(timer); };
  }, [paired]);

  useEffect(() => { if (paired && conversation) sendTelemetry("page_open"); }, [paired, conversation?.id]);

  function sendTelemetry(event_type: "page_open" | "voice_start" | "retry" | "cancel" | "voice_complete") {
    if (!telemetrySession.current) return;
    void elderFetch("/api/telemetry", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_key: telemetrySession.current, event_type, conversation_id: conversation?.id ?? null }) }).catch(() => undefined);
  }

  const processAudio = useCallback(async (blob: Blob) => {
    if (!conversation || !selectedRole) return;
    voiceCycleStartedAt.current = performance.now();
    pendingBlob.current = blob;
    setStatus("processing"); setMessage("我听着呢，马上就好");
    comfortTimer.current = setTimeout(() => setBusyText(true), 3000);
    const form = new FormData();
    form.append("conversation_id", conversation.id);
    form.append("audio", blob, "recording.webm");
    controller.current = new AbortController();
    try {
      const response = await elderFetch("/api/voice/chat", { method: "POST", body: form, signal: controller.current.signal });
      const result: VoiceResult = await response.json();
      if (!response.ok) throw new Error(result.message ?? "网络不太好，等一下再试");
      if (result.status === "confirmation_required") { announceConfirmation(result, true); return; }
      if (result.status === "not_heard") { setStatus("ready"); setMessage("我没听清，您再说一遍好吗"); return; }
      if (!result.answer || !result.audio_base64 || !result.metric_id) throw new Error("没有收到语音回复，请再试一次");
      setLines((old) => [...old, { role: "user", text: result.transcript ?? "" }, { role: "assistant", text: result.answer! }]);
      setMetricId(result.metric_id);
      const bytes = Uint8Array.from(atob(result.audio_base64), (char) => char.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: result.audio_content_type ?? "audio/mpeg" }));
      if (audioUrl.current) URL.revokeObjectURL(audioUrl.current);
      audioUrl.current = url; setLastAudio(url); setStatus("speaking"); setMessage("");
      const player = new Audio(url); const playbackStarted = performance.now();
      player.onended = () => {
        const duration = Math.round(performance.now() - playbackStarted);
        sendTelemetry("voice_complete");
        if (result.metric_id) { const data = new FormData(); data.append("duration_ms", String(duration)); if (voiceCycleStartedAt.current !== null) data.append("end_to_end_ms", String(Math.round(performance.now() - voiceCycleStartedAt.current))); void elderFetch(`/api/voice/${result.metric_id}/playback`, { method: "POST", body: data }); }
        setStatus("ready"); setMessage("说完了。您可以再说一句，或者点“再听一次”。");
      };
      player.onerror = () => { setStatus("error"); setMessage("语音没能播放出来，您可以点“再听一次”。"); };
      await player.play();
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") { setStatus("ready"); setMessage("这次已取消，您准备好时再说。"); }
      else { setStatus("error"); setMessage(error instanceof Error ? error.message : "网络不太好，等一下再试"); }
    } finally { if (comfortTimer.current) clearTimeout(comfortTimer.current); setBusyText(false); controller.current = null; }
  }, [conversation, selectedRole]);

  async function chooseRole(roleKey: string) {
    if (!conversation || status === "speaking" || status === "processing" || status === "listening") return;
    setStatus("processing"); setMessage("正在为您切换陪伴角色…");
    try {
      const response = await elderFetch(`/api/conversations/${conversation.id}/role`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role_key: roleKey }) });
      const result = await response.json(); if (!response.ok) throw new Error("这个角色暂时无法使用，请稍后再试。");
      setSelectedRole(roleKey); setLines((old) => [...old, { role: "assistant", text: result.self_intro }]);
      setMessage(result.self_intro);
      if (result.audio_base64) {
        const bytes = Uint8Array.from(atob(result.audio_base64), (char) => char.charCodeAt(0));
        const url = URL.createObjectURL(new Blob([bytes], { type: result.audio_content_type ?? "audio/mpeg" }));
        if (audioUrl.current) URL.revokeObjectURL(audioUrl.current);
        audioUrl.current = url; setLastAudio(url);
        const player = new Audio(url); playerRef.current = player; setStatus("speaking");
        player.onended = () => { setStatus("ready"); setMessage("点一下大按钮开始说话，再点一下结束录音。"); };
        await player.play();
      } else {
        setStatus("speaking");
        if ("speechSynthesis" in window) {
          const utterance = new SpeechSynthesisUtterance(result.self_intro); utterance.lang = "zh-CN";
          utterance.onend = () => { setStatus("ready"); setMessage("点一下大按钮开始说话，再点一下结束录音。"); };
          utterance.onerror = () => { setStatus("ready"); setMessage("点一下大按钮开始说话，再点一下结束录音。"); };
          speechSynthesis.speak(utterance);
        } else { setStatus("ready"); setMessage(result.self_intro); }
      }
    } catch (error) { setStatus("error"); setMessage(error instanceof Error ? error.message : "暂时无法切换角色，请再试一次。"); }
  }

  async function toggleRecording() {
    if (recorder.current?.state === "recording") { stopSilenceMonitor(); recorder.current.stop(); stream.current?.getTracks().forEach((track) => track.stop()); setStatus("processing"); setMessage("正在处理，请稍等…"); return; }
    if (status === "processing" || status === "speaking" || !selectedRole) return;
    setPermissionDenied(false);
    sendTelemetry("voice_start");
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, sampleRate: 16000, echoCancellation: true, noiseSuppression: true } });
      stream.current = media; chunks.current = [];
      const rec = new MediaRecorder(media); recorder.current = rec;
      rec.ondataavailable = (event) => { if (event.data.size) chunks.current.push(event.data); };
      rec.onstop = () => { stopSilenceMonitor(); if (cancelledRecording.current) { cancelledRecording.current = false; return; } const blob = new Blob(chunks.current, { type: rec.mimeType || "audio/webm" }); if (blob.size) void processAudio(blob); else { setStatus("ready"); setMessage("没有录到声音，您再试一次好吗？"); } };
      rec.start(); setStatus("listening"); setMessage("我在听，您慢慢说。说完再点一下按钮。");
      const context = new AudioContext(); audioContext.current = context;
      const meter = context.createAnalyser(); meter.fftSize = 2048; context.createMediaStreamSource(media).connect(meter); analyser.current = meter;
      const samples = new Uint8Array(meter.fftSize); let lastVoiceAt = performance.now(); let voiceDetected = false;
      const watchSilence = () => {
        if (rec.state !== "recording" || !analyser.current) return;
        analyser.current.getByteTimeDomainData(samples);
        let energy = 0;
        for (const sample of samples) { const amplitude = (sample - 128) / 128; energy += amplitude * amplitude; }
        const now = performance.now();
        if (Math.sqrt(energy / samples.length) > 0.025) { voiceDetected = true; lastVoiceAt = now; }
        if (voiceDetected && now - lastVoiceAt >= 3000) {
          setMessage("我听到您停了一会儿，开始整理刚才的话。"); setStatus("processing"); rec.stop(); media.getTracks().forEach((track) => track.stop()); return;
        }
        silenceFrame.current = requestAnimationFrame(watchSilence);
      };
      silenceFrame.current = requestAnimationFrame(watchSilence);
      setTimeout(() => { if (rec.state === "recording") { stopSilenceMonitor(); rec.stop(); media.getTracks().forEach((track) => track.stop()); setMessage("录音到一分钟了，我来听听刚才说的内容。"); } }, 60_000);
    } catch (error) {
      setStatus("error"); setPermissionDenied(error instanceof DOMException && (error.name === "NotAllowedError" || error.name === "PermissionDeniedError"));
      setMessage("需要允许麦克风权限才能听您说话。请在浏览器设置里打开麦克风，再点下面按钮重试。");
    }
  }

  function cancel() {
    sendTelemetry("cancel");
    if (recorder.current?.state === "recording") { stopSilenceMonitor(); cancelledRecording.current = true; recorder.current.stop(); stream.current?.getTracks().forEach((track) => track.stop()); chunks.current = []; }
    playerRef.current?.pause(); if (playerRef.current) playerRef.current.currentTime = 0;
    controller.current?.abort(); setStatus("ready"); setMessage("这次已取消，您准备好时再说。");
  }
  function stopSilenceMonitor() {
    if (silenceFrame.current !== null) cancelAnimationFrame(silenceFrame.current);
    silenceFrame.current = null; analyser.current = null;
    if (audioContext.current) { void audioContext.current.close().catch(() => undefined); audioContext.current = null; }
  }
  function retry() { sendTelemetry("retry"); if (pendingBlob.current) void processAudio(pendingBlob.current); else void toggleRecording(); }
  function replay() { if (!lastAudio) { setMessage("还没有可以重播的内容。"); return; } setStatus("speaking"); const player = new Audio(lastAudio); playerRef.current = player; player.onended = () => { setStatus("ready"); setMessage(pendingConfirmation?.recite_text ?? "说完了。您可以继续聊聊。"); }; void player.play().catch(() => { setStatus("error"); setMessage("语音没能播放出来，请再试一次。"); }); }
  async function goHome() { cancel(); setLines([]); if (audioUrl.current) URL.revokeObjectURL(audioUrl.current); audioUrl.current = null; setLastAudio(null); setMessage("回到聊天首页了。准备好时，点大按钮说话。"); }

  const stateClass = `state-${status}`;
  if (offlineMode) return <main className="voice-page"><section className="voice-main" role="alert"><div className="welcome-icon">☀</div><h1>{recoveryMode ? "正在整理，稍等一下" : "现在服务暂时连不上"}</h1><p className="hint">{recoveryMode ? "家里的资料正在恢复，请稍等一会儿再试。" : "小爪正在休息。您可以联系家人，或者稍后再试。"}</p><div className="voice-actions"><a className="offline-call" href={`tel:${process.env.NEXT_PUBLIC_FAMILY_PHONE ?? ""}`}>☎ 联系家人</a><button onClick={() => { const comfort = "别着急，先坐下来歇一歇。您可以给家人打个电话，我会在这里等您回来。"; setMessage(comfort); if ("speechSynthesis" in window) { speechSynthesis.cancel(); const line = new SpeechSynthesisUtterance(comfort); line.lang = "zh-CN"; speechSynthesis.speak(line); } }}>♫ 听一段陪伴的话</button><button onClick={() => { setOfflineMode(false); void fetch(`${API}/health`, { cache: "no-store" }).then((r) => { setRecoveryMode(r.status === 503); setOfflineMode(!r.ok); }).catch(() => setOfflineMode(true)); }}>↻ 稍后再试</button></div><p className="hint">小爪是陪伴程序，不是真实家庭成员。</p></section></main>;
  if (!paired) return <main className="voice-page"><header className="voice-header"><div className="voice-brand"><span className="brand-mark">爪</span><div><strong>MiniClaw</strong><small>长辈设备配对</small></div></div></header><section className="voice-main"><h1>请让家属帮忙绑定</h1><p className="hint">家属在“家属设置”里输入下面的 6 位配对码。这个码 10 分钟内有效。</p><strong style={{fontSize:48,letterSpacing:12}}>{pairingCode || "正在准备…"}</strong>{pairingError && <p role="alert">{pairingError}</p>}<p><Link href="/access">我有谁在看我</Link></p></section></main>;
  return <main className="voice-page">
    <header className="voice-header"><div className="voice-brand"><span className="brand-mark">爪</span><div><strong>MiniClaw</strong><small>陪你聊聊天</small></div></div><span className="program-label">陪伴程序</span></header>
    {familyChange && <p className="family-change-note" role="status">{familyChange}</p>}
    <section className="reminder-setup" aria-label="提醒通知与安装设置"><details><summary>提醒通知与安装说明</summary><div className="reminder-steps"><article><b>1</b><span>点“安装 MiniClaw”，或从浏览器菜单添加到主屏幕。</span></article><article><b>2</b><span>在系统提示中允许通知。</span></article><article><b>3</b><span>保持通知权限开启；到时间时网页关闭也会尝试推送。</span></article></div><div className="voice-actions"><button onClick={() => void installApp()}>＋<span>安装 MiniClaw</span></button><button onClick={() => void enableReminderPush()}>♧<span>开启提醒通知</span></button>{process.env.NEXT_PUBLIC_APP_ENV === "development" && <button onClick={() => void simulateReminder()}>⏱<span>模拟提醒</span></button>}</div>{pushStatus && <p role="status">{pushStatus}</p>}</details></section>
    <section className="voice-main">
      <div className="welcome-icon">☀</div><h1>您好，我在这里陪您</h1>
      <div className="role-picker" aria-label="选择陪伴角色">{roles.map((role) => <button key={role.role_key} className={selectedRole === role.role_key ? "active" : ""} onClick={() => void chooseRole(role.role_key)} disabled={Boolean(pendingConfirmation) || status === "processing" || status === "speaking" || status === "listening"}>和{role.name}聊聊</button>)}</div>
      <div className={`state-label ${stateClass}`} aria-live="polite"><i/>{busyText ? "我听着呢，马上就好" : LABEL[status]}</div>
      <button className={`talk-button ${stateClass}`} onClick={() => void toggleRecording()} aria-label={status === "listening" ? "结束录音" : "开始录音"} disabled={status === "processing" || status === "speaking" || !selectedRole}><span className="mic-icon">{status === "listening" ? "■" : "🎙"}</span><span>{status === "listening" ? "说完了" : status === "processing" ? "请稍等" : status === "speaking" ? "正在播报" : pendingConfirmation ? "说话可重听复述" : "点这里说话"}</span></button>
      <p className="hint" role="status">{message}</p>
      {deliveries.length > 0 && <section className="reminder-deliveries" aria-live="assertive"><h2>到时间的提醒</h2>{deliveries.map((item) => <article key={item.delivery_id}><strong>{item.title}</strong><p>{new Date(item.triggered_at).toLocaleString("zh-CN")}{item.status === "escalated" ? " · 已通知家属" : ""}</p><div className="confirmation-buttons"><button className="accept" onClick={() => void resolveDelivery(item.delivery_id, "acknowledge")}>我知道了</button><button className="reject" onClick={() => void resolveDelivery(item.delivery_id, "ignore")}>忽略这次</button></div></article>)}</section>}
      {pendingConfirmation && <section className="confirmation-card" aria-live="assertive"><h2>请听清楚，再点一个按钮</h2><p className="confirmation-step">第 {pendingConfirmation.step} 步 / 共 {pendingConfirmation.total_steps} 步</p><p className="confirmation-recite">{pendingConfirmation.recite_text}</p><p className="confirmation-instruction">请点按钮作决定。只说“嗯”或“好”不会记下这件事。</p><div className="confirmation-buttons"><button className="accept" disabled={confirmationBusy || status === "speaking"} onClick={() => void decideReminder(true)}>是这样的</button><button className="reject" disabled={confirmationBusy || status === "speaking"} onClick={() => void decideReminder(false)}>不对，我重说</button></div></section>}
      {permissionDenied && <button className="secondary" onClick={() => void toggleRecording()}>再试一次</button>}
      {!pendingConfirmation && <div className="voice-actions"><button onClick={retry} disabled={!selectedRole || status === "listening" || status === "speaking"}>↻<span>重说 / 再试</span></button><button onClick={cancel}>×<span>取消</span></button><button onClick={replay}>♫<span>再听一次</span></button><button onClick={() => void goHome()}>⌂<span>返回</span></button></div>}
      <div className="voice-transcript" aria-live="polite">{lines.slice(-4).map((line, index) => <p key={`${index}-${line.text}`} className={line.role}>{line.text}</p>)}</div>
    </section>
      <footer>小爪是陪伴程序，不是真实家庭成员；不提供医疗诊断。<br/>录音仅用于本次识别，不会保存音频文件。需要麦克风的安全连接：localhost 或 HTTPS。<br/><Link href="/memory">我的记忆</Link> · <Link href="/access">谁能看我的资料</Link>{process.env.NEXT_PUBLIC_APP_ENV === "development" && <> · <Link href="/debug/chat">开发用文本对话</Link> · <Link href="/debug">上下文调试</Link> · <Link href="/debug/trace">请求追踪</Link> · <Link href="/family">家属设置</Link></>}</footer>
  </main>;
}
