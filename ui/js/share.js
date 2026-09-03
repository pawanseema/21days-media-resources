/** Share / copy helpers + lightweight toast for Explore UI. */

let toastTimer = null;

export function showToast(message) {
  let el = document.getElementById("appToast");
  if (!el) {
    el = document.createElement("div");
    el.id = "appToast";
    el.className = "app-toast";
    el.setAttribute("role", "status");
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.classList.add("visible");
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.classList.remove("visible");
  }, 2200);
}

export async function copyText(text) {
  const value = (text || "").trim();
  if (!value) return false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch (_) {
    /* fall through */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = value;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (_) {
    return false;
  }
}

export async function shareOrCopy({ url, title = "" } = {}) {
  const link = (url || "").trim();
  if (!link) return;
  const label = (title || "").trim() || "21Days";
  if (typeof navigator.share === "function") {
    try {
      await navigator.share({ title: label, text: label, url: link });
      return;
    } catch (err) {
      if (err && err.name === "AbortError") return;
      /* fall through to copy */
    }
  }
  const ok = await copyText(link);
  showToast(ok ? "Link copied" : "Couldn't copy link");
}

export async function copyLink(url) {
  const ok = await copyText(url);
  showToast(ok ? "Link copied" : "Couldn't copy link");
}
