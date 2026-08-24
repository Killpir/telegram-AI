(() => {
  const ready = (fn) => {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  };

  ready(() => {
    const input = document.getElementById("broadcast-text");
    const out = document.getElementById("broadcast-preview");
    if (input && out) {
      const render = () => { out.textContent = input.value || "Предпросмотр сообщения"; };
      input.addEventListener("input", render);
      render();
    }

    document.querySelectorAll("[data-href]").forEach((node) => {
      node.addEventListener("click", (event) => {
        if (event.target.closest("a,button,input,select,textarea,label")) return;
        window.location.assign(node.dataset.href);
      });
    });

    document.querySelectorAll("[data-confirm]").forEach((node) => {
      node.addEventListener("click", (event) => {
        if (!window.confirm(node.dataset.confirm || "Подтвердить действие?")) {
          event.preventDefault();
        }
      });
    });
  });
})();
