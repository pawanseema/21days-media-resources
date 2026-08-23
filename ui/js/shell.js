import { showExplore } from "./explore.js";
import { showLive } from "./live.js";
import { showRecordings } from "./recordings.js";
import { showWisdom } from "./wisdom.js";

/** Flip to true to restore the Wisdom tab in the main nav. */
export const SHOW_WISDOM_TAB = false;

export const TABS = {
  live: {
    id: "live",
    label: "Upcoming",
  },
  explore: {
    id: "explore",
    label: "Explore",
  },
  recordings: {
    id: "recordings",
    label: "Recordings",
  },
  wisdom: {
    id: "wisdom",
    label: "Wisdom",
  },
};

export function parseHash() {
  const raw = (location.hash || "#/explore").replace(/^#/, "");
  const [pathPart, queryPart] = raw.split("?");
  let tab = (pathPart.replace(/^\//, "").split("/")[0] || "explore").toLowerCase();
  const params = new URLSearchParams(queryPart || "");
  if (!TABS[tab] || (tab === "wisdom" && !SHOW_WISDOM_TAB)) {
    tab = "explore";
  }
  return { tab, params };
}

function setActive(tab) {
  document.querySelectorAll(".main-nav a").forEach((link) => {
    const isWisdom = link.dataset.tab === "wisdom";
    if (isWisdom) {
      link.hidden = !SHOW_WISDOM_TAB;
    }
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
  if (
    !location.hash ||
    !TABS[rawTab] ||
    (rawTab === "wisdom" && !SHOW_WISDOM_TAB)
  ) {
    history.replaceState(null, "", canonicalHash(tab, params));
  }
  setActive(tab);

  if (tab === "live") {
    await showLive();
  } else if (tab === "explore") {
    showExplore(params);
  } else if (tab === "recordings") {
    await showRecordings();
  } else if (tab === "wisdom" && SHOW_WISDOM_TAB) {
    await showWisdom();
  }
}

export function initShell() {
  if (!location.hash) {
    history.replaceState(null, "", "#/explore");
  }
  window.addEventListener("hashchange", applyRoute);
  applyRoute();
}
