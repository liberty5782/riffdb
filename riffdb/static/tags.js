// Chip-style tag input. <div data-chip-input data-name="tags" data-value="a,b">
// renders chips + a text box and keeps a hidden comma-separated input in sync.
(() => {
  function init(root) {
    if (root.dataset.chipReady) return;
    root.dataset.chipReady = "1";
    const hidden = Object.assign(document.createElement("input"), { type: "hidden", name: root.dataset.name });
    const text = Object.assign(document.createElement("input"), {
      type: "text", placeholder: "add tag…", autocomplete: "off",
    });
    text.setAttribute("list", "tag-options");
    text.className = "chip-text";
    const chips = document.createElement("span");
    chips.className = "chips";
    root.append(chips, text, hidden);

    let tags = (root.dataset.value || "").split(",").map((t) => t.trim()).filter(Boolean);

    function render(notify) {
      chips.replaceChildren(...tags.map((t) => {
        const chip = document.createElement("span");
        chip.className = "chip";
        chip.textContent = t;
        const x = Object.assign(document.createElement("button"), { type: "button", textContent: "×", title: "remove" });
        x.addEventListener("click", () => { tags = tags.filter((v) => v !== t); render(true); });
        chip.append(x);
        return chip;
      }));
      hidden.value = tags.join(",");
      if (notify) hidden.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function add(raw) {
      let changed = false;
      for (const part of raw.split(",")) {
        const t = part.trim().toLowerCase().replace(/\s+/g, " ");
        if (t && !tags.includes(t)) { tags.push(t); changed = true; }
      }
      if (changed) render(true);
    }

    text.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === ",") {
        if (!text.value.trim()) return; // let Enter submit the form when the box is empty
        e.preventDefault();
        add(text.value);
        text.value = "";
      } else if (e.key === "Backspace" && !text.value && tags.length) {
        tags.pop();
        render(true);
      }
    });
    // Picking from the datalist fires "input" with the full value; commit it.
    text.addEventListener("input", (e) => {
      e.stopPropagation(); // don't trigger live search on every keystroke of a tag
      if (e.inputType === "insertReplacementText" || e.inputType === undefined) {
        add(text.value);
        text.value = "";
      }
    });
    // Don't lose a half-typed tag when the form is submitted.
    root.closest("form")?.addEventListener("submit", () => {
      if (text.value.trim()) { add(text.value); text.value = ""; }
    }, true);
    text.addEventListener("change", (e) => e.stopPropagation());

    root.addTag = add;
    render(false);
  }

  const initAll = (el) => el.querySelectorAll("[data-chip-input]").forEach(init);
  // htmx:load also fires for content swapped in later (e.g. the tag queue's editor).
  document.addEventListener("htmx:load", (e) => initAll(e.target));
  document.addEventListener("DOMContentLoaded", () => {
    initAll(document);
    // Tag cloud on the browse page adds to the filter chips.
    document.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-add-tag]");
      if (!btn) return;
      btn.closest("form")?.querySelector("[data-chip-input]")?.addTag(btn.dataset.addTag);
    });
  });
})();
