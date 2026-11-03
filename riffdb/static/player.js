// One shared <audio> element docked at the bottom. Lives outside #results, so
// playback continues while HTMX swaps the result list.
(() => {
  const AUTO_KEY = "riffdb.autonext";
  let currentId = null;

  const $ = (id) => document.getElementById(id);

  function buttons() {
    return [...document.querySelectorAll("#results .play-btn")];
  }

  const ICON_PLAY = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>';
  const ICON_PAUSE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 5h4v14H6zm8 0h4v14h-4z"/></svg>';

  function markPlaying() {
    const player = $("player");
    for (const btn of buttons()) {
      const active = btn.dataset.id === currentId;
      btn.closest(".row")?.classList.toggle("playing", active);
      btn.innerHTML = active && !player.paused ? ICON_PAUSE : ICON_PLAY;
    }
  }

  // Long notes are clamped to two lines; only those that actually overflow get
  // the click-to-expand affordance.
  function markClamped() {
    for (const el of document.querySelectorAll("[data-clamp]")) {
      el.classList.toggle("clamped", el.classList.contains("expanded") || el.scrollHeight > el.clientHeight + 1);
    }
  }

  document.addEventListener("click", (e) => {
    const notes = e.target.closest(".row-notes.clamped");
    if (notes) notes.classList.toggle("expanded");
  });

  function play(btn) {
    const player = $("player");
    if (btn.dataset.id === currentId) {
      player.paused ? player.play() : player.pause();
      return;
    }
    // Pause any per-page player (edit page) so only one thing plays.
    document.querySelectorAll("audio").forEach((a) => a !== player && a.pause());
    currentId = btn.dataset.id;
    player.src = btn.dataset.src;
    $("dock-title").textContent = btn.dataset.title;
    $("dock").hidden = false;
    document.body.classList.add("has-dock");
    player.play().catch(() => {});
  }

  function step(delta) {
    const list = buttons();
    const i = list.findIndex((b) => b.dataset.id === currentId);
    const next = list[i + delta] ?? (i === -1 ? list[0] : null);
    if (next) {
      play(next);
      next.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const player = $("player");
    const auto = $("dock-autonext");
    try { auto.checked = localStorage.getItem(AUTO_KEY) === "1"; } catch {}
    auto.addEventListener("change", () => {
      try { localStorage.setItem(AUTO_KEY, auto.checked ? "1" : "0"); } catch {}
    });

    // Only one thing plays at a time, whichever player it's in.
    document.addEventListener("play", (e) => {
      document.querySelectorAll("audio").forEach((a) => a !== e.target && a.pause());
    }, true);

    document.addEventListener("click", (e) => {
      const btn = e.target.closest(".play-btn");
      if (btn) play(btn);
    });
    $("dock-prev").addEventListener("click", () => step(-1));
    $("dock-next").addEventListener("click", () => step(1));
    for (const ev of ["play", "pause"]) player.addEventListener(ev, markPlaying);
    player.addEventListener("ended", () => { markPlaying(); if (auto.checked) step(1); });
    document.body.addEventListener("htmx:afterSwap", () => { markPlaying(); markClamped(); });

    // Autoplay is started from JS rather than the `autoplay` attribute: htmx clones
    // the page before swapping (history snapshot), and a cloned <audio autoplay>
    // starts playing off-page where nothing can pause it.
    const autoplay = () => document.querySelectorAll("audio[data-autoplay]:not([data-started])").forEach((a) => {
      a.dataset.started = "1";
      a.play().catch(() => {}); // blocked without a prior user gesture on first load; fine
    });
    document.body.addEventListener("htmx:afterSettle", autoplay);
    autoplay();

    // Belt and braces: stop any player that's about to be swapped out.
    document.body.addEventListener("htmx:beforeSwap", (e) => {
      e.detail.target.querySelectorAll?.("audio").forEach((a) => a.pause());
    });
    markClamped();
  });
})();
