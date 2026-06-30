(function () {
  function showBanner($, elId, msg) {
    const el = $(elId);
    if (!el) return;
    if (!msg) {
      el.style.display = "none";
      return;
    }
    el.textContent = msg;
    el.style.display = "block";
  }

  ZenoCreate.ubuntu = {
    init(deps) {
      this.deps = deps;
      this.bindEvents();
    },

    collectLanguages() {
      return [...document.querySelectorAll("#lang-grid input:checked")].map(cb => cb.value);
    },

    bindEvents() {
      const { $, api, API_PREFIX, invalidateContainerList, refreshContainers } = this.deps;

      $("submit-ubuntu-btn")?.addEventListener("click", async () => {
        showBanner($, "ubuntu-error", null);
        showBanner($, "ubuntu-success", null);
        $("ubuntu-result").innerHTML = "";

        const name = $("ubuntu-name").value.trim();
        if (!name) return showBanner($, "ubuntu-error", "Enter a server name.");

        const btn = $("submit-ubuntu-btn");
        btn.disabled = true;
        btn.textContent = "Creating…";

        try {
          const res = await api(`${API_PREFIX}/servers/ubuntu`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name,
              persistent: $("ubuntu-persistent").checked,
              languages: this.collectLanguages()
            })
          });
          const langs = res.languages?.length ? res.languages.join(", ") : "none";
          showBanner(
            $,
            "ubuntu-success",
            `Created ${res.container.name}. Languages: ${langs}. Workspace: ${res.workspace}`
          );
          $("ubuntu-result").innerHTML =
            '<div class="ok">Expand the container on the dashboard and use <b>Open terminal</b> to run commands.</div>';
          invalidateContainerList();
          refreshContainers();
        } catch (e) {
          showBanner($, "ubuntu-error", e.message);
        } finally {
          btn.disabled = false;
          btn.textContent = "Create Ubuntu server";
        }
      });
    }
  };
})();
