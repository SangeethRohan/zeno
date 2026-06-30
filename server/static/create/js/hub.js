(function () {
  const CREATE_TYPE_LABELS = {
    database: "Database containers",
    ubuntu: "Ubuntu servers",
    web: "Web servers"
  };

  const VIEW_MAP = {
    database: "create-db",
    ubuntu: "create-ubuntu",
    web: "create-web"
  };

  function filterContainersByCreateType(containers, type) {
    return (containers || []).filter(c => {
      if (type === "database") return c.is_user_db;
      if (type === "ubuntu") return c.is_user_server;
      if (type === "web") return c.is_user_web;
      return false;
    });
  }

  ZenoCreate.hub = {
    init(deps) {
      this.deps = deps;
      this.bindEvents();
    },

    applyFeatureNav(features) {
      const { $ } = this.deps;
      const createNav = $("nav-create");
      const hasAnyCreate = Boolean(
        features.create_database || features.create_ubuntu || features.create_web_server
      );
      if (createNav) createNav.hidden = !hasAnyCreate;

      const cardMap = {
        create_database: "create-card-database",
        create_ubuntu: "create-card-ubuntu",
        create_web_server: "create-card-web"
      };
      Object.entries(cardMap).forEach(([key, id]) => {
        const el = $(id);
        if (el) el.hidden = !features[key];
      });
    },

    applyCreateFeatureCards() {
      const features = this.deps.getCurrentUser().features || {};
      this.applyFeatureNav(features);
    },

    renderCreateAvailableList(type) {
      const { $, escapeHtml, statusClass, getContainers } = this.deps;
      const panel = $("create-available-panel");
      const list = $("create-available-list");
      const title = $("create-available-title");
      if (!panel || !list) return;

      const matches = filterContainersByCreateType(getContainers(), type);
      if (title) title.textContent = CREATE_TYPE_LABELS[type] || "Available containers";

      if (!matches.length) {
        list.innerHTML = '<div class="empty">No containers found for this template.</div>';
      } else {
        list.innerHTML = matches.map(c => `
          <div class="create-available-item">
            <div>
              <div>${escapeHtml(c.name)}</div>
              <div class="create-available-meta">${escapeHtml(c.image || "")}${c.engine ? ` · ${escapeHtml(c.engine)}` : ""}</div>
            </div>
            <div style="display:flex;align-items:center;gap:8px;">
              <span class="status-dot ${escapeHtml(statusClass(c))}"></span>
              <span>${escapeHtml(c.status)}</span>
            </div>
          </div>
        `).join("");
      }
      panel.hidden = false;
    },

    bindEvents() {
      const { $, navigateToView } = this.deps;

      document.querySelectorAll("[data-create-action]").forEach(btn => {
        btn.addEventListener("click", () => {
          const type = btn.dataset.createType;
          const action = btn.dataset.createAction;
          if (!type) return;
          if (action === "show") {
            this.renderCreateAvailableList(type);
            $("create-available-panel")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
            return;
          }
          if (action === "create") {
            navigateToView(VIEW_MAP[type]);
          }
        });
      });

      $("create-available-close")?.addEventListener("click", () => {
        const panel = $("create-available-panel");
        if (panel) panel.hidden = true;
      });
    }
  };
})();
