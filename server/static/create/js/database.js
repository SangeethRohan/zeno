(function () {
  const COL_TYPES = [
    "TEXT", "VARCHAR(255)", "INTEGER", "BIGINT", "BOOLEAN", "TIMESTAMP", "DECIMAL(10,2)"
  ];

  ZenoCreate.database = {
    selectedEngine: "postgres",
    tableCount: 0,

    init(deps) {
      this.deps = deps;
      this.bindEvents();
      this.applyEngineFieldVisibility();
    },

    applyEngineFieldVisibility() {
      const { $ } = this.deps;
      const isRedis = this.selectedEngine === "redis";
      $("row-username").style.display = isRedis ? "none" : "flex";
      $("row-dbname").style.display = isRedis ? "none" : "flex";
      const tablesApplicable =
        this.selectedEngine === "postgres"
        || this.selectedEngine === "mysql"
        || this.selectedEngine === "mongo";
      $("tables-label").textContent =
        this.selectedEngine === "mongo"
          ? "Initial collections (optional)"
          : "Initial tables (optional)";
      $("tables-label").style.display = tablesApplicable ? "block" : "none";
      $("tables-container").style.display = tablesApplicable ? "block" : "none";
      $("add-table-btn").style.display = tablesApplicable ? "block" : "none";
    },

    addTableBlock() {
      const { $ } = this.deps;
      this.tableCount += 1;
      const id = `tbl_${this.tableCount}`;
      const wrap = document.createElement("div");
      wrap.className = "table-block";
      wrap.id = id;
      const isMongo = this.selectedEngine === "mongo";
      wrap.innerHTML = `
        <div class="table-block-head">
          <input type="text" placeholder="${isMongo ? "collection name" : "table name"}" class="tbl-name" />
          <button class="small-btn danger" type="button" data-remove-table="${id}">✕</button>
        </div>
        <div class="cols-holder" style="display:${isMongo ? "none" : "block"}"></div>
        <button class="ghost-btn small-btn" type="button" data-add-column="${id}" style="display:${isMongo ? "none" : "block"}">+ Add column</button>
      `;
      $("tables-container").appendChild(wrap);
      wrap.querySelector(`[data-remove-table="${id}"]`)?.addEventListener("click", () => wrap.remove());
      wrap.querySelector(`[data-add-column="${id}"]`)?.addEventListener("click", () => this.addColumnRow(id));
      if (!isMongo) this.addColumnRow(id);
    },

    addColumnRow(tableId) {
      const holder = document.querySelector(`#${tableId} .cols-holder`);
      if (!holder) return;
      const row = document.createElement("div");
      row.className = "col-row";
      const typeOptions = COL_TYPES.map(t => `<option value="${t}">${t}</option>`).join("");
      row.innerHTML = `
        <input type="text" placeholder="column name" class="col-name" />
        <select class="col-type">${typeOptions}</select>
        <button class="small-btn danger" type="button">✕</button>
      `;
      row.querySelector("button")?.addEventListener("click", () => row.remove());
      holder.appendChild(row);
    },

    collectTables() {
      const tables = [];
      document.querySelectorAll(".table-block").forEach(block => {
        const tname = block.querySelector(".tbl-name").value.trim();
        if (!tname) return;
        const columns = [];
        block.querySelectorAll(".col-row").forEach(row => {
          const cname = row.querySelector(".col-name").value.trim();
          const ctype = row.querySelector(".col-type").value;
          if (cname) columns.push({ name: cname, type: ctype });
        });
        tables.push({ name: tname, columns });
      });
      return tables;
    },

    showCreateError(msg) {
      const el = this.deps.$("create-error");
      if (!msg) {
        el.style.display = "none";
        return;
      }
      el.textContent = msg;
      el.style.display = "block";
    },

    showCreateSuccess(msg) {
      const el = this.deps.$("create-success");
      if (!msg) {
        el.style.display = "none";
        return;
      }
      el.textContent = msg;
      el.style.display = "block";
    },

    parseHostPort(raw) {
      if (raw === "" || raw == null) return null;
      const port = Number.parseInt(String(raw).trim(), 10);
      if (!Number.isFinite(port)) return null;
      return port;
    },

    validateHostPort(raw) {
      const trimmed = String(raw ?? "").trim();
      if (!trimmed) return null;
      const port = this.parseHostPort(trimmed);
      if (port == null) return "Enter a valid port number or leave empty for auto-assign.";
      if (port < 1) return "Port cannot be zero or negative.";
      if (port < 1024) return "Port must be at least 1024.";
      if (port > 65535) return "Port cannot exceed 65535.";
      if (this.isPortUsedByContainer(port)) {
        return `Port ${port} is already used by another container on this host.`;
      }
      return null;
    },

    isPortUsedByContainer(port) {
      const needle = `${port}->`;
      return this.deps.getContainers().some(c =>
        (c.ports || []).some(binding => binding.startsWith(needle))
      );
    },

    updatePortHint() {
      const { $ } = this.deps;
      const hint = $("port-hint");
      const input = $("f-port");
      if (!hint || !input) return;

      const err = this.validateHostPort(input.value);
      if (err) {
        hint.textContent = err;
        hint.style.color = "var(--red)";
      } else if (input.value.trim() === "") {
        hint.textContent = "Leave empty to auto-pick a free port (5500–5999).";
        hint.style.color = "var(--muted)";
      } else {
        hint.textContent = "Port looks available.";
        hint.style.color = "var(--green)";
      }
    },

    bindEvents() {
      const { $, api, API_PREFIX, invalidateContainerList, refreshContainers } = this.deps;

      document.querySelectorAll("#engine-grid .engine-opt").forEach(opt => {
        opt.addEventListener("click", () => {
          document.querySelectorAll("#engine-grid .engine-opt").forEach(o => o.classList.remove("selected"));
          opt.classList.add("selected");
          this.selectedEngine = opt.dataset.engine;
          this.applyEngineFieldVisibility();
        });
      });

      $("add-table-btn")?.addEventListener("click", () => this.addTableBlock());

      $("f-port")?.addEventListener("input", () => {
        const input = $("f-port");
        if (!input) return;
        if (input.value !== "" && Number(input.value) < 0) {
          input.value = String(Math.abs(Number(input.value)));
        }
        this.updatePortHint();
      });

      $("submit-db-btn")?.addEventListener("click", async () => {
        this.showCreateError(null);
        this.showCreateSuccess(null);
        $("result-block").innerHTML = "";

        const rawPort = $("f-port").value.trim();
        const portError = this.validateHostPort(rawPort);
        if (portError) return this.showCreateError(portError);

        const payload = {
          engine: this.selectedEngine,
          name: $("f-name").value.trim(),
          username: $("f-username").value.trim(),
          password: $("f-password").value,
          db_name: $("f-dbname").value.trim(),
          tables: this.collectTables(),
          persistent: $("f-persistent").checked
        };
        const parsedPort = this.parseHostPort(rawPort);
        if (parsedPort != null) payload.host_port = parsedPort;

        if (!payload.name) return this.showCreateError("Give it an identifier first.");

        const btn = $("submit-db-btn");
        btn.disabled = true;
        btn.textContent = "Creating…";

        try {
          const res = await api(`${API_PREFIX}/databases`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
          });
          const assignedPort = parsedPort ?? (() => {
            const binding = res.container?.ports?.[0] || "";
            const match = binding.match(/^(\d+)->/);
            return match ? Number(match[1]) : null;
          })();
          this.showCreateSuccess(
            `Created zeno_userdb_${payload.name} (${this.selectedEngine}) on port ${assignedPort ?? "auto"}. `
            + (res.ready ? "It is up and responding." : res.warning || "")
          );
          if (res.tables && res.tables.length) {
            $("result-block").innerHTML = res.tables.map(t =>
              `<div class="${t.ok ? "ok" : "bad"}">${t.ok ? "✓" : "✗"} ${t.table}${t.ok ? "" : ` — ${t.detail}`}</div>`
            ).join("");
          }
          invalidateContainerList();
          refreshContainers();
        } catch (e) {
          this.showCreateError(e.message);
        } finally {
          btn.disabled = false;
          btn.textContent = "Create database";
        }
      });
    }
  };
})();
