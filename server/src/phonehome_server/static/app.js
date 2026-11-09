// PhoneHome: small progressive enhancements. Every page works without this.
(() => {
  // Only one message plays at a time, and playing one marks it as heard.
  document.addEventListener("play", (e) => {
    const audio = e.target;
    if (!(audio instanceof HTMLAudioElement)) return;
    document.querySelectorAll("audio").forEach((a) => { if (a !== audio) a.pause(); });
    if (audio.dataset.heard === "0" && audio.dataset.id) {
      audio.dataset.heard = "1";
      fetch(`/api/messages/${audio.dataset.id}/heard`, { method: "POST" }).then((r) => {
        if (!r.ok) return;
        const card = audio.closest(".msg");
        if (card) { card.classList.remove("unheard"); card.querySelector(".new")?.remove(); }
        const badge = document.querySelector(".topbar .badge");
        if (badge) {
          const n = parseInt(badge.textContent, 10) - 1;
          if (n > 0) badge.textContent = n; else badge.remove();
        }
      }).catch(() => {});
    }
  }, true);

  // Confirm destructive actions.
  document.querySelectorAll("form[data-confirm]").forEach((f) => {
    f.addEventListener("submit", (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); });
  });

  // Fade the flash message after a while.
  const ok = document.querySelector(".flash.ok");
  if (ok) setTimeout(() => ok.classList.add("gone"), 5000);
})();
