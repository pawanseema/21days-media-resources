import {
  API_MESSAGES,
  escapeHtml,
  extractVideoId,
  fetchJson,
  formatClipDuration,
} from "./api.js";
import { openPlayer } from "./player.js";
import { copyLink, shareOrCopy } from "./share.js";

const EXPLORE_API_URL = "/api/explore/query";
const UI_CONFIG_URL = "/api/ui-config";
const RELATED_API_URL = "/api/videos/related";

const PLACEHOLDERS = {
  videos: "Search meditation videos",
  resources: "Search meditation handouts",
};

const IDLE_COPY = {
  videos: {
    iconHtml:
      '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M15 10l4.5-2.5v9L15 14"></path><rect x="3" y="6" width="12" height="12" rx="2"></rect></svg>',
    title: "Find a meditation video to watch",
    subtitle:
      "Search above, or tap a suggestion, and matching clips will show up here.",
  },
  resources: {
    iconHtml:
      '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M8 4h9a2 2 0 0 1 2 2v14l-3-2-3 2-3-2-3 2V6a2 2 0 0 1 2-2z"></path><path d="M10 9h5M10 13h5"></path></svg>',
    title: "Find a meditation handout to read",
    subtitle:
      "Search above, or tap a suggestion, and matching handouts will show up here.",
  },
};

/** Video chips ordered shortest-first so wrap uses less vertical space. */
const EXAMPLE_PROMPTS = {
  videos: [
    "Foot Soak with Mark",
    "Meditation with Flute",
    "Heart chakra meditation",
    "Meditation and Daily Life",
    "Founder's talk on Innocence",
    "Experience the silence within",
    "What is Sahaja Yoga Meditation?",
  ],
  resources: [
    "Beginner meditation handout",
    "Chakra overview",
    "Daily meditation practice guide",
    "Affirmations for meditation",
    "How to raise Kundalini",
    "List all the handouts",
  ],
};

let searchMode = "videos";
// Match mobile: production-safe until /api/ui-config loads (Cloud Run sets false).
let uiConfig = { showResultDebug: false, enableMoreLikeThis: true };
let currentVideoResults = [];
let currentHandoutResults = [];
let lastOpenedResult = null;
let engagedSeed = null;
let relatedViewActive = false;
let relatedSeed = null;
let searchSnapshot = null;
let bound = false;

/** Last Explore session per mode (query + cards + video-only related). */
const modeCache = {
  videos: emptyVideoCache(),
  resources: emptyHandoutCache(),
};

function emptyVideoCache() {
  return {
    query: "",
    results: [],
    relatedViewActive: false,
    relatedSeed: null,
    searchSnapshot: null,
    engagedSeed: null,
    lastOpenedResult: null,
  };
}

function emptyHandoutCache() {
  return { query: "", results: [] };
}

function $(id) {
  return document.getElementById(id);
}

function seedKey(result) {
  if (!result) return "";
  const vid = result.video_id || extractVideoId(result.url || "");
  return `${vid}|${result.timestamp || ""}`;
}

async function loadUiConfig() {
  try {
    const { data } = await fetchJson(UI_CONFIG_URL, {}, { retries: 0 });
    if (typeof data.showResultDebug === "boolean") {
      uiConfig.showResultDebug = data.showResultDebug;
    }
    if (typeof data.enableMoreLikeThis === "boolean") {
      uiConfig.enableMoreLikeThis = data.enableMoreLikeThis;
    }
  } catch (err) {
    console.warn("Could not load UI config; using defaults", err);
  }
}

function hideRelatedBanner() {
  $("relatedBanner").classList.remove("visible");
  $("relatedBannerText").textContent = "";
}

function hideCatalogBanner() {
  const banner = $("catalogBanner");
  if (!banner) return;
  banner.hidden = true;
  $("catalogBannerText").textContent = "";
}

function showCatalogBanner(mode, total) {
  const banner = $("catalogBanner");
  if (!banner) return;
  const label = mode === "resources" ? "handouts" : "videos";
  const totalLabel =
    typeof total === "number" && total >= 0 ? ` (${total} total)` : "";
  $("catalogBannerText").textContent = `Showing all ${label}${totalLabel}`;
  banner.hidden = false;
}

function showRelatedBanner(seed) {
  relatedSeed = seed || null;
  const title = (seed && seed.section_title) || "this clip";
  $("relatedBannerText").innerHTML = `Showing more like: <span>${escapeHtml(title)}</span>`;
  $("relatedBanner").classList.add("visible");
}

function captureModeCache() {
  if (searchMode === "videos") {
    modeCache.videos = {
      query: $("query").value,
      results: [...currentVideoResults],
      relatedViewActive,
      relatedSeed,
      searchSnapshot,
      engagedSeed,
      lastOpenedResult,
    };
  } else {
    modeCache.resources = {
      query: $("query").value,
      results: [...currentHandoutResults],
    };
  }
}

function restoreModeCache(mode) {
  hideMessages();
  hideCatalogBanner();
  $("exploreNoResults").querySelector("p").textContent =
    "No results found. Try a different search query.";
  $("exploreLoadingText").textContent =
    mode === "videos" ? "Searching for relevant videos…" : "Searching handouts…";

  if (mode === "videos") {
    const cached = modeCache.videos || emptyVideoCache();
    $("query").value = cached.query || "";
    relatedViewActive = Boolean(cached.relatedViewActive);
    relatedSeed = cached.relatedSeed || null;
    searchSnapshot = cached.searchSnapshot || null;
    engagedSeed = cached.engagedSeed || null;
    lastOpenedResult = cached.lastOpenedResult || null;
    currentVideoResults = [...(cached.results || [])];
    if (relatedViewActive && relatedSeed) {
      showRelatedBanner(relatedSeed);
    } else {
      hideRelatedBanner();
    }
    if (currentVideoResults.length > 0) {
      displayResults(currentVideoResults, "videos");
    } else {
      $("exploreResults").innerHTML = "";
      if ((cached.query || "").trim()) {
        $("exploreNoResults").hidden = false;
      }
    }
  } else {
    const cached = modeCache.resources || emptyHandoutCache();
    $("query").value = cached.query || "";
    // Related UI is video-only; live flags stay off while viewing handouts.
    relatedViewActive = false;
    relatedSeed = null;
    searchSnapshot = null;
    engagedSeed = null;
    lastOpenedResult = null;
    hideRelatedBanner();
    currentHandoutResults = [...(cached.results || [])];
    if (currentHandoutResults.length > 0) {
      displayResults(currentHandoutResults, "resources");
    } else {
      $("exploreResults").innerHTML = "";
      if ((cached.query || "").trim()) {
        $("exploreNoResults").hidden = false;
      }
    }
  }
  updateClearButton();
  renderExamplePrompts();
  updateIdleState();
}

function switchContext(mode) {
  if (mode === searchMode) return;
  captureModeCache();
  searchMode = mode;
  $("btnVideos").classList.toggle("active", mode === "videos");
  $("btnHandouts").classList.toggle("active", mode === "resources");
  $("query").placeholder = PLACEHOLDERS[mode];
  $("exploreLoading").hidden = true;
  restoreModeCache(mode);
}

function clearRelatedState() {
  relatedViewActive = false;
  relatedSeed = null;
  searchSnapshot = null;
  engagedSeed = null;
  lastOpenedResult = null;
  hideRelatedBanner();
}

function renderExamplePrompts() {
  const chips = $("examplePromptChips");
  chips.innerHTML = "";
  (EXAMPLE_PROMPTS[searchMode] || []).forEach((text) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "example-chip";
    btn.textContent = text;
    btn.addEventListener("click", () => applyExamplePrompt(text));
    chips.appendChild(btn);
  });
  updateChipVisibility();
}

function updateChipVisibility() {
  const empty = !$("query").value.trim();
  $("examplePrompts").hidden = !(empty && !relatedViewActive);
}

function applyExamplePrompt(text) {
  $("query").value = text;
  updateClearButton();
  performSearch();
}

function updateClearButton() {
  $("clearQuery").hidden = !$("query").value;
  updateChipVisibility();
}

function showError(message, { onRetry } = {}) {
  const error = $("exploreError");
  error.innerHTML = `
    <p style="margin:0">${escapeHtml(message)}</p>
    ${
      onRetry
        ? `<button type="button" class="btn secondary" id="exploreRetry" style="margin-top:10px">Retry</button>`
        : ""
    }
  `;
  error.hidden = false;
  const retry = document.getElementById("exploreRetry");
  if (retry && typeof onRetry === "function") {
    retry.addEventListener("click", onRetry);
  }
  updateIdleState();
}

function hideMessages() {
  $("exploreError").hidden = true;
  $("exploreError").innerHTML = "";
  $("exploreNoResults").hidden = true;
  $("exploreLoading").hidden = true;
}

/** Idle guidance in the results area when there is nothing to show yet. */
function updateIdleState() {
  const idle = $("exploreIdle");
  if (!idle) return;
  const copy = IDLE_COPY[searchMode] || IDLE_COPY.videos;
  const icon = $("exploreIdleIcon");
  const title = $("exploreIdleTitle");
  const subtitle = $("exploreIdleSubtitle");
  if (icon) icon.innerHTML = copy.iconHtml;
  if (title) title.textContent = copy.title;
  if (subtitle) subtitle.textContent = copy.subtitle;

  const hasQuery = Boolean($("query").value.trim());
  const hasResults = $("exploreResults").children.length > 0;
  const loading = !$("exploreLoading").hidden;
  const noResults = !$("exploreNoResults").hidden;
  const errorVisible = !$("exploreError").hidden;
  const show =
    !hasQuery &&
    !hasResults &&
    !loading &&
    !noResults &&
    !errorVisible &&
    !relatedViewActive;
  idle.hidden = !show;
}

function createVideoCard(result) {
  const card = document.createElement("div");
  card.className = "result-card";
  card.addEventListener("click", () => openVideo(result));

  const videoId = result.video_id || extractVideoId(result.url);
  const thumbnailUrl = `https://img.youtube.com/vi/${videoId}/maxresdefault.jpg`;
  const showMoreBtn =
    uiConfig.enableMoreLikeThis &&
    engagedSeed &&
    engagedSeed.result_kind !== "video" &&
    Boolean((engagedSeed.timestamp || "").trim()) &&
    seedKey(engagedSeed) === seedKey(result);
  const durationLabel = formatClipDuration(result.section_duration_seconds);

  card.innerHTML = `
    <div class="thumb">
      <img src="${thumbnailUrl}" alt=""
           onerror="this.src='https://img.youtube.com/vi/${videoId}/hqdefault.jpg'">
      <div class="play-overlay"></div>
    </div>
    <div class="card-body">
      <div class="video-title">${escapeHtml(result.video_title)}</div>
      <div class="section-title">${escapeHtml(result.section_title)}</div>
      ${showMoreBtn ? `<button type="button" class="more-like-btn" data-more-like="1">Find similar clips</button>` : ""}
      <div class="meta-row">
        ${durationLabel ? `<span class="pill">Length ${escapeHtml(durationLabel)}</span>` : ""}
        ${uiConfig.showResultDebug ? `<span class="pill">Starts ${escapeHtml(result.timestamp || "N/A")}</span>` : ""}
      </div>
      <div class="summary">${escapeHtml(result.summary || "No summary available")}</div>
      ${
        uiConfig.showResultDebug && (result.chakra || result.confidence)
          ? `<div class="meta-row">
        ${result.chakra ? `<span class="pill chakra-pill">${escapeHtml(result.chakra)}</span>` : ""}
        ${result.confidence
          ? `<span class="pill">${(result.confidence * 100).toFixed(1)}%</span>`
          : ""}
      </div>`
          : ""
      }
      ${result.quote ? `<div class="quote">"${escapeHtml(result.quote)}"</div>` : ""}
      ${uiConfig.showResultDebug && result.hashtags
        ? `<div class="muted">${escapeHtml(result.hashtags)}</div>`
        : ""}
    </div>
  `;

  const moreBtn = card.querySelector("[data-more-like]");
  if (moreBtn) {
    moreBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      fetchMoreLikeThis(result);
    });
  }
  return card;
}

function createResourceCard(result) {
  const card = document.createElement("div");
  card.className = "result-card handout-card";
  card.addEventListener("click", () => {
    if (!result.download_url) return;
    window.open(result.download_url, "_blank", "noopener");
  });

  let tagsArray = [];
  if (Array.isArray(result.tags)) tagsArray = result.tags;
  else if (typeof result.tags === "string") {
    tagsArray = result.tags.split(",").map((t) => t.trim()).filter(Boolean);
  }
  const tagsHtml = tagsArray
    .slice(0, 6)
    .map((tag) => `<span class="pill">${escapeHtml(tag)}</span>`)
    .join("");
  const description = result.description || "No description available";

  card.innerHTML = `
    <div class="card-body">
      <div class="handout-title">${escapeHtml(result.title)}</div>
      ${uiConfig.showResultDebug
        ? `<div class="meta-row"><span class="pill">${escapeHtml((result.file_type || "file").toUpperCase())}</span></div>`
        : ""}
      <div class="summary">${escapeHtml(description)}</div>
      ${uiConfig.showResultDebug && tagsHtml
        ? `<div class="meta-row">${tagsHtml}</div>`
        : ""}
      <div class="handout-open">Tap to open →</div>
      <div
        class="media-action-bar"
        style="background: transparent; border-bottom: 0; padding: 0; margin-top: 12px;"
      >
        <button type="button" class="media-action-btn handout-share-btn">
          <span class="media-action-icon" aria-hidden="true">↗</span>
          Share
        </button>
        <button type="button" class="media-action-btn handout-copy-btn">
          <span class="media-action-icon" aria-hidden="true">⧉</span>
          Copy link
        </button>
      </div>
    </div>
  `;

  const shareBtn = card.querySelector(".handout-share-btn");
  shareBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    shareOrCopy({ url: result.download_url, title: result.title });
  });

  const copyBtn = card.querySelector(".handout-copy-btn");
  copyBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    copyLink(result.download_url);
  });

  return card;
}

function displayResults(results, mode) {
  const container = $("exploreResults");
  container.innerHTML = "";
  const ordered = [...results].sort((a, b) => (b.confidence || 0) - (a.confidence || 0));
  if (mode === "videos") {
    currentVideoResults = ordered;
    modeCache.videos = {
      ...(modeCache.videos || emptyVideoCache()),
      query: $("query").value,
      results: ordered,
      relatedViewActive,
      relatedSeed,
      searchSnapshot,
      engagedSeed,
      lastOpenedResult,
    };
  } else {
    currentHandoutResults = ordered;
    modeCache.resources = {
      query: $("query").value,
      results: ordered,
    };
  }
  ordered.forEach((result) => {
    container.appendChild(mode === "videos" ? createVideoCard(result) : createResourceCard(result));
  });
  updateChipVisibility();
  updateIdleState();
}

function openVideo(result) {
  lastOpenedResult = result;
  openPlayer({
    videoId: result.video_id || extractVideoId(result.url),
    title: result.video_title,
    sectionTitle: result.section_title,
    timestamp: result.timestamp,
    url: result.url,
    onClose: () => {
      if (
        uiConfig.enableMoreLikeThis &&
        lastOpenedResult &&
        searchMode === "videos" &&
        lastOpenedResult.result_kind !== "video" &&
        Boolean((lastOpenedResult.timestamp || "").trim())
      ) {
        engagedSeed = lastOpenedResult;
        lastOpenedResult = null;
        if (currentVideoResults.length > 0) displayResults(currentVideoResults, "videos");
      } else {
        lastOpenedResult = null;
      }
    },
  });
}

async function performSearch() {
  const mode = searchMode;
  const query = $("query").value.trim();
  if (mode === "videos") {
    clearRelatedState();
  }
  hideCatalogBanner();
  if (mode === "videos") {
    currentVideoResults = [];
    modeCache.videos = { ...emptyVideoCache(), query };
  } else {
    currentHandoutResults = [];
    modeCache.resources = { query, results: [] };
  }
  $("exploreResults").innerHTML = "";
  hideMessages();
  $("exploreNoResults").querySelector("p").textContent =
    "No results found. Try a different search query.";
  updateChipVisibility();
  updateIdleState();

  if (!query) return;

  $("exploreLoading").hidden = false;
  $("exploreLoadingText").textContent =
    mode === "videos" ? "Searching for relevant videos…" : "Searching handouts…";
  updateIdleState();

  try {
    const { data } = await fetchJson(
      EXPLORE_API_URL,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query,
          mode,
          top_k: 5,
          limit: 100,
          offset: 0,
        }),
      },
      {
        onSlow: () => {
          if (searchMode !== mode) return;
          if ($("exploreLoadingText").textContent === API_MESSAGES.retrying) return;
          $("exploreLoadingText").textContent = API_MESSAGES.takingLonger;
        },
        onRetry: () => {
          if (searchMode !== mode) return;
          $("exploreLoadingText").textContent = API_MESSAGES.retrying;
        },
      }
    );
    const results = data.results || [];
    if (mode === "videos") {
      modeCache.videos = {
        ...emptyVideoCache(),
        query,
        results: [...results],
      };
      currentVideoResults = [...results];
    } else {
      modeCache.resources = { query, results: [...results] };
      currentHandoutResults = [...results];
    }
    if (searchMode !== mode) return;

    $("exploreLoading").hidden = true;
    if (data.intent === "list_catalog") {
      showCatalogBanner(mode, data.total);
      $("exploreLoadingText").textContent =
        mode === "videos" ? "Loading videos…" : "Loading handouts…";
    }
    if (results.length > 0) {
      displayResults(results, mode);
    } else {
      $("exploreResults").innerHTML = "";
      $("exploreNoResults").hidden = false;
      if (data.intent === "list_catalog") {
        $("exploreNoResults").querySelector("p").textContent =
          mode === "resources"
            ? "No handouts are available yet."
            : "No videos are available yet.";
      }
      updateIdleState();
    }
  } catch (err) {
    if (searchMode !== mode) return;
    $("exploreLoading").hidden = true;
    showError(API_MESSAGES.requestFailed, { onRetry: performSearch });
  }
}

async function fetchMoreLikeThis(seed) {
  if (!uiConfig.enableMoreLikeThis || !seed) return;
  const videoId = seed.video_id || extractVideoId(seed.url || "");
  if (!seed.chroma_id && (!videoId || !seed.timestamp)) {
    showError("Couldn't find similar clips for this result.");
    return;
  }
  if (!relatedViewActive) {
    searchSnapshot = { query: $("query").value, results: [...currentVideoResults] };
  }
  const snapshot = searchSnapshot;
  hideMessages();
  hideCatalogBanner();
  $("exploreLoading").hidden = false;
  $("exploreLoadingText").textContent = "Finding similar clips…";
  updateIdleState();

  const body = seed.chroma_id
    ? { id: seed.chroma_id, top_k: 5 }
    : { video_id: videoId, timestamp: seed.timestamp, top_k: 5 };

  try {
    const { data } = await fetchJson(
      RELATED_API_URL,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
      {
        onSlow: () => {
          if (searchMode !== "videos") return;
          if ($("exploreLoadingText").textContent === API_MESSAGES.retrying) return;
          $("exploreLoadingText").textContent = API_MESSAGES.takingLonger;
        },
        onRetry: () => {
          if (searchMode !== "videos") return;
          $("exploreLoadingText").textContent = API_MESSAGES.retrying;
        },
      }
    );
    const results = data.results || [];
    const seedForBanner = data.seed || seed;
    modeCache.videos = {
      query: (snapshot && snapshot.query) || $("query").value,
      results: [...results],
      relatedViewActive: true,
      relatedSeed: seedForBanner,
      searchSnapshot: snapshot,
      engagedSeed: null,
      lastOpenedResult: null,
    };
    if (searchMode !== "videos") return;

    $("exploreLoading").hidden = true;
    relatedViewActive = true;
    searchSnapshot = snapshot;
    engagedSeed = null;
    showRelatedBanner(seedForBanner);
    updateChipVisibility();
    if (results.length > 0) {
      displayResults(results, "videos");
    } else {
      currentVideoResults = [];
      $("exploreResults").innerHTML = "";
      $("exploreNoResults").hidden = false;
      $("exploreNoResults").querySelector("p").textContent = "No similar segments found.";
      updateIdleState();
    }
  } catch (err) {
    if (searchMode !== "videos") return;
    $("exploreLoading").hidden = true;
    showError(API_MESSAGES.requestFailed, {
      onRetry: () => fetchMoreLikeThis(seed),
    });
  }
}

function backToSearch() {
  if (!searchSnapshot) {
    clearRelatedState();
    updateChipVisibility();
    updateIdleState();
    return;
  }
  const snapshot = searchSnapshot;
  relatedViewActive = false;
  relatedSeed = null;
  searchSnapshot = null;
  engagedSeed = null;
  lastOpenedResult = null;
  hideRelatedBanner();
  $("query").value = snapshot.query;
  hideMessages();
  $("exploreNoResults").querySelector("p").textContent =
    "No results found. Try a different search query.";
  if (snapshot.results && snapshot.results.length > 0) {
    displayResults(snapshot.results, "videos");
  } else {
    currentVideoResults = [];
    modeCache.videos = { ...emptyVideoCache(), query: snapshot.query };
    $("exploreResults").innerHTML = "";
    updateIdleState();
  }
  updateClearButton();
}

export function initExplore() {
  if (bound) return;
  bound = true;
  $("btnVideos").addEventListener("click", () => switchContext("videos"));
  $("btnHandouts").addEventListener("click", () => switchContext("resources"));
  $("query").addEventListener("keydown", (e) => {
    if (e.key === "Enter") performSearch();
  });
  $("query").addEventListener("input", updateClearButton);
  $("clearQuery").addEventListener("click", () => {
    $("query").value = "";
    if (searchMode === "videos") {
      clearRelatedState();
      currentVideoResults = [];
      modeCache.videos = emptyVideoCache();
    } else {
      currentHandoutResults = [];
      modeCache.resources = emptyHandoutCache();
    }
    hideCatalogBanner();
    $("exploreResults").innerHTML = "";
    hideMessages();
    updateClearButton();
    updateIdleState();
  });
  $("backToSearchBtn").addEventListener("click", backToSearch);
  $("query").placeholder = PLACEHOLDERS[searchMode];
  renderExamplePrompts();
  updateIdleState();
  loadUiConfig();
}

export function showExplore(params) {
  initExplore();
  const q = params.get("q");
  if (q && q.trim() && $("query").value.trim() !== q.trim()) {
    $("query").value = q.trim();
    updateClearButton();
    performSearch();
  } else {
    updateIdleState();
  }
}
