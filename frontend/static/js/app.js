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
const SF_CITY_OPTIONS = ["许昌", "成都", "长沙", "建业", "洛阳"];
const SF_MODES = ["进攻", "防守"];
const HOUR_OPTIONS_0_23 = Array.from({ length: 24 }, (_, i) => i);
const JS_TIME_OPTIONS = ["12:00", "12:30"];
const LK_TIME_OPTIONS = ["10:00", "15:00"];
const DY_TIME_OPTIONS = ["18:00"];

const MENTION_PREFIXES = ["qt", "zg", "sf", "qy", "wc", "tz", "js", "lk", "dy"];

let settings = null;
const membersByPrefix = Object.fromEntries(MENTION_PREFIXES.map((p) => [p, []]));
let seasonTimer = null;

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
  toast._t = setTimeout(() => el.classList.remove("show"), 2600);
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

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function toLocalInput(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) {
    const m = String(iso).match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/);
    return m ? `${m[1]}T${m[2]}` : "";
  }
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fromLocalInput(value) {
  const text = (value || "").trim();
  if (!text) return "";
  return text.length === 16 ? `${text}:00` : text;
}

function setDateTime(baseId, iso) {
  const local = toLocalInput(iso || "");
  const [datePart, timePart] = local ? local.split("T") : ["", ""];
  setValue(`${baseId}_date`, datePart || "");
  setValue(`${baseId}_time`, (timePart || "").slice(0, 5));
}

function getDateTime(baseId) {
  const datePart = (($(`${baseId}_date`) && $(`${baseId}_date`).value) || "").trim();
  if (!datePart) return "";
  const timePart =
    (($(`${baseId}_time`) && $(`${baseId}_time`).value) || "").trim() || "00:00";
  return fromLocalInput(`${datePart}T${timePart.slice(0, 5)}`);
}

function renderChips(container, options, selected, onChange, mapLabel) {
  if (!container) return;
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
  if (!container) return;
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

function renderMembers(prefix) {
  const box = $(`${prefix}Members`);
  if (!box) return;
  const list = membersByPrefix[prefix] || [];
  box.innerHTML = "";
  if (!list.length) {
    box.innerHTML = '<div class="hint">尚未添加成员</div>';
    return;
  }
  list.forEach((name, idx) => {
    const tag = document.createElement("span");
    tag.className = "member-tag";
    tag.innerHTML = `<span>${escapeHtml(name)}</span>`;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "×";
    btn.title = "移除";
    btn.addEventListener("click", () => {
      membersByPrefix[prefix] = list.filter((_, i) => i !== idx);
      renderMembers(prefix);
    });
    tag.appendChild(btn);
    box.appendChild(tag);
  });
}

function fillMentions(prefix, cfg) {
  setChecked(`${prefix}AtEveryone`, !!(cfg && cfg.at_everyone));
  membersByPrefix[prefix] = [...((cfg && cfg.members) || [])];
  renderMembers(prefix);
}

function collectMentions(prefix) {
  return {
    at_everyone: !!($(`${prefix}AtEveryone`) && $(`${prefix}AtEveryone`).checked),
    members: [...(membersByPrefix[prefix] || [])],
  };
}

function bindWeekdays(containerId, storeKey, bag) {
  const bind = () => {
    renderChips($(containerId), WEEKDAYS, bag[storeKey] || [], (next) => {
      bag[storeKey] = next.sort((a, b) => a - b);
      bind();
    });
  };
  bind();
}

function fillTimedEvent(prefix, cfg) {
  setChecked(`${prefix}Enabled`, cfg.enabled);
  setValue(`${prefix}Hour`, cfg.hour);
  setValue(`${prefix}Minute`, cfg.minute);
  setValue(`${prefix}EndHour`, cfg.end_hour);
  setValue(`${prefix}EndMinute`, cfg.end_minute);
  setValue(`${prefix}Advance`, cfg.advance_minutes ?? 5);
  setDateTime(`${prefix}StartsAt`, cfg.starts_at || "");
  setDateTime(`${prefix}EndsAt`, cfg.ends_at || "");
  setValue(`${prefix}Message`, cfg.message || "");
  fillMentions(prefix, cfg);
}

function collectTimedEvent(prefix, bag) {
  return {
    enabled: $(`${prefix}Enabled`).checked,
    weekdays: (bag && bag.weekdays) || [],
    hour: Number($(`${prefix}Hour`).value || 0),
    minute: Number($(`${prefix}Minute`).value || 0),
    end_hour: Number($(`${prefix}EndHour`).value || 0),
    end_minute: Number($(`${prefix}EndMinute`).value || 0),
    advance_minutes: Number($(`${prefix}Advance`).value || 0),
    starts_at: getDateTime(`${prefix}StartsAt`),
    ends_at: getDateTime(`${prefix}EndsAt`),
    message: $(`${prefix}Message`).value,
    ...collectMentions(prefix),
  };
}

function updateSeasonBar(season) {
  const name = (season && season.name) || "S2";
  const seasonDays = Math.max(1, Number((season && season.season_days) || 45));
  const phaseDays = Math.max(1, Number((season && season.phase_days) || 15));
  const endsAt = (season && season.ends_at) || "";
  const startsAt = (season && season.starts_at) || "";
  const end = endsAt ? new Date(endsAt) : null;
  let start = startsAt ? new Date(startsAt) : null;
  if ((!start || Number.isNaN(start.getTime())) && end && !Number.isNaN(end.getTime())) {
    start = new Date(end.getTime() - seasonDays * 86400000);
  }
  const now = new Date();
  $("seasonTitle").textContent = `赛季 ${name}`;

  if (!end || Number.isNaN(end.getTime()) || !start || Number.isNaN(start.getTime())) {
    $("seasonPhase").textContent = "阶段 —";
    $("seasonRemain").textContent = "剩余 —";
    $("seasonFill").style.width = "0%";
    $("seasonSub").textContent = "请配置赛季开始/结束时间";
    return;
  }

  const totalMs = Math.max(end - start, 1);
  const elapsedMs = Math.min(Math.max(now - start, 0), totalMs);
  const remainMs = Math.max(end - now, 0);
  const pct = Math.min(100, Math.max(0, (elapsedMs / totalMs) * 100));
  const dayMs = 86400000;
  const elapsedDays = elapsedMs / dayMs;
  const phaseIndex = Math.min(
    Math.floor(elapsedDays / phaseDays) + 1,
    Math.ceil(seasonDays / phaseDays)
  );
  const remainDays = Math.floor(remainMs / dayMs);
  const remainHours = Math.floor((remainMs % dayMs) / 3600000);
  const active = now >= start && now < end;

  $("seasonPhase").textContent = `第 ${phaseIndex} 阶段`;
  $("seasonRemain").textContent = active
    ? `剩余 ${remainDays} 天 ${remainHours} 时`
    : now < start
      ? "尚未开始"
      : "已结束";
  $("seasonFill").style.width = `${pct.toFixed(1)}%`;
  $("seasonSub").textContent = active
    ? `${toLocalInput(start.toISOString()).replace("T", " ")} → ${toLocalInput(endsAt).replace("T", " ")} · 进度 ${pct.toFixed(0)}%`
    : now < start
      ? `赛季将于 ${toLocalInput(startsAt || start.toISOString()).replace("T", " ")} 开始`
      : `赛季已于 ${toLocalInput(endsAt).replace("T", " ")} 结束（赛季类停播）`;
}

function fillForm(data) {
  settings = data;
  const wechat = data.wechat || {};
  const bw = data.broadcast_window || {};
  const season = data.season || {};
  const qt = data.qiang_taizi || {};
  const js = data.jian_shou || {};
  const zg = data.zhen_guo || {};
  const sf = data.si_fang || {};
  const lk = data.liu_kou || {};
  const dy = data.diao_yu || {};
  const qy = data.qun_ying_hui || {};
  const wc = data.wang_cheng || {};
  const tz = data.tian_zi_zhan || {};

  setValue("groupName", wechat.group_name || "");
  setValue("testGroupName", wechat.test_group_name || "三国测试组");
  setChecked("dryRun", wechat.dry_run);
  setChecked("bwEnabled", bw.enabled !== false);
  setValue("bwStart", bw.start_hour ?? 9);
  setValue("bwEnd", bw.end_hour ?? 23);

  setValue("seasonName", season.name || "S2");
  setValue("seasonDays", season.season_days ?? 45);
  setValue("phaseDays", season.phase_days ?? 15);
  setDateTime("seasonStartsAt", season.starts_at || "");
  setDateTime("seasonEndsAt", season.ends_at || "");
  updateSeasonBar(season);

  setChecked("qtEnabled", qt.enabled);
  setValue("qtStart", qt.start_hour);
  setValue("qtMonStart", qt.monday_start_hour ?? 10);
  setValue("qtEnd", qt.end_hour);
  setValue("qtAdvance", qt.advance_minutes);
  setChecked("qtSkipSun24", qt.skip_sunday_24);
  setValue("qtMessage", qt.message || "");
  fillMentions("qt", qt);

  setChecked("jsEnabled", js.enabled);
  setValue("jsAdvance", js.advance_minutes ?? 5);
  setValue("jsMessage", js.message || "");
  fillMentions("js", js);
  settings.jian_shou = settings.jian_shou || js;
  {
    let times = (settings.jian_shou.times || []).filter((t) =>
      JS_TIME_OPTIONS.includes(t)
    );
    if (!times.length) times = [...JS_TIME_OPTIONS];
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
  setValue("zgStart", zg.start_hour ?? 9);
  setValue("zgMonStart", zg.monday_start_hour ?? 10);
  setValue("zgAdvance", zg.advance_minutes);
  setValue("zgMessage", zg.message || "");
  fillMentions("zg", zg);
  settings.zhen_guo = settings.zhen_guo || zg;
  bindWeekdays("zgWeekdays", "weekdays", settings.zhen_guo);
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
  fillMentions("sf", sf);
  settings.si_fang = settings.si_fang || sf;
  bindWeekdays("sfWeekdays", "weekdays", settings.si_fang);
  const bindSfMode = () => {
    renderSingleChips($("sfMode"), SF_MODES, settings.si_fang.mode || "进攻", (next) => {
      settings.si_fang.mode = next;
      bindSfMode();
    });
  };
  bindSfMode();
  const bindSfCities = () => {
    renderChips(
      $("sfCities"),
      SF_CITY_OPTIONS.filter((c) => c !== "洛阳"),
      settings.si_fang.cities || [],
      (next) => {
        settings.si_fang.cities = next;
        bindSfCities();
      }
    );
  };
  bindSfCities();
  const bindSfWeekendMode = () => {
    renderSingleChips(
      $("sfWeekendMode"),
      SF_MODES,
      settings.si_fang.weekend_mode || "防守",
      (next) => {
        settings.si_fang.weekend_mode = next;
        bindSfWeekendMode();
      }
    );
  };
  bindSfWeekendMode();
  const bindSfWeekendCities = () => {
    renderChips(
      $("sfWeekendCities"),
      SF_CITY_OPTIONS,
      settings.si_fang.weekend_cities || [],
      (next) => {
        settings.si_fang.weekend_cities = next;
        bindSfWeekendCities();
      }
    );
  };
  bindSfWeekendCities();

  setChecked("lkEnabled", lk.enabled);
  setValue("lkAdvance", lk.advance_minutes ?? 5);
  setValue("lkMessage", lk.message || "");
  fillMentions("lk", lk);
  settings.liu_kou = settings.liu_kou || lk;
  {
    let times = (settings.liu_kou.times || []).filter((t) =>
      LK_TIME_OPTIONS.includes(t)
    );
    if (!times.length) times = [...LK_TIME_OPTIONS];
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

  setChecked("dyEnabled", dy.enabled !== false);
  setValue("dyAdvance", dy.advance_minutes ?? 5);
  setValue("dyMessage", dy.message || "");
  fillMentions("dy", dy);
  settings.diao_yu = settings.diao_yu || dy;
  {
    let times = (settings.diao_yu.times || []).filter((t) =>
      DY_TIME_OPTIONS.includes(t)
    );
    if (!times.length) times = [...DY_TIME_OPTIONS];
    settings.diao_yu.times = times;
  }
  const bindDyTimes = () => {
    renderChips(
      $("dyTimes"),
      DY_TIME_OPTIONS,
      settings.diao_yu.times || [...DY_TIME_OPTIONS],
      (next) => {
        settings.diao_yu.times = next.slice().sort();
        bindDyTimes();
      }
    );
  };
  bindDyTimes();

  settings.qun_ying_hui = settings.qun_ying_hui || qy;
  settings.wang_cheng = settings.wang_cheng || wc;
  settings.tian_zi_zhan = settings.tian_zi_zhan || tz;
  fillTimedEvent("qy", qy);
  fillTimedEvent("wc", wc);
  fillTimedEvent("tz", tz);
  bindWeekdays("qyWeekdays", "weekdays", settings.qun_ying_hui);
  bindWeekdays("wcWeekdays", "weekdays", settings.wang_cheng);
  bindWeekdays("tzWeekdays", "weekdays", settings.tian_zi_zhan);

  syncFeaturePanels();
}

function syncFeaturePanels() {
  const map = [
    ["qtEnabled", "panelQt"],
    ["zgEnabled", "panelZg"],
    ["sfEnabled", "panelSf"],
    ["qyEnabled", "panelQy"],
    ["wcEnabled", "panelWc"],
    ["tzEnabled", "panelTz"],
    ["jsEnabled", "panelJs"],
    ["lkEnabled", "panelLk"],
    ["dyEnabled", "panelDy"],
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
      test_group_name: $("testGroupName").value.trim() || "三国测试组",
      dry_run: $("dryRun").checked,
      wake_hotkey:
        (settings.wechat && settings.wechat.wake_hotkey) || "ctrl+alt+w",
    },
    broadcast_window: {
      enabled: $("bwEnabled").checked,
      start_hour: Number($("bwStart").value || 9),
      end_hour: Number($("bwEnd").value || 23),
    },
    season: {
      name: $("seasonName").value.trim() || "S2",
      season_days: Number($("seasonDays").value || 45),
      phase_days: Number($("phaseDays").value || 15),
      starts_at: getDateTime("seasonStartsAt"),
      ends_at: getDateTime("seasonEndsAt"),
    },
    qiang_taizi: {
      enabled: $("qtEnabled").checked,
      advance_minutes: Number($("qtAdvance").value || 0),
      start_hour: Number($("qtStart").value || 9),
      monday_start_hour: Number($("qtMonStart").value || 10),
      end_hour: Number($("qtEnd").value || 23),
      skip_sunday_24: $("qtSkipSun24").checked,
      message: $("qtMessage").value,
      ...collectMentions("qt"),
    },
    jian_shou: {
      enabled: $("jsEnabled").checked,
      advance_minutes: Number($("jsAdvance").value || 0),
      times: settings.jian_shou.times || [...JS_TIME_OPTIONS],
      cities: settings.jian_shou.cities || [],
      message: $("jsMessage").value,
      ...collectMentions("js"),
    },
    zhen_guo: {
      enabled: $("zgEnabled").checked,
      weekdays: settings.zhen_guo.weekdays || [],
      hours: settings.zhen_guo.hours || [],
      start_hour: Number($("zgStart").value || 9),
      monday_start_hour: Number($("zgMonStart").value || 10),
      advance_minutes: Number($("zgAdvance").value || 0),
      message: $("zgMessage").value,
      ...collectMentions("zg"),
    },
    si_fang: {
      enabled: $("sfEnabled").checked,
      weekdays: settings.si_fang.weekdays || [],
      hour: Number($("sfHour").value || 13),
      minute: Number($("sfMinute").value || 0),
      advance_minutes: Number($("sfAdvance").value || 0),
      mode: settings.si_fang.mode || "进攻",
      cities: settings.si_fang.cities || [],
      weekend_mode: settings.si_fang.weekend_mode || "防守",
      weekend_cities: settings.si_fang.weekend_cities || [],
      message: $("sfMessage").value,
      ...collectMentions("sf"),
    },
    liu_kou: {
      enabled: $("lkEnabled").checked,
      times: settings.liu_kou.times || [...LK_TIME_OPTIONS],
      advance_minutes: Number($("lkAdvance").value || 0),
      message: $("lkMessage").value,
      ...collectMentions("lk"),
    },
    diao_yu: {
      enabled: $("dyEnabled").checked,
      times: settings.diao_yu.times || [...DY_TIME_OPTIONS],
      advance_minutes: Number($("dyAdvance").value || 0),
      message: $("dyMessage").value,
      ...collectMentions("dy"),
    },
    qun_ying_hui: collectTimedEvent("qy", settings.qun_ying_hui),
    wang_cheng: collectTimedEvent("wc", settings.wang_cheng),
    tian_zi_zhan: collectTimedEvent("tz", settings.tian_zi_zhan),
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

function setWindowChip(role, name, connected, title) {
  const nameEl = $(role === "target" ? "winTargetName" : "winTestName");
  const stateEl = $(role === "target" ? "winTargetState" : "winTestState");
  const chip = document.querySelector(`.window-chip[data-role="${role}"]`);
  if (nameEl) nameEl.textContent = name || "未配置";
  if (stateEl) {
    if (!name) {
      stateEl.textContent = "未配置";
      stateEl.className = "window-chip-state warn";
    } else if (connected) {
      stateEl.textContent = "已连接";
      stateEl.title = title || name;
      stateEl.className = "window-chip-state ok";
    } else {
      stateEl.textContent = "未找到";
      stateEl.className = "window-chip-state err";
    }
  }
  if (chip) {
    chip.classList.toggle("is-ok", !!connected);
    chip.classList.toggle("is-err", !!name && !connected);
  }
}

async function refreshWindows() {
  try {
    const data = await api("/api/wechat/windows");
    setWindowChip(
      "target",
      data.target_group || "",
      !!data.target_window,
      data.target_title || ""
    );
    setWindowChip(
      "test",
      data.test_group || "",
      !!data.test_window,
      data.test_title || ""
    );
  } catch (err) {
    setWindowChip("target", "", false, "");
    setWindowChip("test", "", false, "");
    const t = $("winTargetState");
    const s = $("winTestState");
    if (t) {
      t.textContent = `获取失败：${err.message || err}`;
      t.className = "window-chip-state err";
    }
    if (s) {
      s.textContent = "—";
      s.className = "window-chip-state warn";
    }
  }
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
  await refreshWindows();
  if (seasonTimer) clearInterval(seasonTimer);
  seasonTimer = setInterval(() => {
    if (settings && settings.season) updateSeasonBar(settings.season);
  }, 60000);
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

$("btnRefreshWindows").addEventListener("click", async () => {
  await refreshWindows();
  toast("窗口状态已刷新");
});

$("btnTestWechat").addEventListener("click", async () => {
  try {
    await api("/api/settings", {
      method: "PATCH",
      body: JSON.stringify({
        wechat: {
          group_name: $("groupName").value.trim(),
          test_group_name: $("testGroupName").value.trim(),
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
          test_group_name: $("testGroupName").value.trim(),
          dry_run: $("dryRun").checked,
        },
      }),
    });
    toast("试发仅发往测试群…");
    const log = await api("/api/broadcast/preview", {
      method: "POST",
      body: JSON.stringify({
        feature: "手动试发",
        message:
          "📢【三国群雄传平台司令台】连通性测试：若看到本消息，说明播报通道正常。",
        members: [],
      }),
    });
    await refreshLogs();
    toast(log.success ? "试发完成（测试群）" : log.detail || "试发失败", !!log.success);
  } catch (err) {
    toast(err.message || String(err), false);
  } finally {
    btn.disabled = false;
    btn.textContent = oldText;
  }
});

function addMember(prefix) {
  const input = $(`${prefix}MemberInput`);
  if (!input) return;
  const name = input.value.trim();
  if (!name) return;
  const list = membersByPrefix[prefix] || [];
  if (list.includes(name)) {
    toast("成员已存在", false);
    return;
  }
  membersByPrefix[prefix] = [...list, name];
  input.value = "";
  renderMembers(prefix);
}

document.querySelectorAll("[data-add-member]").forEach((btn) => {
  btn.addEventListener("click", () => addMember(btn.getAttribute("data-add-member")));
});

MENTION_PREFIXES.forEach((prefix) => {
  const input = $(`${prefix}MemberInput`);
  if (!input) return;
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addMember(prefix);
    }
  });
});

$("btnLogout").addEventListener("click", async () => {
  try {
    await api("/api/auth/logout", { method: "POST", body: "{}" });
  } catch (_) {}
  window.location.href = "/login";
});

[
  "qtEnabled",
  "zgEnabled",
  "sfEnabled",
  "qyEnabled",
  "wcEnabled",
  "tzEnabled",
  "jsEnabled",
  "lkEnabled",
  "dyEnabled",
].forEach((id) => {
  const el = $(id);
  if (!el) return;
  el.addEventListener("change", syncFeaturePanels);
});

function layoutMainCards() {
  const stack = document.querySelector(".main-stack");
  const grid = document.getElementById("featureGrid");
  if (!grid) return;
  const width = (stack && stack.clientWidth) || grid.clientWidth || 0;
  // 宽屏固定三列：赛季 / 活动 / 普通；窄屏叠成一列
  const cols = width >= 860 ? 3 : 1;
  grid.style.setProperty("--main-cols", String(cols));
  grid.dataset.cols = String(cols);
}

let layoutTimer = null;
function scheduleLayoutMainCards() {
  clearTimeout(layoutTimer);
  layoutTimer = setTimeout(layoutMainCards, 50);
}

window.addEventListener("resize", scheduleLayoutMainCards);
if (window.ResizeObserver) {
  const stack = document.querySelector(".main-stack");
  const grid = document.getElementById("featureGrid");
  if (stack) new ResizeObserver(scheduleLayoutMainCards).observe(stack);
  if (grid) new ResizeObserver(scheduleLayoutMainCards).observe(grid);
}

loadAll()
  .catch((err) => {
    setStatus(false, "服务未就绪");
    toast(err.message || String(err), false);
  })
  .finally(() => {
    layoutMainCards();
  });

setInterval(() => {
  refreshJobs().catch(() => {});
  refreshLogs().catch(() => {});
}, 15000);

layoutMainCards();
