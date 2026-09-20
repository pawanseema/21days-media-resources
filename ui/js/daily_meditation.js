/**
 * Sticky Today's Meditation / "Meditation Video of the Day" (localStorage + API).
 * Shared by Explore (chip / exact query) and the optional More tab.
 */
import {
  escapeHtml,
  fetchJson,
  formatClipDuration,
  timestampToSeconds,
} from "./api.js";
import { openPlayer } from "./player.js";

export const API_PATH = "/api/recommendations/daily-meditation";
export const STORAGE_KEY = "daily_meditation_state_v1";
export const HISTORY_LIMIT = 21;
export const BOUNDARY_HOUR = 6;

/** Exact chip / typed query reserved for sticky daily meditation (Videos). */
export const DAILY_MEDITATION_QUERY = "Meditation Video of the Day";

export const GUIDANCE_TEXT =
  "Find a quiet, peaceful space and settle into a comfortable, relaxed position. " +
  "When you feel ready, press play and follow along to get into meditation.";

/** Panel heading on Explore daily-meditation results. */
export const DAILY_RESULT_TITLE = "Recommended Meditation Video for Today";

/** Shown below the title after the user has opened/played today's pick. */
export const DAILY_PLAYED_SUBTITLE = "A new recommendation comes tomorrow.";

export function isDailyMeditationQuery(query) {
  return (
    String(query || "")
      .trim()
      .toLowerCase() === DAILY_MEDITATION_QUERY.toLowerCase()
  );
}

function newDeviceId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `web-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function meditationDayStart(now = new Date()) {
  const local = new Date(now.getTime());
  const six = new Date(
    local.getFullYear(),
    local.getMonth(),
    local.getDate(),
    BOUNDARY_HOUR,
    0,
    0,
    0,
  );
  if (local < six) six.setDate(six.getDate() - 1);
  return six;
}

export function meditationDayId(now = new Date()) {
  return meditationDayStart(now).toISOString();
}

function isLaterDayThan(playedDayId, now = new Date()) {
  if (!playedDayId) return false;
  const played = new Date(playedDayId);
  if (Number.isNaN(played.getTime())) return false;
  return meditationDayStart(now) > played;
}

export function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      const state = {
        deviceId: newDeviceId(),
        active: null,
        history: [],
        sectionExpanded: true,
      };
      saveState(state);
      return state;
    }
    const parsed = JSON.parse(raw);
    if (!parsed.deviceId) parsed.deviceId = newDeviceId();
    if (!Array.isArray(parsed.history)) parsed.history = [];
    if (typeof parsed.sectionExpanded !== "boolean") {
      parsed.sectionExpanded = true;
    }
    return parsed;
  } catch (_) {
    const state = {
      deviceId: newDeviceId(),
      active: null,
      history: [],
      sectionExpanded: true,
    };
    saveState(state);
    return state;
  }
}

export function saveState(state) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function excludePayload(state) {
  const out = [];
  const seen = new Set();
  const add = (videoId, timestamp) => {
    const key = `${videoId}|${timestamp}`;
    if (!videoId || !timestamp || seen.has(key)) return;
    seen.add(key);
    out.push({ video_id: videoId, timestamp });
  };
  for (const item of state.history || []) {
    add(item.videoId || item.video_id, item.timestamp);
  }
  if (state.active?.card) {
    add(state.active.card.video_id, state.active.card.timestamp);
  }
  return out;
}

export function needsFetch(state, now = new Date()) {
  const active = state.active;
  if (!active?.card) return true;
  if (!active.playedAt) return false;
  return isLaterDayThan(active.playedMeditationDayId, now);
}

function practiceLabel(card) {
  const seconds =
    card.practice_duration_seconds ?? card.section_duration_seconds;
  const label = formatClipDuration(seconds);
  return label ? `Length ${label}` : "";
}

export function renderMeditationCard(card) {
  const videoId = card.video_id || "";
  const thumb = videoId
    ? `https://img.youtube.com/vi/${videoId}/maxresdefault.jpg`
    : "";
  const duration = practiceLabel(card);
  return `
    <article class="result-card more-meditation-card" data-play="1">
      <div class="thumb">
        ${
          thumb
            ? `<img src="${thumb}" alt="" loading="lazy"
                 onerror="this.src='https://img.youtube.com/vi/${escapeHtml(videoId)}/hqdefault.jpg'">`
            : ""
        }
        <div class="play-overlay"></div>
      </div>
      <div class="card-body">
        <div class="video-title">${escapeHtml(card.video_title || "Meditation video")}</div>
        ${
          duration
            ? `<div class="meta-row"><span class="pill">${escapeHtml(duration)}</span></div>`
            : ""
        }
      </div>
    </article>
  `;
}

export function markPlayed(state, now = new Date()) {
  if (!state?.active || state.active.playedAt) return state;
  state.active.playedAt = now.toISOString();
  state.active.playedMeditationDayId = meditationDayId(now);
  saveState(state);
  return state;
}

export function bindMeditationPlay(root, state, card, { onPlayed } = {}) {
  const el = root.querySelector("[data-play]");
  if (!el) return;
  el.addEventListener("click", () => {
    const alreadyPlayed = Boolean(state?.active?.playedAt);
    markPlayed(state);
    if (!alreadyPlayed && typeof onPlayed === "function") {
      onPlayed(state);
    }
    openPlayer({
      videoId: card.video_id,
      title: card.video_title || "",
      sectionTitle: card.section_title || "",
      timestamp: card.timestamp || "",
      startSeconds: timestampToSeconds(card.timestamp || ""),
      url: card.url || "",
    });
  });
}

/**
 * Load sticky active card (fetch only when local rules say so).
 * @returns {Promise<{ state: object, card: object }>}
 */
export async function ensureActiveMeditation() {
  let state = loadState();

  if (needsFetch(state)) {
    const { data } = await fetchJson(API_PATH, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        device_id: state.deviceId,
        exclude: excludePayload(state),
        limit: 1,
      }),
    });

    if (!data?.result) {
      const err = new Error("No meditation recommendation is available right now.");
      err.code = "empty";
      throw err;
    }

    const history = [...(state.history || [])];
    if (state.active?.card) {
      history.unshift({
        videoId: state.active.card.video_id,
        timestamp: state.active.card.timestamp,
        playedAt: state.active.playedAt || null,
      });
      while (history.length > HISTORY_LIMIT) history.pop();
    }

    const now = new Date();
    state = {
      ...state,
      active: {
        card: data.result,
        assignedAt: now.toISOString(),
        assignedMeditationDayId: meditationDayId(now),
        playedAt: null,
        playedMeditationDayId: null,
      },
      history,
    };
    saveState(state);
  }

  return { state, card: state.active.card };
}
