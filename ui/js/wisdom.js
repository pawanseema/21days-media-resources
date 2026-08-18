import { escapeHtml, fetchJson } from "./api.js";

const WISDOM_URL = "/api/wisdom/topics";
let topicsCache = null;

function closeWisdomModal() {
  const modal = document.getElementById("wisdomModal");
  modal.classList.remove("open");
  document.body.style.overflow = "";
}

function openWisdomModal(topic) {
  document.getElementById("wisdomModalTitle").textContent = topic.title || "";
  document.getElementById("wisdomModalSubtitle").textContent = topic.subtitle || "";
  document.getElementById("wisdomModalBody").textContent = topic.body || "";
  document.getElementById("wisdomModal").classList.add("open");
  document.body.style.overflow = "hidden";
}

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
          (topic, index) => `
        <article class="wisdom-card" data-index="${index}">
          <div class="wisdom-accent"></div>
          <div>
            ${topic.accent_label ? `<div class="wisdom-label">${escapeHtml(topic.accent_label)}</div>` : ""}
            <h3 style="margin:4px 0">${escapeHtml(topic.title)}</h3>
            <p class="muted" style="margin:0">${escapeHtml(topic.subtitle || "")}</p>
          </div>
        </article>`
        )
        .join("")}
    </div>
  `;

  panel.querySelectorAll(".wisdom-card").forEach((card) => {
    card.addEventListener("click", () => {
      const topic = topics[Number(card.dataset.index)];
      if (topic) openWisdomModal(topic);
    });
  });
}

export function initWisdomModal() {
  document.getElementById("wisdomClose").addEventListener("click", closeWisdomModal);
  document.getElementById("wisdomModal").addEventListener("click", (event) => {
    if (event.target.id === "wisdomModal") closeWisdomModal();
  });
}

export async function showWisdom() {
  if (topicsCache) {
    renderTopics(topicsCache);
    return;
  }
  const panel = document.getElementById("panel-wisdom");
  panel.innerHTML = `<div class="panel-status"><div class="spinner"></div><p>Loading wisdom topics…</p></div>`;
  try {
    const { data } = await fetchJson(WISDOM_URL);
    topicsCache = data;
    renderTopics(data);
  } catch (err) {
    panel.innerHTML = `
      <div class="panel-status">
        <h2>Unable to load wisdom topics</h2>
        <p class="muted">${escapeHtml(err.message || "Request failed")}</p>
        <button type="button" class="btn secondary" id="wisdomRetry">Retry</button>
      </div>
    `;
    document.getElementById("wisdomRetry").addEventListener("click", () => {
      topicsCache = null;
      showWisdom();
    });
  }
}
