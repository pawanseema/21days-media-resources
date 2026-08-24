import {
  countdownLabel,
  escapeHtml,
  fetchJson,
  formatDate,
  formatDateTime,
  formatTime,
} from "./api.js";
import { openPlayer } from "./player.js";

const SESSIONS_URL = "/api/live/sessions";
const RECENT_URL = "/api/live/recent";

function openYouTube(item) {
  const videoId = (item.video_id || "").trim();
  if (!videoId) {
    if (item.youtube_live_url) window.open(item.youtube_live_url, "_blank", "noopener");
    return;
  }
  openPlayer({
    videoId,
    title: item.title || "Sahaja Yoga meditation",
    sectionTitle: item.channel_title || item.channel_handle || "",
    url: item.youtube_live_url || item.youtube_watch_url,
  });
}

function sessionCard(session) {
  const live = session.status === "live";
  const title = escapeHtml(session.title || "Sahaja Yoga meditation");
  const channel = session.channel_title || session.channel_handle || "";
  const start = session.starts_at;
  const dateLabel = formatDate(start);
  const timeLabel = formatTime(start);
  const countdown = countdownLabel(start, live);
  const hasYouTube = Boolean((session.video_id || "").trim() || session.youtube_live_url);
  const hasZoom = Boolean((session.zoom_meeting_url || "").trim());

  const actions = live
    ? `<div class="live-actions">
         <p class="muted live-actions-hint">Watch here, or join Zoom for the interactive meeting.</p>
         <div class="join-btn-row">
           <button type="button" class="join-btn" id="watchYoutube" ${hasYouTube ? "" : "disabled"}>
             <strong>Watch on YouTube</strong>
             <small>Plays on this page</small>
           </button>
           <button type="button" class="join-btn outline" id="joinZoom" ${hasZoom ? "" : "disabled"}>
             <strong>Join Zoom Meeting</strong>
             <small>Opens Zoom</small>
           </button>
         </div>
       </div>`
    : "";

  return `
    <article class="surface-card">
      ${live ? `<span class="live-badge">LIVE</span>` : `<span class="upcoming-badge">Upcoming</span>`}
      <h2 style="margin:12px 0 8px">${title}</h2>
      ${channel ? `<div class="meta-line">${escapeHtml(channel)}</div>` : ""}
      ${dateLabel ? `<div class="meta-line">${escapeHtml(dateLabel)}</div>` : ""}
      ${timeLabel ? `<div class="meta-line">${escapeHtml(timeLabel)}</div>` : ""}
      <div class="meta-line">${escapeHtml(countdown)}</div>
      ${actions}
    </article>
  `;
}

function emptyCard() {
  return `
    <article class="empty-card" style="text-align:center;padding:28px 20px">
      <h2 style="margin:0 0 8px">No live or upcoming session right now</h2>
      <p class="muted" style="margin:0">We look ahead 72 hours for the next stream.</p>
    </article>
  `;
}

function recentCard(item) {
  const thumb =
    item.youtube_thumbnail_url ||
    (item.video_id ? `https://img.youtube.com/vi/${item.video_id}/hqdefault.jpg` : "");
  const when = formatDateTime(item.starts_at || item.published_at);
  const channel = item.channel_title || item.channel_handle || "";
  return `
    <div class="session-video" data-recent-id="${escapeHtml(item.video_id || "")}">
      ${thumb
        ? `<img src="${escapeHtml(thumb)}" alt="">`
        : `<div style="width:88px;height:50px;background:var(--mist);border-radius:8px;flex-shrink:0"></div>`}
      <div>
        <div style="font-weight:700">${escapeHtml(item.title || "Recent session")}</div>
        ${channel ? `<div class="muted" style="font-size:13px">${escapeHtml(channel)}</div>` : ""}
        ${when ? `<div class="muted" style="font-size:13px">${escapeHtml(when)}</div>` : ""}
      </div>
    </div>
  `;
}

export async function showLive() {
  const panel = document.getElementById("panel-live");
  panel.innerHTML = `<div class="panel-status"><div class="spinner"></div><p>Loading live sessions…</p></div>`;

  let sessionData = { session: null };
  let recentData = { items: [] };
  let error = "";

  try {
    const sessionRes = await fetchJson(SESSIONS_URL);
    sessionData = sessionRes.data;
  } catch (err) {
    error = err.message || "Could not reach live sessions.";
  }

  try {
    const recentRes = await fetchJson(RECENT_URL);
    recentData = recentRes.data;
  } catch (err) {
    if (!error) error = err.message || "Could not load recent sessions.";
  }

  const session = sessionData.session;
  const items = Array.isArray(recentData.items) ? recentData.items : [];

  panel.innerHTML = `
    ${error
      ? `<div class="error-banner">
           <strong>Could not reach live sessions</strong>
           <p class="muted">${escapeHtml(error)}</p>
           <button type="button" class="btn secondary" id="liveRetry">Retry</button>
         </div>`
      : ""}
    ${session ? sessionCard(session) : emptyCard()}
    <h2 class="section-heading">Recent</h2>
    <p class="section-sub">Tap to watch</p>
    <div id="recentList" class="${items.length ? "recent-list" : ""}">
      ${items.length
        ? items.map(recentCard).join("")
        : `<p class="muted">No recent recordings in the last 72 hours.</p>`}
    </div>
  `;

  const retry = document.getElementById("liveRetry");
  if (retry) retry.addEventListener("click", showLive);

  const watch = document.getElementById("watchYoutube");
  if (watch && session) watch.addEventListener("click", () => openYouTube(session));

  const zoom = document.getElementById("joinZoom");
  if (zoom && session && session.zoom_meeting_url) {
    zoom.addEventListener("click", () => {
      window.open(session.zoom_meeting_url, "_blank", "noopener");
    });
  }

  panel.querySelectorAll("[data-recent-id]").forEach((card, index) => {
    card.addEventListener("click", () => openYouTube(items[index]));
  });
}
