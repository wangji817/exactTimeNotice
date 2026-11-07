const WEEKDAYS = [
  { v: 1, t: "周一" },
  { v: 2, t: "周二" },
  { v: 3, t: "周三" },
  { v: 4, t: "周四" },
  { v: 5, t: "周五" },
  { v: 6, t: "周六" },
  { v: 7, t: "周日" },
];

const JS_CITY_OPTIONS = ["益州", "扬州", "兖州"];
const SF_CITY_OPTIONS = ["许昌", "成都", "长沙", "建业"];
const SF_MODES = ["进攻", "防守"];
const HOUR_OPTIONS_0_23 = Array.from({ length: 24 }, (_, i) => i);
const JS_TIME_OPTIONS = ["12:00", "12:30"];
const LK_TIME_OPTIONS = ["10:00", "15:00"];

let settings = null;
let zgMembers = [];

function $(id) {
  return document.getElementById(id);
}

function setChecked(id, value) {
  const el = $(id);
  if (el) el.checked = !!value;
}

function setValue(id, value) {
  const el = $(id);
  if (el) el.value = value ?? "";
}

function toast(msg, ok = true) {
  const el = $("toast");
  if (!el) return;
  el.textContent = msg;
  el.className = `toast show ${ok ? "ok" : "err"}`;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    el.classList.remove("show");
  }, 2600);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...options,
  });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("未登录");
  }
  if (!res.ok) {
    let detail = "请求失败";
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

function renderChips(container, options, selected, onChange, mapLabel) {
  container.innerHTML = "";
  options.forEach((opt) => {
    const value = typeof opt === "object" ? opt.v : opt;
    const label = mapLabel
      ? mapLabel(opt)
      : typeof opt === "object"
        ? opt.t
        : String(opt);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `chip${selected.includes(value) ? " active" : ""}`;
    btn.textContent = label;
    btn.addEventListener("click", () => {
      const next = selected.includes(value)
        ? selected.filter((x) => x !== value)
        : [...selected, value];
      onChange(next);
    });
    container.appendChild(btn);
  });
}

function renderSingleChips(container, options, selected, onChange) {
  container.innerHTML = "";
  options.forEach((opt) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `chip${selected === opt ? " active" : ""}`;
    btn.textContent = opt;
    btn.addEventListener("click", () => onChange(opt));
    container.appendChild(btn);
  });
}

function renderMembers() {
  const box = $("zgMembers");
  box.innerHTML = "";
  if (!zgMembers.length) {
    box.innerHTML = '<div class="hint">尚未添加成员</div>';
    return;
  }
  zgMembers.forEach((name, idx) => {
    const tag = document.createElement("span");
    tag.className = "member-tag";
    tag.innerHTML = `<span>${escapeHtml(name)}</span>`;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "×";
    btn.title = "移除";
    btn.addEventListener("click", () => {
      zgMembers = zgMembers.filter((_, i) => i !== idx);
      renderMembers();
    });
    tag.appendChild(btn);
    box.appendChild(tag);
  });
}

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function fillForm(data) {
  settings = data;
  const wechat = data.wechat || {};
  const qt = data.qiang_taizi || {};
  const js = data.jian_shou || {};
  const zg = data.zhen_guo || {};
  const sf = data.si_fang || {};
  const lk = data.liu_kou || {};

  setValue("groupName", wechat.group_name || "");
  setChecked("dryRun", wechat.dry_run);

  setChecked("qtEnabled", qt.enabled);
  setValue("qtStart", qt.start_hour);
  setValue("qtEnd", qt.end_hour);
  setValue("qtAdvance", qt.advance_minutes);
  setChecked("qtSkipSun24", qt.skip_sunday_24);
  setValue("qtMessage", qt.message || "");

  setChecked("jsEnabled", js.enabled);
  setValue("jsMessage", js.message || "");
  settings.jian_shou = settings.jian_shou || js;
  {
    // 只保留可选时刻；若为空（或旧配置 12/13）则默认全选 12:00、12:30
    let times = (settings.jian_shou.times || []).filter((t) =>
      JS_TIME_OPTIONS.includes(t)
    );
    if (!times.length) {
      times = [...JS_TIME_OPTIONS];
    }
    settings.jian_shou.times = times;
  }
  const bindJsTimes = () => {
    renderChips(
      $("jsTimes"),
      JS_TIME_OPTIONS,
      settings.jian_shou.times || [...JS_TIME_OPTIONS],
      (next) => {
        settings.jian_shou.times = next.slice().sort();
        bindJsTimes();
      }
    );
  };
  bindJsTimes();

  const bindJsCities = () => {
    renderChips(
      $("jsCities"),
      JS_CITY_OPTIONS,
      settings.jian_shou.cities || [],
      (next) => {
        settings.jian_shou.cities = next;
        bindJsCities();
      }
    );
  };
  bindJsCities();

  setChecked("zgEnabled", zg.enabled);
  setValue("zgAdvance", zg.advance_minutes);
  setValue("zgMessage", zg.message || "");
  zgMembers = [...(zg.members || [])];
  renderMembers();
  settings.zhen_guo = settings.zhen_guo || zg;

  const bindZgWeekdays = () => {
    renderChips(
      $("zgWeekdays"),
      WEEKDAYS,
      settings.zhen_guo.weekdays || [],
      (next) => {
        settings.zhen_guo.weekdays = next.sort((a, b) => a - b);
        bindZgWeekdays();
      }
    );
  };
  bindZgWeekdays();

  const bindZgHours = () => {
    renderChips(
      $("zgHours"),
      HOUR_OPTIONS_0_23,
      settings.zhen_guo.hours || [],
      (next) => {
        settings.zhen_guo.hours = next.sort((a, b) => a - b);
        bindZgHours();
      },
      (h) => `${String(h).padStart(2, "0")}:00`
    );
  };
  bindZgHours();

  setChecked("sfEnabled", sf.enabled);
  setValue("sfHour", sf.hour);
  setValue("sfMinute", sf.minute);
  setValue("sfAdvance", sf.advance_minutes ?? 5);
  setValue("sfMessage", sf.message || "");
  settings.si_fang = settings.si_fang || sf;

  const bindSfWeekdays = () => {
    renderChips(
      $("sfWeekdays"),
      WEEKDAYS,
      settings.si_fang.weekdays || [],
      (next) => {
        settings.si_fang.weekdays = next.sort((a, b) => a - b);
        bindSfWeekdays();
      }
    );
  };
  bindSfWeekdays();

  const bindSfMode = () => {
    renderSingleChips($("sfMode"), SF_MODES, settings.si_fang.mode, (next) => {
      settings.si_fang.mode = next;
      bindSfMode();
    });
  };
  bindSfMode();

  const bindSfCities = () => {
    renderChips(
      $("sfCities"),
      SF_CITY_OPTIONS,
      settings.si_fang.cities || [],
      (next) => {
        settings.si_fang.cities = next;
        bindSfCities();
      }
    );
  };
  bindSfCities();

  setChecked("lkEnabled", lk.enabled);
  setValue("lkAdvance", lk.advance_minutes ?? 5);
  setValue("lkMessage", lk.message || "");
  settings.liu_kou = settings.liu_kou || lk;
  {
    let times = (settings.liu_kou.times || []).filter((t) =>
      LK_TIME_OPTIONS.includes(t)
    );
    if (!times.length) {
      times = [...LK_TIME_OPTIONS];
    }
    settings.liu_kou.times = times;
  }
  const bindLkTimes = () => {
    renderChips(
      $("lkTimes"),
      LK_TIME_OPTIONS,
      settings.liu_kou.times || [...LK_TIME_OPTIONS],
      (next) => {
        settings.liu_kou.times = next.slice().sort();
        bindLkTimes();
      }
    );
  };
  bindLkTimes();

  syncFeaturePanels();
}

function syncFeaturePanels() {
  const map = [
    ["qtEnabled", "panelQt"],
    ["jsEnabled", "panelJs"],
    ["zgEnabled", "panelZg"],
    ["sfEnabled", "panelSf"],
    ["lkEnabled", "panelLk"],
  ];
  map.forEach(([toggleId, panelId]) => {
    const on = !!($(toggleId) && $(toggleId).checked);
    const panel = $(panelId);
    if (panel) panel.classList.toggle("is-off", !on);
    document.querySelectorAll(`.panel-state[data-for="${toggleId}"]`).forEach((el) => {
      el.textContent = on ? "已开启" : "已关闭";
      el.classList.toggle("on", on);
    });
  });
}

function collectForm() {
  return {
    auth: {
      server_port: (settings.auth && settings.auth.server_port) || 8765,
    },
    wechat: {
      group_name: $("groupName").value.trim(),
      dry_run: $("dryRun").checked,
      wake_hotkey:
        (settings.wechat && settings.wechat.wake_hotkey) || "ctrl+alt+w",
    },
    qiang_taizi: {
      enabled: $("qtEnabled").checked,
      advance_minutes: Number($("qtAdvance").value || 0),
      start_hour: Number($("qtStart").value || 9),
      end_hour: Number($("qtEnd").value || 24),
      skip_sunday_24: $("qtSkipSun24").checked,
      message: $("qtMessage").value,
    },
    jian_shou: {
      enabled: $("jsEnabled").checked,
      times: settings.jian_shou.times || [...JS_TIME_OPTIONS],
      cities: settings.jian_shou.cities || [],
      message: $("jsMessage").value,
    },
    zhen_guo: {
      enabled: $("zgEnabled").checked,
      weekdays: settings.zhen_guo.weekdays || [],
      hours: settings.zhen_guo.hours || [],
      advance_minutes: Number($("zgAdvance").value || 0),
      members: [...zgMembers],
      message: $("zgMessage").value,
    },
    si_fang: {
      enabled: $("sfEnabled").checked,
      weekdays: settings.si_fang.weekdays || [],
      hour: Number($("sfHour").value || 13),
      minute: Number($("sfMinute").value || 0),
      advance_minutes: Number($("sfAdvance").value || 0),
      mode: settings.si_fang.mode || "进攻",
      cities: settings.si_fang.cities || [],
      message: $("sfMessage").value,
    },
    liu_kou: {
      enabled: $("lkEnabled").checked,
      times: settings.liu_kou.times || [...LK_TIME_OPTIONS],
      advance_minutes: Number($("lkAdvance").value || 0),
      message: $("lkMessage").value,
    },
  };
}

function renderJobs(jobs) {
  const box = $("jobsList");
  if (!jobs.length) {
    box.innerHTML = '<div class="empty">当前无启用任务</div>';
    return;
  }
  box.innerHTML = jobs
    .map(
      (j) => `
      <div class="job-item">
        <strong>${escapeHtml(j.name)}</strong>
        <div class="meta">下次执行：${j.next_run || "—"} · ${escapeHtml(j.id)}</div>
      </div>`
    )
    .join("");
}

function renderLogs(logs) {
  const box = $("logsList");
  if (!logs.length) {
    box.innerHTML = '<div class="empty">暂无播报日志</div>';
    return;
  }
  box.innerHTML = logs
    .map(
      (l) => `
      <div class="log-item ${l.success ? "ok" : "fail"}">
        <strong>${escapeHtml(l.feature)}</strong>
        <div class="meta">${escapeHtml(l.time)} · ${l.success ? "成功" : "失败"} · ${escapeHtml(l.detail || "")}</div>
        <div class="meta" style="margin-top:6px;color:var(--ink);white-space:pre-wrap;">${escapeHtml(l.message)}</div>
      </div>`
    )
    .join("");
}

function setStatus(ok, text) {
  $("statusText").textContent = text;
  const dot = $("statusDot");
  dot.className = `status-dot ${ok === true ? "" : ok === false ? "err" : "warn"}`.trim();
}

async function loadUser() {
  try {
    const me = await api("/api/auth/me");
    if (!me.authenticated) {
      window.location.href = "/login";
      return;
    }
    $("userName").textContent = me.nickname || "已登录";
  } catch (_) {
    window.location.href = "/login";
  }
}

async function loadAll() {
  await loadUser();
  const data = await api("/api/settings");
  fillForm(data);
  setStatus(true, data.wechat.dry_run ? "演练模式运行中" : "实战模式运行中");
  await refreshJobs();
  await refreshLogs();
}

async function refreshJobs() {
  const data = await api("/api/jobs");
  renderJobs(data.jobs || []);
}

async function refreshLogs() {
  const data = await api("/api/logs");
  renderLogs(data.logs || []);
}

$("btnSave").addEventListener("click", async () => {
  try {
    const payload = collectForm();
    const res = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    fillForm(res.settings);
    await refreshJobs();
    toast("配置已保存，调度任务已即时生效");
    setStatus(true, res.settings.wechat.dry_run ? "演练模式运行中" : "实战模式运行中");
  } catch (err) {
    toast(err.message || String(err), false);
  }
});

$("btnReloadJobs").addEventListener("click", async () => {
  try {
    await api("/api/scheduler/reload", { method: "POST", body: "{}" });
    await refreshJobs();
    toast("任务列表已刷新");
  } catch (err) {
    toast(err.message || String(err), false);
  }
});

$("btnRefreshLogs").addEventListener("click", async () => {
  try {
    await refreshLogs();
  } catch (err) {
    toast(err.message || String(err), false);
  }
});

$("btnTestWechat").addEventListener("click", async () => {
  try {
    // 先把当前群名写入内存配置，便于测试
    await api("/api/settings", {
      method: "PATCH",
      body: JSON.stringify({
        wechat: {
          group_name: $("groupName").value.trim(),
          dry_run: $("dryRun").checked,
        },
      }),
    });
    const res = await api("/api/wechat/test", { method: "POST", body: "{}" });
    toast(res.message, !!res.ok);
    setStatus(!!res.ok, res.ok ? "微信可用" : "微信连接异常");
  } catch (err) {
    toast(err.message || String(err), false);
  }
});

$("btnPreview").addEventListener("click", async () => {
  const btn = $("btnPreview");
  if (btn.disabled) return;
  const oldText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "发送中…";
  try {
    await api("/api/settings", {
      method: "PATCH",
      body: JSON.stringify({
        wechat: {
          group_name: $("groupName").value.trim(),
          dry_run: $("dryRun").checked,
        },
      }),
    });
    toast("正在操控 PC 微信发送…");
    const log = await api("/api/broadcast/preview", {
      method: "POST",
      body: JSON.stringify({
        feature: "手动试发",
        message: "📢【三国群雄传平台司令台】连通性测试：若看到本消息，说明播报通道正常。",
        members: [],
      }),
    });
    await refreshLogs();
    toast(log.success ? "试发完成" : log.detail || "试发失败", !!log.success);
  } catch (err) {
    toast(err.message || String(err), false);
  } finally {
    btn.disabled = false;
    btn.textContent = oldText;
  }
});

$("btnAddMember").addEventListener("click", () => {
  const name = $("zgMemberInput").value.trim();
  if (!name) return;
  if (zgMembers.includes(name)) {
    toast("成员已存在", false);
    return;
  }
  zgMembers.push(name);
  $("zgMemberInput").value = "";
  renderMembers();
});

$("zgMemberInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    $("btnAddMember").click();
  }
});

$("btnLogout").addEventListener("click", async () => {
  try {
    await api("/api/auth/logout", { method: "POST", body: "{}" });
  } catch (_) {}
  window.location.href = "/login";
});

["qtEnabled", "jsEnabled", "zgEnabled", "sfEnabled", "lkEnabled"].forEach((id) => {
  const el = $(id);
  if (!el) return;
  el.addEventListener("change", syncFeaturePanels);
});

loadAll().catch((err) => {
  setStatus(false, "服务未就绪");
  toast(err.message || String(err), false);
});

setInterval(() => {
  refreshJobs().catch(() => {});
  refreshLogs().catch(() => {});
}, 15000);
