const TIERS = ["Core", "Pro", "Elite"];
let allUsers = [];
let selectedUsernames = new Set();
let activityUsername = null;
let activitySkip = 0;
const ACTIVITY_PAGE = 50;

async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: "include", ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401) location.href = "/login";
    if (res.status === 403) location.href = "/";
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

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function fmtTs(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function fmtShortDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return "—";
  }
}

function updateBulkButtons() {
  const selectable = allUsers.filter(u => !u.is_primary);
  const n = [...selectedUsernames].filter(name =>
    selectable.some(u => u.username === name)
  ).length;
  document.getElementById("bulk-tier-btn").disabled = n === 0;
  document.getElementById("bulk-delete-btn").disabled = n === 0;
}

function updateSummary(users) {
  document.getElementById("stat-users").textContent = users.length;
  document.getElementById("stat-created").textContent = users.reduce(
    (s, u) => s + (u.containers_created || 0), 0
  );
  document.getElementById("stat-deleted").textContent = users.reduce(
    (s, u) => s + (u.containers_deleted || 0), 0
  );
  document.getElementById("stat-ops").textContent = users.reduce(
    (s, u) => s + (u.operations || 0), 0
  );
}

async function resetUserPassword(username) {
  const password = prompt(`New password for ${username} (min 4 chars):`);
  if (!password) return;
  if (password.length < 4) {
    showMsg(document.getElementById("user-msg"), "Password must be at least 4 characters.", false);
    return;
  }
  await api(`/api/v1/users/${encodeURIComponent(username)}/password`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password })
  });
  showMsg(document.getElementById("user-msg"), `Password updated for ${username}.`);
}

function renderUsersTable() {
  const tbody = document.getElementById("users-tbody");
  if (!allUsers.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty-cell">No users yet.</td></tr>';
    return;
  }

  tbody.innerHTML = allUsers.map(u => {
    const checked = selectedUsernames.has(u.username) ? "checked" : "";
    const active = activityUsername === u.username ? "row-active" : "";
    const primary = u.is_primary;
    return `
      <tr class="user-data-row ${active}" data-user="${escapeHtml(u.username)}">
        <td class="col-check">
          ${primary ? "" : `<input type="checkbox" class="user-check" data-user="${escapeHtml(u.username)}" ${checked} />`}
        </td>
        <td>
          <button type="button" class="user-link" data-view="${escapeHtml(u.username)}">${escapeHtml(u.username)}</button>
          ${primary ? '<span class="primary-badge">primary</span>' : ""}
        </td>
        <td><span class="role-pill role-${u.role}">${escapeHtml(u.role)}</span></td>
        <td>
          ${primary ? `<span class="tier-static">${escapeHtml(u.tier)}</span>` : `
          <select class="tier-select" data-user="${escapeHtml(u.username)}">
            ${TIERS.map(t => `<option value="${t}" ${t === u.tier ? "selected" : ""}>${t}</option>`).join("")}
          </select>`}
        </td>
        <td class="muted-cell">${fmtShortDate(u.created_at)}</td>
        <td class="stat-cell">${u.containers_created || 0}</td>
        <td class="stat-cell">${u.containers_deleted || 0}</td>
        <td class="stat-cell">${u.operations || 0}</td>
        <td class="row-actions">
          <button type="button" class="ghost-btn small-btn" data-pw="${escapeHtml(u.username)}">Password</button>
        </td>
      </tr>
    `;
  }).join("");

  tbody.querySelectorAll(".user-check").forEach(cb => {
    cb.addEventListener("change", () => {
      const name = cb.dataset.user;
      if (cb.checked) selectedUsernames.add(name);
      else selectedUsernames.delete(name);
      updateBulkButtons();
      syncSelectAll();
    });
  });

  tbody.querySelectorAll(".user-link").forEach(el => {
    el.addEventListener("click", e => {
      e.stopPropagation();
      viewUserActivity(el.dataset.view);
    });
  });

  tbody.querySelectorAll(".user-data-row").forEach(row => {
    row.addEventListener("click", e => {
      if (e.target.closest("button, input, select")) return;
      viewUserActivity(row.dataset.user);
    });
  });

  tbody.querySelectorAll(".tier-select").forEach(sel => {
    sel.addEventListener("change", async () => {
      const username = sel.dataset.user;
      try {
        await api(`/api/v1/users/${encodeURIComponent(username)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tier: sel.value })
        });
        const u = allUsers.find(x => x.username === username);
        if (u) u.tier = sel.value;
      } catch (err) {
        showMsg(document.getElementById("user-msg"), err.message, false);
        loadDashboard();
      }
    });
  });

  tbody.querySelectorAll("[data-pw]").forEach(btn => {
    btn.addEventListener("click", async e => {
      e.stopPropagation();
      try {
        await resetUserPassword(btn.dataset.pw);
      } catch (err) {
        showMsg(document.getElementById("user-msg"), err.message, false);
      }
    });
  });
}

function syncSelectAll() {
  const all = document.getElementById("select-all");
  if (!all) return;
  const checks = [...document.querySelectorAll(".user-check")];
  all.checked = checks.length > 0 && checks.every(c => c.checked);
  all.indeterminate = checks.some(c => c.checked) && !all.checked;
}

async function loadDashboard() {
  try {
    const data = await api("/api/v1/users/dashboard");
    allUsers = data.users || [];
    updateSummary(allUsers);
    renderUsersTable();
    if (!activityUsername && data.recent?.length) {
      renderActivityEntries(data.recent, false);
      document.getElementById("activity-title").textContent = "Recent activity";
      document.getElementById("activity-sub").textContent = "Latest operations across all users";
    }
  } catch (e) {
    document.getElementById("page-error").textContent = e.message;
    document.getElementById("page-error").style.display = "block";
  }
}

function actionLabel(action) {
  const map = {
    create: "Created",
    delete: "Deleted",
    start: "Started",
    stop: "Stopped",
    restart: "Restarted",
    exec: "CLI exec"
  };
  return map[action] || action;
}

function renderActivityEntries(entries, append) {
  const log = document.getElementById("activity-log");
  const html = entries.map(e => `
    <div class="activity-entry action-${e.action}">
      <div class="activity-entry-top">
        <span class="activity-action">${actionLabel(e.action)}</span>
        <span class="activity-user">@${escapeHtml(e.username)}</span>
        <span class="activity-ts">${fmtTs(e.ts)}</span>
      </div>
      <div class="activity-container mono">${escapeHtml(e.container || "—")}</div>
      ${e.container_image ? `<div class="activity-meta">${escapeHtml(e.container_image)}</div>` : ""}
      ${e.details ? `<div class="activity-meta">${escapeHtml(e.details)}</div>` : ""}
    </div>
  `).join("");

  if (append) log.insertAdjacentHTML("beforeend", html);
  else log.innerHTML = html || '<div class="empty-cell">No activity recorded yet.</div>';
}

async function viewUserActivity(username) {
  activityUsername = username;
  activitySkip = 0;
  document.getElementById("activity-title").textContent = `Activity — ${username}`;
  document.getElementById("activity-sub").textContent = "Container creates, deletes, and operations";
  renderUsersTable();

  const data = await api(
    `/api/v1/activity?username=${encodeURIComponent(username)}&limit=${ACTIVITY_PAGE}&skip=0`
  );
  renderActivityEntries(data.entries || [], false);
  document.getElementById("load-more-activity").hidden =
    (data.entries || []).length < ACTIVITY_PAGE;
  activitySkip = (data.entries || []).length;
}

async function loadMoreActivity() {
  const params = new URLSearchParams({
    limit: String(ACTIVITY_PAGE),
    skip: String(activitySkip)
  });
  if (activityUsername) params.set("username", activityUsername);
  const data = await api(`/api/v1/activity?${params}`);
  renderActivityEntries(data.entries || [], true);
  activitySkip += (data.entries || []).length;
  document.getElementById("load-more-activity").hidden =
    (data.entries || []).length < ACTIVITY_PAGE;
}

document.getElementById("select-all")?.addEventListener("change", e => {
  const checked = e.target.checked;
  document.querySelectorAll(".user-check").forEach(cb => {
    cb.checked = checked;
    const name = cb.dataset.user;
    if (checked) selectedUsernames.add(name);
    else selectedUsernames.delete(name);
  });
  updateBulkButtons();
});

document.getElementById("bulk-tier-btn")?.addEventListener("click", async () => {
  const tier = document.getElementById("bulk-tier").value;
  const usernames = [...selectedUsernames].filter(name => {
    const u = allUsers.find(x => x.username === name);
    return u && !u.is_primary;
  });
  if (!usernames.length) return;
  if (!confirm(`Set tier to ${tier} for ${usernames.length} user(s)?`)) return;
  try {
    const res = await api("/api/v1/users/bulk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "set_tier", usernames, tier })
    });
    showMsg(document.getElementById("user-msg"),
      `Updated tier for ${res.updated.length} user(s).`, true);
    selectedUsernames.clear();
    loadDashboard();
  } catch (e) {
    showMsg(document.getElementById("user-msg"), e.message, false);
  }
});

document.getElementById("bulk-delete-btn")?.addEventListener("click", async () => {
  const usernames = [...selectedUsernames].filter(name => {
    const u = allUsers.find(x => x.username === name);
    return u && !u.is_primary;
  });
  if (!usernames.length) return;
  if (!confirm(`Delete ${usernames.length} user(s)? This cannot be undone.`)) return;
  try {
    const res = await api("/api/v1/users/bulk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "delete", usernames })
    });
    let msg = `Deleted ${res.deleted.length} user(s).`;
    if (res.errors?.length) msg += ` ${res.errors.length} failed.`;
    showMsg(document.getElementById("user-msg"), msg, !res.errors?.length);
    selectedUsernames.clear();
    loadDashboard();
  } catch (e) {
    showMsg(document.getElementById("user-msg"), e.message, false);
  }
});

document.getElementById("activity-all-btn")?.addEventListener("click", async () => {
  activityUsername = null;
  activitySkip = 0;
  document.getElementById("activity-title").textContent = "Recent activity";
  document.getElementById("activity-sub").textContent = "Latest operations across all users";
  const data = await api(`/api/v1/activity?limit=${ACTIVITY_PAGE}&skip=0`);
  renderActivityEntries(data.entries || [], false);
  document.getElementById("load-more-activity").hidden =
    (data.entries || []).length < ACTIVITY_PAGE;
  activitySkip = (data.entries || []).length;
  renderUsersTable();
});

document.getElementById("load-more-activity")?.addEventListener("click", loadMoreActivity);

document.getElementById("create-user-btn")?.addEventListener("click", async () => {
  const msg = document.getElementById("user-msg");
  const username = document.getElementById("new-username").value.trim();
  const password = document.getElementById("new-password").value;
  const role = document.getElementById("new-role").value;
  const tier = document.getElementById("new-tier").value;
  try {
    await api("/api/v1/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, role, tier })
    });
    document.getElementById("new-username").value = "";
    document.getElementById("new-password").value = "";
    showMsg(msg, `Created user ${username} (${tier}).`);
    loadDashboard();
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

loadDashboard();
