import {
  escapeHtml,
  extractVideoId,
  fetchJson,
  formatClipDuration,
  timestampToSeconds,
} from "./api.js";
import { copyLink, shareOrCopy } from "./share.js";

const CHAPTERS_URL = (videoId) =>
  `/api/videos/${encodeURIComponent(videoId)}/chapters`;

let onCloseCallback = null;
let currentVideoId = "";
let currentStart = 0;
let currentShareTitle = "";

function watchUrl(videoId, startSeconds) {
  const t = Math.max(0, Number(startSeconds) || 0);
  const base = `https://www.youtube.com/watch?v=${videoId}`;
  return t > 0 ? `${base}&t=${t}s` : base;
}

function isMobileDevice() {
  return (
    /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent) ||
    (navigator.maxTouchPoints > 1 && /Macintosh/i.test(navigator.userAgent))
  );
}

function embedUrl(videoId, startSeconds) {
  const autoplay = isMobileDevice() ? 0 : 1;
  return (
    `https://www.youtube.com/embed/${videoId}` +
    `?start=${startSeconds}&autoplay=${autoplay}&playsinline=1&rel=0`
  );
}

function setIframe(videoId, startSeconds) {
  const frame = document.getElementById("playerFrame");
  frame.innerHTML =
    `<iframe src="${embedUrl(videoId, startSeconds)}" ` +
    `allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" ` +
    `allowfullscreen playsinline></iframe>`;
}

function currentLink() {
  return watchUrl(currentVideoId, currentStart);
}

function renderChapters(chapters, activeSeconds) {
  const list = document.getElementById("chapterList");
  const heading = document.getElementById("chapterHeading");
  if (!chapters.length) {
    heading.hidden = true;
    list.innerHTML = "";
    return;
  }
  heading.hidden = false;
  list.innerHTML = chapters
    .map((ch) => {
      const start = Number(ch.start_seconds) || timestampToSeconds(ch.timestamp);
      const active = start === activeSeconds ? " active" : "";
      return `<button type="button" class="chapter-row${active}" data-start="${start}">
        <span>${escapeHtml(ch.section_title || "Section")}</span>
        <span class="chapter-time">${escapeHtml(ch.timestamp || formatClipDuration(start))}</span>
      </button>`;
    })
    .join("");

  list.querySelectorAll(".chapter-row").forEach((btn) => {
    btn.addEventListener("click", () => {
      const start = Number(btn.dataset.start) || 0;
      currentStart = start;
      setIframe(currentVideoId, start);
      const youtubeLink = document.getElementById("openYoutube");
      youtubeLink.href = currentLink();
      list.querySelectorAll(".chapter-row").forEach((row) => {
        row.classList.toggle("active", row === btn);
      });
    });
  });
}

export async function openPlayer({
  videoId,
  title = "",
  sectionTitle = "",
  startSeconds = 0,
  timestamp = "",
  url = "",
  onClose,
} = {}) {
  currentVideoId = videoId || extractVideoId(url);
  if (!currentVideoId) return;

  onCloseCallback = typeof onClose === "function" ? onClose : null;
  let start = startSeconds || timestampToSeconds(timestamp);
  currentShareTitle =
    [title, sectionTitle].filter(Boolean).join(" — ") || "Meditation video";

  const modal = document.getElementById("videoModal");
  const titleEl = document.getElementById("modalTitle");
  const metaTitle = document.getElementById("playerVideoTitle");
  const metaSection = document.getElementById("playerSectionTitle");
  const youtubeLink = document.getElementById("openYoutube");
  const heading = document.getElementById("chapterHeading");
  const list = document.getElementById("chapterList");

  titleEl.textContent = currentShareTitle;
  metaTitle.textContent = title || "Meditation video";
  metaSection.textContent = sectionTitle || "";
  youtubeLink.href = watchUrl(currentVideoId, start);
  heading.hidden = true;
  list.innerHTML = "";

  modal.classList.add("open");
  document.body.style.overflow = "hidden";

  let chapters = [];
  try {
    const { data } = await fetchJson(CHAPTERS_URL(currentVideoId));
    chapters = Array.isArray(data.chapters) ? data.chapters : [];
  } catch (_) {
    chapters = [];
  }

  if (chapters.length) {
    const first = Number(chapters[0].start_seconds) || 0;
    if (start < first) start = first;
  }
  currentStart = start;
  setIframe(currentVideoId, start);
  youtubeLink.href = currentLink();
  renderChapters(chapters, start);
}

export function closePlayer() {
  const modal = document.getElementById("videoModal");
  if (!modal.classList.contains("open")) return;
  modal.classList.remove("open");
  document.getElementById("playerFrame").innerHTML = "";
  document.body.style.overflow = "";
  const cb = onCloseCallback;
  onCloseCallback = null;
  if (cb) cb();
}

export function initPlayer() {
  document.getElementById("playerClose").addEventListener("click", closePlayer);
  document.getElementById("videoModal").addEventListener("click", (event) => {
    if (event.target.id === "videoModal") closePlayer();
  });
  document.getElementById("playerShareBtn").addEventListener("click", () => {
    shareOrCopy({ url: currentLink(), title: currentShareTitle });
  });
  document.getElementById("playerCopyBtn").addEventListener("click", () => {
    copyLink(currentLink());
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closePlayer();
  });
}
