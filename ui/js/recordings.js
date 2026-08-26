import { API_MESSAGES, escapeHtml, fetchJson, formatDate } from "./api.js";
import { openPlayer } from "./player.js";

const RECORDINGS_URL = "/api/recordings";

function videoRow(sessionLabel, video) {
  const thumb =
    video.youtube_thumbnail_url ||
    (video.video_id ? `https://img.youtube.com/vi/${video.video_id}/hqdefault.jpg` : "");
  const published = formatDate(video.published_at);
  return `
    <div class="session-video" data-video-id="${escapeHtml(video.video_id || "")}">
      ${thumb ? `<img src="${escapeHtml(thumb)}" alt="">` : `<div style="width:88px;height:50px;background:var(--mist);border-radius:8px"></div>`}
      <div>
        <div style="font-weight:700">${escapeHtml(video.title || "Recording")}</div>
        ${published ? `<div class="muted" style="font-size:13px">${escapeHtml(published)}</div>` : ""}
      </div>
    </div>
  `;
}

function sessionDateRange(session) {
  const start = formatDate(session.starts_at);
  const end = formatDate(session.ends_at);
  if (start && end) {
    return start === end ? start : `${start} – ${end}`;
  }
  if (start) return `Starts ${start}`;
  if (end) return `Ends ${end}`;
  return "";
}

function sessionTile(session) {
  const videos = (session.videos || []).filter((v) => v && v.video_id);
  if (!videos.length) return "";
  const count = videos.length;
  const range = sessionDateRange(session);
  const meta = [
    range,
    `${count} recording${count === 1 ? "" : "s"}`,
  ]
    .filter(Boolean)
    .join(" · ");
  return `
    <details class="session-tile">
      <summary>
        <h3>${escapeHtml(session.label || "Session")}</h3>
        <p class="muted" style="margin:4px 0 0">${escapeHtml(meta)}</p>
      </summary>
      ${videos.map((video) => videoRow(session.label, video)).join("")}
    </details>
  `;
}

export async function showRecordings() {
  const panel = document.getElementById("panel-recordings");
  const setLoading = (text) => {
    panel.innerHTML = `<div class="panel-status"><div class="spinner"></div><p>${escapeHtml(text)}</p></div>`;
  };
  setLoading("Loading recordings…");

  try {
    const { data } = await fetchJson(RECORDINGS_URL, {}, {
      onRetry: () => setLoading(API_MESSAGES.retrying),
    });
    const sessions = (data.sessions || []).filter(
      (session) => Array.isArray(session.videos) && session.videos.some((v) => v && v.video_id)
    );
    if (!sessions.length) {
      panel.innerHTML = `<div class="panel-status"><p>No recordings are configured yet.</p></div>`;
      return;
    }
    const heading = data.title || `${data.year || ""} recordings`;
    panel.innerHTML = `
      <h2 class="section-heading" style="margin-top:4px">${escapeHtml(heading)}</h2>
      <p class="section-sub">Open a session to browse its recordings. Tap a row to play here.</p>
      ${sessions.map((session) => sessionTile(session)).join("")}
    `;

    panel.querySelectorAll(".session-video").forEach((row) => {
      row.addEventListener("click", () => {
        const videoId = row.dataset.videoId;
        if (!videoId) return;
        const title = row.querySelector("div > div")?.textContent || "Recording";
        openPlayer({ videoId, title, sectionTitle: "", startSeconds: 0 });
      });
    });
  } catch (err) {
    panel.innerHTML = `
      <div class="panel-status">
        <p>${escapeHtml(API_MESSAGES.requestFailed)}</p>
        <button type="button" class="btn secondary" id="recordingsRetry">Retry</button>
      </div>
    `;
    document.getElementById("recordingsRetry").addEventListener("click", showRecordings);
  }
}
