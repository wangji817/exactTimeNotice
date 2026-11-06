const btnLocalEnter = document.getElementById("btnLocalEnter");
const localMsg = document.getElementById("localMsg");
const wxStatus = document.getElementById("wxStatus");
const wxTitle = document.getElementById("wxTitle");
const wxDetail = document.getElementById("wxDetail");
const btnWakeWx = document.getElementById("btnWakeWx");

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

function renderWxStatus(data) {
  const level = data.level || (data.ok ? "ok" : "err");
  wxStatus.dataset.level = level;
  wxTitle.textContent = data.title || (data.ok ? "PC 微信已就绪" : "PC 微信不可用");
  wxDetail.textContent = data.detail || data.message || "";
  // 进程在但未确认登录、或窗口未显示时，提供唤醒
  const needWake =
    !!data.process && (!data.logged_in || (!data.window && !data.ok));
  btnWakeWx.hidden = !needWake;
  btnWakeWx.disabled = false;
  btnWakeWx.textContent = "尝试唤醒";
  // 已登录即可进入（含托盘隐藏）；登录页不允许
  const canEnter = !!data.ok && !!data.logged_in && !data.login_page;
  btnLocalEnter.disabled = !canEnter;
  btnLocalEnter.title = canEnter
    ? ""
    : data.title || "请先打开并登录 PC 微信";
}

async function refreshWxStatus(tryWake = false) {
  wxStatus.dataset.level = "warn";
  wxTitle.textContent = tryWake ? "正在尝试唤醒微信…" : "正在检测 PC 微信…";
  wxDetail.textContent = "请稍候";
  if (tryWake) {
    btnWakeWx.disabled = true;
    btnWakeWx.textContent = "唤醒中…";
  }
  try {
    const data = tryWake
      ? await api("/api/wechat/wake", { method: "POST", body: "{}" })
      : await api("/api/wechat/status");
    renderWxStatus(data);
    return data;
  } catch (err) {
    renderWxStatus({
      ok: false,
      level: "err",
      process: false,
      window: false,
      title: "检测失败",
      detail: err.message || String(err),
    });
    return null;
  }
}

btnWakeWx.addEventListener("click", async () => {
  await refreshWxStatus(true);
});

btnLocalEnter.addEventListener("click", async () => {
  btnLocalEnter.disabled = true;
  localMsg.className = "confirm-msg";
  localMsg.textContent = "正在进入…";
  try {
    // 进入前再检一次；已登录但隐藏可直接进；未确认登录时再唤醒探测
    let st = await api("/api/wechat/status");
    if (st.process && !st.logged_in && !st.login_page) {
      localMsg.textContent = "正在唤醒微信以确认登录状态…";
      st = await api("/api/wechat/wake", { method: "POST", body: "{}" });
    }
    renderWxStatus(st);
    if (!st.process) {
      throw new Error("未检测到 PC 微信进程，请先打开并登录官方微信");
    }
    if (st.login_page || (st.window && st.logged_in === false)) {
      throw new Error("微信还在登录页，请先扫码/登录成功后再进入");
    }
    if (!st.ok || !st.logged_in) {
      throw new Error(st.detail || st.message || "PC 微信未就绪");
    }
    await api("/api/auth/local_enter", { method: "POST", body: "{}" });
    window.location.href = "/";
  } catch (err) {
    localMsg.className = "confirm-msg err";
    localMsg.textContent = err.message || String(err);
    // 保持按检测结果决定是否可点
    await refreshWxStatus(false);
  }
});

(async function boot() {
  try {
    const me = await api("/api/auth/me");
    if (me.authenticated) {
      window.location.href = "/";
      return;
    }
  } catch (_) {}
  await refreshWxStatus(false);
})();
