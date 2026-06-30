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

  function parseHostPort(raw) {
    if (raw === "" || raw == null) return null;
    const port = Number.parseInt(String(raw).trim(), 10);
    if (!Number.isFinite(port)) return null;
    return port;
  }

  ZenoCreate.web = {
    selectedWebType: "nginx",

    init(deps) {
      this.deps = deps;
      this.bindEvents();
    },

    isPortUsedByContainer(port) {
      const needle = `${port}->`;
      return this.deps.getContainers().some(c =>
        (c.ports || []).some(binding => binding.startsWith(needle))
      );
    },

    bindEvents() {
      const { $, api, API_PREFIX, invalidateContainerList, refreshContainers } = this.deps;

      document.querySelectorAll("#web-type-grid .engine-opt").forEach(opt => {
        opt.addEventListener("click", () => {
          document.querySelectorAll("#web-type-grid .engine-opt").forEach(o => o.classList.remove("selected"));
          opt.classList.add("selected");
          this.selectedWebType = opt.dataset.type;
        });
      });

      $("submit-web-btn")?.addEventListener("click", async () => {
        showBanner($, "web-error", null);
        showBanner($, "web-success", null);
        $("web-result").innerHTML = "";

        const name = $("web-name").value.trim();
        if (!name) return showBanner($, "web-error", "Enter a server name.");

        const rawPort = $("web-port").value.trim();
        let hostPort = null;
        if (rawPort !== "") {
          hostPort = parseHostPort(rawPort);
          if (hostPort == null || hostPort < 1024 || hostPort > 65535) {
            return showBanner($, "web-error", "Host port must be between 1024 and 65535.");
          }
          if (this.isPortUsedByContainer(hostPort)) {
            return showBanner($, "web-error", `Port ${hostPort} is already used by another container.`);
          }
        }

        const btn = $("submit-web-btn");
        btn.disabled = true;
        btn.textContent = "Creating…";

        try {
          const payload = {
            name,
            type: this.selectedWebType,
            persistent: $("web-persistent").checked
          };
          if (hostPort != null) payload.host_port = hostPort;

          const res = await api(`${API_PREFIX}/servers/web`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
          });
          showBanner($, "web-success", `Created ${res.container.name} (${res.type}) at ${res.url}`);
          $("web-result").innerHTML =
            `<div class="ok"><a href="${res.url}" target="_blank" rel="noopener">Open ${res.url}</a></div>`;
          invalidateContainerList();
          refreshContainers();
        } catch (e) {
          showBanner($, "web-error", e.message);
        } finally {
          btn.disabled = false;
          btn.textContent = "Create web server";
        }
      });
    }
  };
})();
