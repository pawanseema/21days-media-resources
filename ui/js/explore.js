import {
  API_MESSAGES,
  escapeHtml,
  extractVideoId,
  fetchJson,
  formatClipDuration,
} from "./api.js";
import { openPlayer } from "./player.js";

const EXPLORE_API_URL = "/api/explore/query";
const UI_CONFIG_URL = "/api/ui-config";
const RELATED_API_URL = "/api/videos/related";

const PLACEHOLDERS = {
  videos: "Search meditation videos",
  resources: "Search meditation handouts",
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
let uiConfig = { showResultDebug: true, enableMoreLikeThis: true };
let currentVideoResults = [];
let lastOpenedResult = null;
let engagedSeed = null;
let relatedViewActive = false;
let searchSnapshot = null;
let bound = false;

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
  const title = (seed && seed.section_title) || "this clip";
  $("relatedBannerText").innerHTML = `Showing more like: <span>${escapeHtml(title)}</span>`;
  $("relatedBanner").classList.add("visible");
}

function clearRelatedState() {
  relatedViewActive = false;
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
}

function hideMessages() {
  $("exploreError").hidden = true;
  $("exploreError").innerHTML = "";
  $("exploreNoResults").hidden = true;
  $("exploreLoading").hidden = true;
}

function switchContext(mode) {
  if (mode === searchMode) return;
  searchMode = mode;
  $("btnVideos").classList.toggle("active", mode === "videos");
  $("btnHandouts").classList.toggle("active", mode === "resources");
  $("query").value = "";
  $("query").placeholder = PLACEHOLDERS[mode];
  clearRelatedState();
  currentVideoResults = [];
  $("exploreResults").innerHTML = "";
  hideMessages();
  $("exploreNoResults").querySelector("p").textContent =
    "No results found. Try a different search query.";
  $("exploreLoadingText").textContent =
    mode === "videos" ? "Searching for relevant videos…" : "Searching handouts…";
  hideCatalogBanner();
  updateClearButton();
  renderExamplePrompts();
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
    if (result.download_url) window.open(result.download_url, "_blank", "noopener");
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
    </div>
  `;
  return card;
}

function displayResults(results, mode) {
  const container = $("exploreResults");
  container.innerHTML = "";
  const ordered = [...results].sort((a, b) => (b.confidence || 0) - (a.confidence || 0));
  if (mode === "videos") currentVideoResults = ordered;
  ordered.forEach((result) => {
    container.appendChild(mode === "videos" ? createVideoCard(result) : createResourceCard(result));
  });
  updateChipVisibility();
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
  const query = $("query").value.trim();
  clearRelatedState();
  hideCatalogBanner();
  currentVideoResults = [];
  $("exploreResults").innerHTML = "";
  hideMessages();
  $("exploreNoResults").querySelector("p").textContent =
    "No results found. Try a different search query.";
  updateChipVisibility();

  if (!query) return;

  $("exploreLoading").hidden = false;
  $("exploreLoadingText").textContent =
    searchMode === "videos" ? "Searching for relevant videos…" : "Searching handouts…";

  try {
    const { data } = await fetchJson(
      EXPLORE_API_URL,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query,
          mode: searchMode,
          top_k: 5,
          limit: 100,
          offset: 0,
        }),
      },
      {
        onRetry: () => {
          $("exploreLoadingText").textContent = API_MESSAGES.retrying;
        },
      }
    );
    $("exploreLoading").hidden = true;
    if (data.intent === "list_catalog") {
      showCatalogBanner(searchMode, data.total);
      $("exploreLoadingText").textContent =
        searchMode === "videos" ? "Loading videos…" : "Loading handouts…";
    }
    if (data.results && data.results.length > 0) {
      displayResults(data.results, searchMode);
    } else {
      $("exploreNoResults").hidden = false;
      if (data.intent === "list_catalog") {
        $("exploreNoResults").querySelector("p").textContent =
          searchMode === "resources"
            ? "No handouts are available yet."
            : "No videos are available yet.";
      }
    }
  } catch (err) {
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
  hideMessages();
  hideCatalogBanner();
  $("exploreLoading").hidden = false;
  $("exploreLoadingText").textContent = "Finding similar clips…";

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
        onRetry: () => {
          $("exploreLoadingText").textContent = API_MESSAGES.retrying;
        },
      }
    );
    $("exploreLoading").hidden = true;
    relatedViewActive = true;
    engagedSeed = null;
    showRelatedBanner(data.seed || seed);
    updateChipVisibility();
    if (data.results && data.results.length > 0) {
      displayResults(data.results, "videos");
    } else {
      currentVideoResults = [];
      $("exploreResults").innerHTML = "";
      $("exploreNoResults").hidden = false;
      $("exploreNoResults").querySelector("p").textContent = "No similar segments found.";
    }
  } catch (err) {
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
    return;
  }
  const snapshot = searchSnapshot;
  relatedViewActive = false;
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
    $("exploreResults").innerHTML = "";
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
    clearRelatedState();
    hideCatalogBanner();
    currentVideoResults = [];
    $("exploreResults").innerHTML = "";
    hideMessages();
    updateClearButton();
  });
  $("backToSearchBtn").addEventListener("click", backToSearch);
  $("query").placeholder = PLACEHOLDERS[searchMode];
  renderExamplePrompts();
  loadUiConfig();
}

export function showExplore(params) {
  initExplore();
  const q = params.get("q");
  if (q && q.trim() && $("query").value.trim() !== q.trim()) {
    $("query").value = q.trim();
    updateClearButton();
    performSearch();
  }
}
