// Browse page, wide screens: clicking a result opens its editor in the side
// panel instead of navigating away, so the search stays put.
(() => {
  const WIDE = window.matchMedia("(min-width: 1000px)");
  let selectedId = null;

  function markSelected() {
    for (const row of document.querySelectorAll("#results .row")) {
      row.classList.toggle("selected", row.dataset.id === selectedId);
    }
  }

  document.addEventListener("click", (e) => {
    const link = e.target.closest("#results a.row-title");
    if (!link || !WIDE.matches || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    selectedId = link.closest(".row").dataset.id;
    markSelected();
    htmx.ajax("GET", link.getAttribute("href"), { target: "#panel", select: "#panel", swap: "outerHTML" });
  });

  document.body.addEventListener("htmx:afterSwap", (e) => {
    if (e.target.id === "results") return markSelected();
    // A save/delete from the panel changes titles/tags: refresh the results to match.
    // (The submitting form is already detached here, so check the swap target.)
    if (e.target.id === "panel" && e.detail.requestConfig?.verb === "post") {
      if (!document.querySelector("#panel form")) selectedId = null; // deleted or closed
      htmx.trigger("#search", "submit");
    }
  });

  // Closing the panel (its Close link loads the empty placeholder) clears the selection.
  document.body.addEventListener("htmx:afterSettle", () => {
    if (!document.querySelector("#panel form") && selectedId) { selectedId = null; markSelected(); }
  });
})();
