import { showExplore } from "./explore.js";
import { showLive } from "./live.js";
import { showRecordings } from "./recordings.js";
import { showWisdom } from "./wisdom.js";

export const TABS = {
  live: {
    id: "live",
    label: "Live",
    subtitle: "Live and upcoming sessions",
  },
  explore: {
    id: "explore",
    label: "Explore",
    subtitle: "Search meditation videos and handouts",
  },
  recordings: {
    id: "recordings",
    label: "Recordings",
    subtitle: "Access previous session recordings",
  },
  wisdom: {
    id: "wisdom",
    label: "Wisdom",
    subtitle: "Sahaja Yoga knowledge",
  },
};

export function parseHash() {
  const raw = (location.hash || "#/live").replace(/^#/, "");
  const [pathPart, queryPart] = raw.split("?");
  const tab = (pathPart.replace(/^\//, "").split("/")[0] || "live").toLowerCase();
  const params = new URLSearchParams(queryPart || "");
  return {
    tab: TABS[tab] ? tab : "live",
    params,
  };
}

function setActive(tab) {
  document.getElementById("headerSubtitle").textContent = TABS[tab].subtitle;
  document.title = `21Days — ${TABS[tab].label}`;
  document.querySelectorAll(".main-nav a").forEach((link) => {
    link.classList.toggle("active", link.dataset.tab === tab);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.hidden = panel.id !== `panel-${tab}`;
  });
}

function canonicalHash(tab, params) {
  const query = params.toString();
  return query ? `#/${tab}?${query}` : `#/${tab}`;
}

async function applyRoute() {
  const { tab, params } = parseHash();
  const rawTab = (location.hash.replace(/^#\/?/, "").split("?")[0] || "").toLowerCase();
  if (!location.hash || !TABS[rawTab]) {
    history.replaceState(null, "", canonicalHash(tab, params));
  }
  setActive(tab);

  if (tab === "live") {
    await showLive();
  } else if (tab === "explore") {
    showExplore(params);
  } else if (tab === "recordings") {
    await showRecordings();
  } else if (tab === "wisdom") {
    await showWisdom();
  }
}

export function initShell() {
  if (!location.hash) {
    history.replaceState(null, "", "#/live");
  }
  window.addEventListener("hashchange", applyRoute);
  applyRoute();
}
