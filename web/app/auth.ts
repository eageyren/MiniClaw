const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type TokenPair = { access_token: string; refresh_token?: string; expires_at?: string; role?: string };

function key(role: "elder" | "family", field: "access" | "refresh") { return `miniclaw.${role}.${field}`; }
function save(role: "elder" | "family", pair: TokenPair) {
  localStorage.setItem(key(role, "access"), pair.access_token);
  if (pair.refresh_token) localStorage.setItem(key(role, "refresh"), pair.refresh_token);
}
function clear(role: "elder" | "family") { localStorage.removeItem(key(role, "access")); localStorage.removeItem(key(role, "refresh")); }

async function refresh(role: "elder" | "family"): Promise<boolean> {
  const secret = localStorage.getItem(key(role, "refresh"));
  if (!secret) return false;
  const response = await fetch(`${API}/api/auth/refresh`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: secret }) });
  if (!response.ok) { clear(role); return false; }
  const pair: TokenPair = await response.json(); save(role, pair); return true;
}

async function authorizedFetch(role: "elder" | "family", path: string, init: RequestInit = {}): Promise<Response> {
  const send = () => {
    const headers = new Headers(init.headers);
    const token = localStorage.getItem(key(role, "access"));
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return fetch(`${API}${path}`, { ...init, headers });
  };
  const rememberRequestId = (response: Response) => {
    const requestId = response.headers.get("X-Request-ID");
    if (requestId) localStorage.setItem("miniclaw.last_request_id", requestId);
    return response;
  };
  let response = rememberRequestId(await send());
  if (response.status === 401) {
    if (await refresh(role)) response = rememberRequestId(await send());
    else if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("miniclaw.session.expired", { detail: role }));
  }
  return response;
}

export async function elderFetch(path: string, init?: RequestInit) { return authorizedFetch("elder", path, init); }
export async function familyFetch(path: string, init?: RequestInit) { return authorizedFetch("family", path, init); }
export async function ensureSession(role: "elder" | "family") {
  if (localStorage.getItem(key(role, "access"))) return true;
  return refresh(role);
}
export function storeSession(role: "elder" | "family", pair: TokenPair) { save(role, pair); }
export function clearSession(role: "elder" | "family") { clear(role); }

function decodeVapidKey(value: string): Uint8Array {
  const padded = value + "=".repeat((4 - value.length % 4) % 4);
  const binary = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

export async function enablePush(role: "elder" | "family", notifyFamily = false) {
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) throw new Error("这个浏览器暂时不支持网页推送，请更新浏览器后再试。");
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("需要允许通知，提醒才能送到这台设备。");
  const registration = await navigator.serviceWorker.register("/sw.js");
  const response = await (role === "elder" ? elderFetch("/api/push/vapid-public-key") : familyFetch("/api/push/vapid-public-key"));
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail ?? "网页推送尚未配置。");
  const subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: decodeVapidKey(data.public_key) as BufferSource });
  const save = await (role === "elder" ? elderFetch("/api/push/subscriptions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...subscription.toJSON(), notify_family: false }) }) : familyFetch("/api/push/subscriptions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...subscription.toJSON(), notify_family: notifyFamily }) }));
  if (!save.ok) { const result = await save.json().catch(() => ({})); throw new Error(result.detail ?? "推送设置保存失败。"); }
  return subscription;
}

export async function startElderPairing() {
  const response = await fetch(`${API}/api/auth/pairing-code`, { method: "POST" });
  if (!response.ok) throw new Error("暂时生成不了配对码，请稍后再试。");
  const pair: { pairing_code: string; enrollment_secret: string; expires_at: string } = await response.json();
  localStorage.setItem("miniclaw.elder.enrollment", pair.enrollment_secret);
  localStorage.setItem("miniclaw.elder.pairing_code", pair.pairing_code);
  return pair;
}

export async function pollElderPairing() {
  const enrollment_secret = localStorage.getItem("miniclaw.elder.enrollment");
  if (!enrollment_secret) return null;
  const response = await fetch(`${API}/api/auth/pairing/status`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enrollment_secret }) });
  if (response.status === 410) { localStorage.removeItem("miniclaw.elder.enrollment"); return null; }
  if (!response.ok) return null;
  const result = await response.json();
  if (result.status !== "paired") return null;
  save("elder", result); localStorage.removeItem("miniclaw.elder.enrollment"); localStorage.removeItem("miniclaw.elder.pairing_code");
  return result as TokenPair;
}
