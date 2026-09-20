/**
 * More tab — expandable Today's Meditation (optional; SHOW_MORE_TAB in shell.js).
 */
import {
  ensureActiveMeditation,
  GUIDANCE_TEXT,
  loadState,
  renderMeditationCard,
  bindMeditationPlay,
  saveState,
} from "./daily_meditation.js";

export async function showMore() {
  const panel = document.getElementById("panel-more");
  if (!panel) return;

  let state = loadState();
  const expandedAttr = state.sectionExpanded === false ? "" : " open";
  panel.innerHTML = `
    <div class="more-section">
      <details class="more-details"${expandedAttr}>
        <summary>
          <span class="more-summary-title">Today's Meditation</span>
        </summary>
        <div class="more-details-body">
          <p class="more-guidance">${GUIDANCE_TEXT}</p>
          <div id="moreCardHost" class="panel-status">Loading…</div>
        </div>
      </details>
    </div>
  `;

  const details = panel.querySelector(".more-details");
  details?.addEventListener("toggle", () => {
    const next = loadState();
    next.sectionExpanded = details.open;
    saveState(next);
  });

  const host = document.getElementById("moreCardHost");

  try {
    const loaded = await ensureActiveMeditation();
    state = loaded.state;
    const card = loaded.card;
    host.className = "more-card-host";
    host.innerHTML = renderMeditationCard(card);
    bindMeditationPlay(host, state, card);
  } catch (err) {
    console.error(err);
    if (err?.code === "empty") {
      host.innerHTML = `<p class="muted">No meditation recommendation is available right now.</p>`;
    } else {
      host.innerHTML = `<p class="muted">Could not load today’s meditation.</p>`;
    }
  }
}
