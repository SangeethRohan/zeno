const form = document.getElementById("authForm");
const errEl = document.getElementById("err");
const submitBtn = document.getElementById("submit-btn");
const modeToggle = document.getElementById("mode-toggle");
const authSub = document.getElementById("auth-sub");
const usernameInput = document.getElementById("username");
const passwordInput = document.getElementById("password");

let mode = "login";

function showError(msg) {
  if (!msg) {
    errEl.hidden = true;
    errEl.textContent = "";
    return;
  }
  errEl.textContent = msg;
  errEl.hidden = false;
}

function setMode(next) {
  mode = next;
  const isLogin = mode === "login";

  authSub.textContent = isLogin
    ? "Dashboard authentication"
    : "Create a new user account";

  submitBtn.textContent = isLogin ? "Login" : "Create account";
  modeToggle.textContent = isLogin ? "Create account" : "Back to login";

  passwordInput.autocomplete = isLogin ? "current-password" : "new-password";
  usernameInput.focus();

  showError(null);
}

modeToggle?.addEventListener("click", () => {
  setMode(mode === "login" ? "register" : "login");
});

async function checkExistingSession() {
  try {
    const res = await fetch("/api/v1/me", { credentials: "include" });
    if (res.ok) location.href = "/";
  } catch {
    /* stay on login */
  }
}

form.addEventListener("submit", async e => {
  e.preventDefault();
  showError(null);

  const username = usernameInput.value.trim();
  const password = passwordInput.value;

  if (!username || !password) {
    showError("Enter both username and password.");
    return;
  }

  const isLogin = mode === "login";
  const endpoint = isLogin ? "/api/v1/login" : "/api/v1/register";
  const pendingLabel = isLogin ? "Signing in…" : "Creating…";

  submitBtn.disabled = true;
  submitBtn.textContent = pendingLabel;

  try {
    const res = await fetch(endpoint, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password })
    });

    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      throw new Error(
        data.error || (isLogin ? "Invalid username or password" : "Could not create account")
      );
    }

    if (isLogin) {
      location.href = "/";
      return;
    }

    setMode("login");
    showError(`Account created. Sign in as ${username}.`);
    passwordInput.value = password;
  } catch (err) {
    showError(err.message);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = mode === "login" ? "Login" : "Create account";
  }
});

checkExistingSession();
