const btnLocalEnter = document.getElementById("btnLocalEnter");
const localMsg = document.getElementById("localMsg");

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || "请求失败");
  }
  return data;
}

btnLocalEnter.addEventListener("click", async () => {
  btnLocalEnter.disabled = true;
  localMsg.className = "confirm-msg";
  localMsg.textContent = "正在进入…";
  try {
    await api("/api/auth/local_enter", { method: "POST", body: "{}" });
    window.location.href = "/";
  } catch (err) {
    localMsg.className = "confirm-msg err";
    localMsg.textContent = err.message || String(err);
    btnLocalEnter.disabled = false;
  }
});

(async function boot() {
  try {
    const me = await api("/api/auth/me");
    if (me.authenticated) {
      window.location.href = "/";
    }
  } catch (_) {}
})();
