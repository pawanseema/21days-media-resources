import { API_MESSAGES, escapeHtml, fetchJson } from "./api.js";

const WISDOM_URL = "/api/wisdom/topics";
let topicsCache = null;

function renderTopics(data) {
  const panel = document.getElementById("panel-wisdom");
  const heading = data.heading || "Meditation wisdom";
  const subtitle =
    data.subtitle || "Foundational Sahaja Yoga topics to deepen your attention.";
  const topics = Array.isArray(data.topics) ? data.topics : [];

  panel.innerHTML = `
    <h2 class="section-heading" style="margin-top:4px">${escapeHtml(heading)}</h2>
    <p class="section-sub">${escapeHtml(subtitle)}</p>
    <div id="wisdomList">
      ${topics
        .map(
          (topic) => `
        <details class="wisdom-card">
          <summary>
            <div class="wisdom-accent"></div>
            <div class="wisdom-card-head">
              ${topic.accent_label ? `<div class="wisdom-label">${escapeHtml(topic.accent_label)}</div>` : ""}
              <h3>${escapeHtml(topic.title)}</h3>
              <p class="muted">${escapeHtml(topic.subtitle || "")}</p>
            </div>
            <span class="wisdom-chevron" aria-hidden="true"></span>
          </summary>
          <div class="wisdom-card-body">${escapeHtml(topic.body || "")}</div>
        </details>`
        )
        .join("")}
    </div>
  `;
}

export async function showWisdom() {
  if (topicsCache) {
    renderTopics(topicsCache);
    return;
  }
  const panel = document.getElementById("panel-wisdom");
  const setLoading = (text) => {
    panel.innerHTML = `<div class="panel-status"><div class="spinner"></div><p>${escapeHtml(text)}</p></div>`;
  };
  setLoading("Loading wisdom topics…");
  try {
    const { data } = await fetchJson(WISDOM_URL, {}, {
      onRetry: () => setLoading(API_MESSAGES.retrying),
    });
    topicsCache = data;
    renderTopics(data);
  } catch (err) {
    panel.innerHTML = `
      <div class="panel-status">
        <p>${escapeHtml(API_MESSAGES.requestFailed)}</p>
        <button type="button" class="btn secondary" id="wisdomRetry">Retry</button>
      </div>
    `;
    document.getElementById("wisdomRetry").addEventListener("click", () => {
      topicsCache = null;
      showWisdom();
    });
  }
}
