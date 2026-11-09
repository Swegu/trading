const dialog = document.createElement("dialog");
dialog.id = "confirm-dialog";
dialog.className = "modal small-modal";
dialog.innerHTML = '<div class="eyebrow">PLEASE CONFIRM</div><h2>Are you sure?</h2><p id="confirm-message" class="subheading">This action cannot be undone.</p><div class="dialog-actions"><button id="confirm-cancel" class="secondary-button" type="button">Cancel</button><button id="confirm-accept" class="primary-button" type="button">Continue</button></div>';
document.body.append(dialog);
const style = document.createElement("style");
style.textContent = ".watch-group-title{display:flex;justify-content:space-between;align-items:center;cursor:pointer;border-radius:7px;padding:8px 9px}.watch-group-title:hover,.watch-group-title.target{color:var(--accent);background:var(--surface-2)}.watch-symbol{position:relative;padding-right:3px;grid-template-columns:minmax(0,1fr) auto 22px}.remove-symbol{align-self:center;width:22px;height:22px;padding:0;border:0;border-radius:6px;background:transparent;color:var(--faint);font-size:17px;opacity:0}.watch-symbol:hover .remove-symbol,.remove-symbol:focus-visible{opacity:1}.remove-symbol:hover{background:var(--surface-3);color:var(--down)}.search-result:focus-visible,.watch-symbol:focus-visible,.heat-tile:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}#price-chart:fullscreen{width:100vw;height:100vh;background:var(--bg);padding:24px}#price-chart:fullscreen .chart-empty{height:100%}";
document.head.append(style);
document.addEventListener("click", event => {
  const mobileButton = event.target.closest(".mobile-nav [data-view]");
  if (mobileButton) {
    document.querySelectorAll(".mobile-nav [data-view]").forEach(button => button.classList.toggle("active", button === mobileButton));
  }
  if (event.target.closest("#settings-dialog .modal-foot button")) document.getElementById("settings-dialog")?.close();
});
