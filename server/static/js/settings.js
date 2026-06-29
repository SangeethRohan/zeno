async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: "include", ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401) location.href = "/login";
    throw new Error(data.error || "Request failed");
  }
  return data;
}

function showMsg(el, text, ok = true) {
  if (!el) return;
  el.textContent = text;
  el.hidden = false;
  el.className = "user-msg " + (ok ? "ok" : "bad");
}

function wireNotificationToggles(s) {
  const container = document.getElementById("notification-toggles");
  if (!container) return;
  const prefs = s.alert_notifications || {};
  const labels = s.alert_notification_labels || {};
  const rules = s.alert_notification_rules || Object.keys(prefs);
  const descriptions = {
    cpu_high: "Dashboard badge when container CPU exceeds threshold",
    mem_high: "Dashboard badge when container memory exceeds threshold",
    crash_loop: "Dashboard badge when a container restarts repeatedly",
    port_failure: "Dashboard badge when a published port is unreachable"
  };
  container.innerHTML = rules.map(rule => `
    <div class="notification-row">
      <div>
        <div class="notification-row-label">${labels[rule] || rule}</div>
        <div class="notification-row-desc">${descriptions[rule] || ""}</div>
      </div>
      <label class="toggle">
        <input type="checkbox" data-rule="${rule}" ${prefs[rule] !== false ? "checked" : ""} />
        <span class="slider"></span>
      </label>
    </div>
  `).join("");
  const msg = document.getElementById("notification-msg");
  container.querySelectorAll("input[data-rule]").forEach(input => {
    input.addEventListener("change", async () => {
      const next = {};
      container.querySelectorAll("input[data-rule]").forEach(el => {
        next[el.dataset.rule] = el.checked;
      });
      try {
        const data = await api("/api/v1/account/notifications", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ alert_notifications: next })
        });
        showMsg(msg, "Notification preferences saved.", true);
        if (msg) msg.className = "user-msg ok";
      } catch (e) {
        input.checked = !input.checked;
        showMsg(msg, e.message, false);
      }
    });
  });
}

function wireTierPicker(s) {
  const panel = document.getElementById("tier-settings");
  const picker = document.getElementById("tier-picker");
  const readonly = document.getElementById("tier-readonly");
  if (!s.is_admin || !panel) return;
  panel.hidden = false;

  if (s.is_primary) {
    if (picker) picker.hidden = true;
    if (readonly) {
      readonly.hidden = false;
      readonly.textContent = `Your edition is ${s.tier}. Primary admin tier cannot be changed.`;
    }
    return;
  }

  if (!picker) return;
  picker.hidden = false;
  picker.innerHTML = (s.tiers || ["Core", "Pro", "Elite"]).map(t => `
    <button type="button" class="tier-opt ${t === s.tier ? "selected" : ""}" data-tier="${t}">${t}</button>
  `).join("");
  picker.querySelectorAll(".tier-opt").forEach(btn => {
    btn.addEventListener("click", async () => {
      const tier = btn.dataset.tier;
      const msg = document.getElementById("tier-save-msg");
      try {
        const data = await api("/api/v1/account/tier", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tier })
        });
        picker.querySelectorAll(".tier-opt").forEach(b =>
          b.classList.toggle("selected", b.dataset.tier === data.tier)
        );
        document.getElementById("tier").textContent = data.tier;
        msg.textContent = `Your admin edition is now ${data.tier}. New users still receive Core by default.`;
        msg.hidden = false;
        msg.className = "tier-save-msg ok";
      } catch (e) {
        msg.textContent = e.message;
        msg.hidden = false;
        msg.className = "tier-save-msg bad";
      }
    });
  });
}

async function loadSettings() {
  const errEl = document.getElementById("page-error");
  try {
    const s = await api("/api/v1/settings");
    document.getElementById("product").textContent = s.product;
    document.getElementById("version").textContent = s.version;
    document.getElementById("host").textContent = s.host;
    document.getElementById("mongo").textContent = s.mongo_ready ? "MongoDB connected" : "Connecting…";
    document.getElementById("tier").textContent = s.tier || "—";
    document.getElementById("role").textContent = s.role || "—";
    document.getElementById("dashboard-url").textContent = `${location.protocol}//${location.host}`;

    wireTierPicker(s);
    wireNotificationToggles(s);
  } catch (e) {
    errEl.textContent = e.message;
    errEl.style.display = "block";
  }
}

document.getElementById("change-password-btn")?.addEventListener("click", async () => {
  const msg = document.getElementById("password-msg");
  const current_password = document.getElementById("current-password").value;
  const new_password = document.getElementById("new-password").value;
  try {
    await api("/api/v1/account/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password, new_password })
    });
    document.getElementById("current-password").value = "";
    document.getElementById("new-password").value = "";
    showMsg(msg, "Password updated successfully.");
  } catch (e) {
    showMsg(msg, e.message, false);
  }
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  try {
    await api("/api/v1/logout", { method: "POST" });
  } finally {
    location.href = "/login";
  }
});

loadSettings();
