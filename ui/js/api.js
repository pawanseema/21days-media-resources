/** Shared fetch helper for the 21Days web app. */

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function escapeHtml(text) {
  if (!text) return "";
  const div = document.createElement("div");
  div.textContent = String(text);
  return div.innerHTML;
}

export function extractVideoId(url = "") {
  const patterns = [
    /(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([^&\n?#]+)/,
    /youtube\.com\/.*[?&]v=([^&\n?#]+)/,
  ];
  for (const pattern of patterns) {
    const match = String(url).match(pattern);
    if (match && match[1]) return match[1];
  }
  return "";
}

export function timestampToSeconds(timestamp) {
  if (timestamp == null || timestamp === "") return 0;
  if (typeof timestamp === "number" && Number.isFinite(timestamp)) {
    return Math.max(0, Math.floor(timestamp));
  }
  const parts = String(timestamp).split(":").map(Number);
  if (parts.some((n) => !Number.isFinite(n))) return 0;
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  return 0;
}

export function formatClipDuration(seconds) {
  if (seconds == null || seconds === "") return "";
  const total = Math.floor(Number(seconds));
  if (!Number.isFinite(total) || total < 0) return "";
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function formatDateTime(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

export function formatDate(iso) {
  if (!iso) return "";
  // Date-only strings are calendar dates; parse as local civil day (not UTC).
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso).trim());
  const date = m
    ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
    : new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    weekday: "long",
    month: "short",
    day: "numeric",
  }).format(date);
}

export function formatTime(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

export function countdownLabel(startsAt, isLive) {
  if (isLive) return "Happening now";
  if (!startsAt) return "Scheduled on YouTube";
  const until = new Date(startsAt).getTime() - Date.now();
  if (!Number.isFinite(until)) return "Scheduled on YouTube";
  if (until <= 0) return "Starting soon";
  const days = Math.floor(until / 86400000);
  if (days >= 1) return `In ${days} day${days === 1 ? "" : "s"}`;
  const hours = Math.floor(until / 3600000);
  const minutes = Math.floor((until % 3600000) / 60000);
  return `In ${hours}h ${minutes}m`;
}

/**
 * POST/GET JSON. Retries transient failures up to [retries] times (default 2).
 * [onSlow] fires after 30s while an attempt is still waiting (reassurance only).
 * [onRetry] fires before each retry so UIs can show a waiting message.
 */
export const API_MESSAGES = {
  takingLonger: "Taking longer than usual.",
  tryingAgain: "Trying again…",
  // Two lines; loading UIs use white-space: pre-line + text-align: center.
  retrying: "Taking longer than usual.\nTrying again…",
  requestFailed:
    "Couldn't complete the request. Check your connection and try again.",
  slowAfterMs: 30000,
};

export async function fetchJson(
  url,
  options = {},
  { retries = 2, retryDelayMs = 450, onRetry, onSlow } = {}
) {
  let lastError = null;
  const maxAttempts = retries + 1;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    let response;
    let slowTimer = null;
    try {
      if (typeof onSlow === "function") {
        slowTimer = setTimeout(() => {
          try {
            onSlow();
          } catch (_) {
            /* ignore UI callback errors */
          }
        }, API_MESSAGES.slowAfterMs);
      }
      response = await fetch(url, options);
    } catch (err) {
      lastError = err;
      console.warn(
        `API network error (attempt ${attempt + 1}/${maxAttempts}):`,
        err
      );
      if (attempt < retries) {
        if (typeof onRetry === "function") onRetry();
        await sleep(retryDelayMs);
        continue;
      }
      throw new Error(API_MESSAGES.requestFailed);
    } finally {
      if (slowTimer != null) clearTimeout(slowTimer);
    }

    let data = {};
    try {
      data = await response.json();
    } catch (_) {
      data = {};
    }

    if (response.ok) return { response, data };

    const errMsg =
      data.error || data.message || `Request failed (${response.status})`;
    lastError = new Error(errMsg);
    console.warn(
      `API HTTP ${response.status} (attempt ${attempt + 1}/${maxAttempts}):`,
      errMsg
    );
    if (response.status >= 500 && attempt < retries) {
      if (typeof onRetry === "function") onRetry();
      await sleep(retryDelayMs);
      continue;
    }
    throw new Error(API_MESSAGES.requestFailed);
  }
  throw lastError || new Error(API_MESSAGES.requestFailed);
}

export function panelMarkup(html) {
  const wrap = document.createElement("div");
  wrap.innerHTML = html;
  return wrap;
}

export function setBusy(el, html) {
  el.innerHTML = html;
}
