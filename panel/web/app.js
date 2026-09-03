const SVG_FUNNEL = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z"/></svg>';
const SVG_TRASH = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/></svg>';

let settings = null;
// Discord 频道真实名称映射 {频道ID: 真实名}，由 /api/channel-names 拉取（仅展示，不写入 settings）
let channelNames = {};
// 转发组：当前活动组 id 持久化到 localStorage，刷新后恢复；
// 组内成员（channels/groups）随 settings 整体保存
const FG_STORAGE_KEY = "wuppo_active_fg";
const MAX_FG = 10;
let activeFgId = null;
try { activeFgId = localStorage.getItem(FG_STORAGE_KEY); } catch (e) { activeFgId = null; }

async function jget(url) {
  const r = await fetch(url);
  return r.json();
}
async function jpost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: body ? JSON.stringify(body) : undefined,
  });
  return r.json();
}

// 运行日志自动跟随：仅当用户已在日志底部时新日志才滚到底部，向上翻阅时暂停，滚回底部自动恢复
var logFollow = true;
document.getElementById("log").addEventListener("scroll", function () {
  const el = document.getElementById("log");
  logFollow = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
});

function renderStatus(st) {
  const b = document.getElementById("badge");
  if (st.running) {
    document.getElementById("btnStart").disabled = true;
    document.getElementById("btnStop").disabled = false;
    document.getElementById("btnRestart").disabled = false;
    const h = st.health;
    if (h && h.discord && h.qq) {
      b.textContent = "运行中";
      b.className = "badge on";
    } else if (h) {
      b.textContent = h.discord ? "运行中（QQ 未连接）" : h.qq ? "运行中（Discord 未连接）" : "运行中（未连接）";
      b.className = "badge warn";
    } else {
      b.textContent = "运行中（检测中…）";
      b.className = "badge warn";
    }
  } else {
    b.textContent = "已停止";
    b.className = "badge off";
    document.getElementById("btnStart").disabled = false;
    document.getElementById("btnStop").disabled = true;
    document.getElementById("btnRestart").disabled = true;
  }
  document.getElementById("pidinfo").textContent = st.pid ? ("PID: " + st.pid) : "";
  const stats = st.stats || {};
  const statsEl = document.getElementById("stats");
  statsEl.textContent = (stats.total_forwarded || stats.total_failed)
    ? ("今日转发 " + (stats.today_forwarded || 0) + " 条 · 累计 " + (stats.total_forwarded || 0) + " 条 · 失败 " + (stats.total_failed || 0) + " 条")
    : "";
  const lvlEl = document.getElementById("logLevel");
  if (lvlEl && st.level) lvlEl.value = st.level;
  const hint = document.getElementById("managedHint");
  hint.textContent = st.running && !st.managed
    ? "检测到机器人可能由其他方式启动，本面板无法完全控制；如需接管请先手动停止。"
    : "";
  const el = document.getElementById("log");
  const stick = logFollow;
  el.textContent = st.log || "（暂无日志）";
  if (stick) el.scrollTop = el.scrollHeight;
  renderMode(st.mode);
  var autoEl = document.getElementById("chkAutostart");
  if (autoEl) autoEl.checked = !!st.autostart;
}

var MODE_INFO = {
  test:     { btn: "modeTest",     name: "测试模式", hint: "仅启用测试群与测试频道" },
  forward:  { btn: "modeForward",  name: "转发模式", hint: "选中所有非测试群与非测试频道" },
  custom:   { btn: "modeCustom",   name: "自定义模式", hint: "按当前手动勾选配置" }
};

function renderMode(mode) {
  var info = MODE_INFO[mode] || MODE_INFO.custom;
  var gs = (settings && settings.qq_group_openids) || [];
  var cs = (settings && settings.discord_channels) || [];
  var hasTestG = gs.some(function (g) { return g.is_test; });
  var hasTestC = cs.some(function (c) { return c.is_test; });
  var hasNontestG = gs.some(function (g) { return !g.is_test; });
  var hasNontestC = cs.some(function (c) { return !c.is_test; });
  document.getElementById("modeTest").disabled = !(hasTestG && hasTestC);
  document.getElementById("modeForward").disabled = !(hasNontestG && hasNontestC);
  ["modeTest", "modeForward", "modeCustom"].forEach(function (id) {
    document.getElementById(id).classList.toggle("active", id === info.btn);
  });
  var tips = [];
  if (!(hasTestG && hasTestC)) tips.push("测试模式需至少 1 个测试群和 1 个测试频道");
  if (!(hasNontestG && hasNontestC)) tips.push("转发模式需至少 1 个非测试群和 1 个非测试频道");
  document.getElementById("modeHint").textContent =
    "当前模式：" + info.name + " · " + info.hint +
    (tips.length ? " · " + tips.join("；") : "");
}

// ---------- 转发组 ----------
function getFgList() {
  return (settings && settings.forwarding_groups) || [];
}
function getActiveFg() {
  const list = getFgList();
  if (!list.length) return null;
  let fg = null;
  if (activeFgId) {
    for (let i = 0; i < list.length; i++) {
      if (list[i].id === activeFgId) { fg = list[i]; break; }
    }
  }
  if (!fg) {
    // 当前 id 失效（组被删除/首次访问）：回退第一个组并固定记录
    fg = list[0];
    activeFgId = fg.id;
    try { localStorage.setItem(FG_STORAGE_KEY, activeFgId); } catch (e) {}
  }
  return fg;
}
function setActiveFg(id) {
  activeFgId = id;
  try { localStorage.setItem(FG_STORAGE_KEY, id); } catch (e) {}
  renderSettings();
}
function toggleFgMember(fg, id, field, on) {
  // 只修改当前组的 channels/groups 集合；id 统一转字符串存储
  if (!fg) return;
  const idStr = String(id);
  const arr = fg[field] || [];
  const idx = arr.indexOf(idStr);
  if (on && idx < 0) arr.push(idStr);
  if (!on && idx >= 0) arr.splice(idx, 1);
}
function removeFgMember(id, field) {
  // 只把实体从当前转发组移除（脱组）：不删全局实体、不影响其他组
  const fg = getActiveFg();
  if (!fg) return;
  const idStr = String(id);
  const arr = fg[field] || [];
  const idx = arr.indexOf(idStr);
  if (idx >= 0) arr.splice(idx, 1);
  saveAndReload();
}
function openFgAddPicker(kind) {
  // 「从已注册项新增」：从全局已发现/已注册实体中选择加入当前转发组
  const fg = getActiveFg();
  if (!fg) return;
  const isGroup = kind === "group";
  const field = isGroup ? "groups" : "channels";
  const memberSet = {};
  (fg[field] || []).forEach(function (id) { memberSet[String(id)] = true; });
  const all = isGroup ? settings.qq_group_openids : settings.discord_channels;
  const candidates = all.filter(function (item) {
    const id = isGroup ? String(item.openid) : String(item.id);
    return !memberSet[id];
  });
  if (!candidates.length) {
    showAlert(
      "从已注册项新增",
      "当前转发组已包含全部已发现/已注册的" + (isGroup ? "群" : "频道") + "，没有可添加的条目"
    );
    return;
  }
  showConfirmSelect(
    "已注册项",
    candidates,
    function (item) {
      if (isGroup) {
        const gname = item.name || item.remark || identityName("groups", item.openid) || "";
        return (gname + " " + item.openid).trim();
      }
      const cname = item.name || "";
      return (cname + " " + item.id).trim();
    },
    async function (selected) {
      selected.forEach(function (item) {
        toggleFgMember(fg, isGroup ? item.openid : item.id, field, true);
      });
      await saveAndReload();
    }
  );
}
let fgAddKind = null;
function openFgAddChooser(kind) {
  // 「＋ 新增」合并入口：Discord 提供 从全部可读频道 / 从已注册项 / 手动；
  // QQ 提供 从已注册项 / 手动。
  fgAddKind = kind;
  const isGroup = kind === "group";
  document.getElementById("fgAddTitle").textContent = isGroup ? "新增群" : "新增频道";
  const scanBtn = document.getElementById("fgAddScan");
  if (scanBtn) scanBtn.style.display = isGroup ? "none" : "";
  document.getElementById("fgAddModal").style.display = "flex";
}
function hideFgAddChooser() {
  document.getElementById("fgAddModal").style.display = "none";
}
// 「从全部可读频道新增」处理中：整个二级窗口按钮全部禁用（CSS button:disabled 提供半透明效果），
// 防止处理期间重复触发请求。
function setFgAddModalBusy(busy) {
  const modal = document.getElementById("fgAddModal");
  if (!modal) return;
  modal.querySelectorAll("button").forEach(function (b) { b.disabled = busy; });
}
// 「从全部可读频道新增」：整合原「同步可读频道」——扫描 Bot 可读的频道，
// 弹窗勾选后登记为全局频道（默认未启用，不自动加入当前转发组）。
async function scanReadableChannels(btn) {
  if (btn) btn.disabled = true;
  let r;
  try {
    r = await jpost("/api/channels/refresh");
  } finally {
    if (btn) btn.disabled = false;
  }
  if (!r.ok) { showAlert("扫描失败", r.msg || "扫描失败"); return; }
  const list = (r.audit && r.audit.readable) || [];
  const known = {};
  settings.discord_channels.forEach(function (c) { known[String(c.id)] = true; });
  const pending = list.filter(function (ch) { return !known[String(ch.id)]; });
  if (!pending.length) { showAlert("同步结果", "没有发现新频道"); return; }
  showConfirmSelect("全部可读频道", pending, function (ch) { return ch.name + " (" + ch.id + ")"; }, async function (selected) {
    if (!selected.length) { showAlert("同步结果", "未选择任何频道"); return; }
    let added = 0;
    selected.forEach(function (ch) {
      const id = String(ch.id);
      if (known[id]) return;
      settings.discord_channels.push({id: id, name: String(ch.name || "").replace(/^#/, ""), enabled: false, is_test: false});
      known[id] = true;
      added++;
    });
    if (added) {
      await saveAndReload();
    } else {
      renderSettings();
    }
    showAlert("同步结果", "已同步 " + added + " 个新频道（默认未启用）");
  });
}
function renderFgTabs() {
  const wrap = document.getElementById("fgTabs");
  if (!wrap) return;
  const list = getFgList();
  getActiveFg();   // 顺带校正 activeFgId（失效则回退并持久化）
  wrap.innerHTML = "";
  list.forEach(function (fg) {
    const isActive = fg.id === activeFgId;
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "fgtab" + (isActive ? " active" : "");
    tab.dataset.kind = "fgTab";
    tab.dataset.id = fg.id;
    tab.textContent = fg.name;
    tab.title = "切换到转发组「" + fg.name + "」";
    wrap.appendChild(tab);
    if (isActive) {
      // 重命名/删除只对当前组可用（与 tab 并列，避免按钮嵌套）
      const rename = document.createElement("button");
      rename.type = "button";
      rename.className = "fgctrl rowicon";
      rename.dataset.kind = "fgRename";
      rename.textContent = "✎";
      rename.title = "重命名当前转发组（ID 不变）";
      wrap.appendChild(rename);
      if (list.length > 1) {
        const del = document.createElement("button");
        del.type = "button";
        del.className = "fgctrl rowicon";
        del.dataset.kind = "fgDel";
        del.innerHTML = SVG_TRASH;   // 与频道/群行内删除按钮同一白色垃圾桶图标
        del.title = "删除当前转发组";
        wrap.appendChild(del);
      }
    }
  });
  // 达到上限(10)后直接不显示新增按钮，删除一个后自动恢复
  if (list.length < MAX_FG) {
    const add = document.createElement("button");
    add.type = "button";
    add.className = "fgtab-add";
    add.id = "btnAddFg";
    add.title = "添加转发组（空组，按需勾选成员）";
    add.textContent = "＋ 添加转发组";
    wrap.appendChild(add);
  }
}
function addForwardingGroup() {
  const list = getFgList();
  if (list.length >= MAX_FG) { showAlert("添加转发组", "最多 " + MAX_FG + " 个转发组"); return; }
  // 空组：不复制任何成员；名称自动取未占用的「转发组N」
  let n = list.length + 1;
  const names = {};
  list.forEach(function (g) { names[g.name] = true; });
  while (names["转发组" + n]) n++;
  const fg = {
    id: "fg_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    name: "转发组" + n,
    channels: [],
    groups: [],
  };
  list.push(fg);
  activeFgId = fg.id;
  try { localStorage.setItem(FG_STORAGE_KEY, fg.id); } catch (e) {}
  saveAndReload();
}
let fgRenameActive = false;
function startFgRename() {
  const fg = getActiveFg();
  if (!fg || fgRenameActive) return;
  const wrap = document.getElementById("fgTabs");
  const tabs = Array.prototype.slice.call(wrap.querySelectorAll(".fgtab"));
  const tabBtn = tabs.find(function (b) { return b.dataset.id === fg.id; });
  if (!tabBtn) return;
  fgRenameActive = true;
  const input = document.createElement("input");
  input.type = "text";
  input.className = "fg-rename";
  input.value = fg.name;
  input.style.width = "110px";
  input.style.padding = "4px 8px";
  input.style.fontSize = "13px";
  tabBtn.replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const finish = function (commit) {
    if (done) return;
    done = true;
    fgRenameActive = false;
    const v = input.value.trim();
    if (commit && v && v !== fg.name) {
      fg.name = v;   // 只改 name，id 保持不变
      saveAndReload();
    } else {
      renderSettings();
    }
  };
  input.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
    else if (ev.key === "Escape") { ev.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", function () { finish(true); });
}

function renderSettings() {
  renderFgTabs();
  const activeFg = getActiveFg();
  const gb = document.getElementById("groupBody");
  gb.innerHTML = "";
  // 只渲染当前转发组的成员；「启用/测试」仍是全局实体状态
  const memberGrps = {};
  if (activeFg) {
    (activeFg.groups || []).forEach(function (o) { memberGrps[String(o)] = true; });
  }
  const grpRows = settings.qq_group_openids.filter(function (g) { return memberGrps[String(g.openid)]; });
  if (!grpRows.length) {
    gb.innerHTML = '<tr><td colspan="6" class="fgempty">（空）点击上方「从已注册项新增」从已发现/已注册的群中选择加入</td></tr>';
  }
  grpRows.forEach((g) => {
    const tr = document.createElement("tr");
    const gi = settings.qq_group_openids.indexOf(g);
    // 名称/账号：注册审核通过时写入（无注册信息的手动条目为空），
    // 名称缺省时回退显示身份库自动识别的群名
    const gname = g.name || identityName("groups", g.openid) || "";
    const gacc = g.qq_id || "";
    tr.innerHTML =
      '<td style="text-align:center"><input type="checkbox" data-kind="group" data-i="' + gi + '"' + (g.enabled ? " checked" : "") + '></td>' +
      '<td style="text-align:center"><input type="checkbox" data-kind="groupIsTest" data-i="' + gi + '"' + (g.is_test ? " checked" : "") + '></td>' +
      '<td>' +
        (gname ? '<div style="color:#8dc891;font-size:12px;word-break:break-all;">' + escapeHtml(gname) + '</div>' : '') +
      '</td>' +
      '<td style="font-size:12px;">' + escapeHtml(gacc) + '</td>' +
      '<td class="mono">' + escapeHtml(g.openid) + '</td>' +
      '<td style="text-align:right"><button class="danger rowicon" data-kind="fgGroupRemove" data-id="' + escapeHtml(String(g.openid)) + '" title="从当前转发组移除（不影响全局与其他组）">' + SVG_TRASH + '</button></td>';
    gb.appendChild(tr);
  });
  const cb = document.getElementById("chanBody");
  cb.innerHTML = "";
  // 只渲染当前转发组的成员
  const memberChans = {};
  if (activeFg) {
    (activeFg.channels || []).forEach(function (id) { memberChans[String(id)] = true; });
  }
  const chanRows = settings.discord_channels.filter(function (c) { return memberChans[String(c.id)]; });
  if (!chanRows.length) {
    cb.innerHTML = '<tr><td colspan="5" class="fgempty">（空）点击上方「从已注册项新增」从已发现/已注册的频道中选择加入</td></tr>';
  }
  chanRows.forEach((c) => {
    const tr = document.createElement("tr");
    const gi = settings.discord_channels.indexOf(c);
    const realName = channelNames[c.id] || "";
    tr.innerHTML =
      '<td style="text-align:center"><input type="checkbox" data-kind="chan" data-i="' + gi + '"' + (c.enabled ? " checked" : "") + '></td>' +
      '<td style="text-align:center"><input type="checkbox" data-kind="chanIsTest" data-i="' + gi + '"' + (c.is_test ? " checked" : "") + '></td>' +
      '<td>' +
        '<span class="chan-name" data-kind="chanNameDisplay" data-i="' + gi + '">' +
          '<span class="chan-name-text">' + escapeHtml(c.name || "") + '</span>' +
          '<span class="chan-name-edit" data-kind="chanNameEdit" data-i="' + gi + '" title="编辑频道名称">✎</span>' +
        '</span>' +
        (realName ? '<div style="color:#8dc891;font-size:12px;margin-top:2px;">' + escapeHtml(realName) + '</div>' : '') +
      '</td>' +
      '<td class="mono">' + escapeHtml(c.id) + '</td>' +
      '<td><div style="display:flex;gap:4px;justify-content:flex-end;">' +
        '<button class="sec rowicon' + ((c.filter_usernames && c.filter_usernames.length) || (c.filter_keywords && c.filter_keywords.length) ? ' filter-active' : '') + '" data-kind="chanFilter" data-i="' + gi + '" title="消息筛选">' + SVG_FUNNEL + '</button>' +
        '<button class="danger rowicon" data-kind="fgChanRemove" data-id="' + escapeHtml(String(c.id)) + '" title="从当前转发组移除（不影响全局与其他组）">' + SVG_TRASH + '</button>' +
      '</div></td>';
    cb.appendChild(tr);
  });
  const ub = document.getElementById("userBody");
  if (ub) {
    ub.innerHTML = "";
    (settings.qq_user_openids || []).forEach((u, i) => {
      const tr = document.createElement("tr");
      // 名称/账号：注册审核通过时写入（无注册信息的手动条目为空），
      // 名称缺省时回退显示身份库自动识别的昵称
      const uname = u.name || identityName("users", u.openid) || "";
      const uacc = u.qq_id || "";
      tr.innerHTML =
        '<td style="text-align:center"><input type="checkbox" data-kind="user" data-i="' + i + '"' + (u.enabled ? " checked" : "") + '></td>' +
        '<td>' +
          (uname ? '<div style="color:#8dc891;font-size:12px;word-break:break-all;">' + escapeHtml(uname) + '</div>' : '') +
        '</td>' +
        '<td style="font-size:12px;">' + escapeHtml(uacc) + '</td>' +
        '<td class="mono">' + escapeHtml(u.openid) + '</td>' +
        '<td style="text-align:right"><button class="danger rowicon" data-kind="userDel" data-i="' + i + '" title="删除用户">' + SVG_TRASH + '</button></td>';
      ub.appendChild(tr);
    });
  }
  // 离线补发设置
  const bfEnabled = document.getElementById("backfillEnabled");
  if (bfEnabled) bfEnabled.checked = settings.backfill_enabled !== false;
  const bfLimit = document.getElementById("backfillLimit");
  if (bfLimit) bfLimit.value = Number(settings.backfill_limit) > 0 ? settings.backfill_limit : 10;
  // 表头全选框状态仅反映「当前转发组」实际显示的成员（而非全部全局实体），
  // 避免隐藏的非本组成员导致半选误判：全部→勾选，全不→取消，部分→半选，无成员→两者均 false
  syncHeaderAll("groupAllEnabled", grpRows, "enabled");
  syncHeaderAll("groupAllTest", grpRows, "is_test");
  syncHeaderAll("chanAllEnabled", chanRows, "enabled");
  syncHeaderAll("chanAllTest", chanRows, "is_test");
  syncHeaderAll("userAllEnabled", settings.qq_user_openids || [], "enabled");
}

function syncHeaderAll(id, items, field) {
  const el = document.getElementById(id);
  if (!el) return;
  const on = items.filter(function (o) { return !!o[field]; }).length;
  if (on === 0) {
    el.checked = false;
    el.indeterminate = false;
  } else if (on === items.length) {
    el.checked = true;
    el.indeterminate = false;
  } else {
    el.checked = false;
    el.indeterminate = true;
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
  });
}

// ---------------- OpenID 身份识别（辅助数据源） ----------------
// 身份库只作为「QQ 接收群」「私聊权限」表格的辅助数据源：
// 在 OpenID 旁展示已识别的群名/昵称，不提供独立的管理入口。
let identityData = {users: [], groups: []};

function identityMap(kind) {
  const m = {};
  (identityData[kind] || []).forEach(function (it) {
    m[it.openid] = it;
  });
  return m;
}

function identityName(kind, openid) {
  const it = identityMap(kind)[openid];
  return (it && it.name) || "";
}

async function refreshIdentities() {
  try {
    const r = await jget("/api/identities");
    if (r && r.ok) {
      identityData = {users: r.users || [], groups: r.groups || []};
      renderSettings();  // 用识别的昵称/群名刷新白名单表格展示
    }
  } catch (err) { /* 身份库暂不可用时忽略，仅不显示名称 */ }
}

function renderBackfill(data) {
  const el = document.getElementById("backfillPending");
  if (!el) return;
  if (!data || !data.ok) {
    el.textContent = data && data.running === false ? "机器人未运行" : "—";
    return;
  }
  const total = data.total || 0;
  el.textContent = total > 0 ? ("共 " + total + " 条") : "无";
  el.style.color = total > 0 ? "#e5b567" : "";
}

async function refreshBackfill() {
  try {
    const r = await jget("/api/backfill/pending");
    renderBackfill(r);
  } catch (err) {
    renderBackfill(null);
  }
}

async function refreshChannelNames() {
  try {
    const r = await jget("/api/channel-names");
    if (r && r.names) channelNames = r.names || {};
  } catch (err) { /* ignore */ }
  renderSettings();
}

// ---------- 通用弹窗（confirmModal：alert / confirm / 勾选确认） ----------
let confirmAction = null;

function setConfirmButtons(okText, showCancel, cancelText, showOk) {
  const ok = document.getElementById("confirmOk");
  ok.textContent = okText;
  ok.style.display = showOk === false ? "none" : "";
  const cancel = document.getElementById("confirmCancel");
  cancel.style.display = showCancel ? "" : "none";
  cancel.textContent = cancelText || "取消";
}

function showAlert(title, message) {
  document.getElementById("confirmTitle").textContent = title;
  const body = document.getElementById("confirmBody");
  body.innerHTML = "";
  const p = document.createElement("div");
  p.style.whiteSpace = "pre-wrap";
  p.textContent = message;
  body.appendChild(p);
  setConfirmButtons("确定", false);
  confirmAction = null;
  document.getElementById("confirmModal").style.display = "flex";
}

function showConfirmDlg(title, message, onOk, okText) {
  document.getElementById("confirmTitle").textContent = title;
  const body = document.getElementById("confirmBody");
  body.innerHTML = "";
  const p = document.createElement("div");
  p.style.whiteSpace = "pre-wrap";
  p.textContent = message;
  body.appendChild(p);
  setConfirmButtons(okText || "确定", true);
  confirmAction = onOk;
  document.getElementById("confirmModal").style.display = "flex";
}

function showConfirmSelect(title, items, labelFn, onOkSelected) {
  document.getElementById("confirmTitle").textContent = title;
  const body = document.getElementById("confirmBody");
  body.innerHTML = "";
  const rows = [];
  items.forEach(function (it) {
    const row = document.createElement("label");
    row.style.display = "flex";
    row.style.alignItems = "center";
    row.style.gap = "8px";
    row.style.cursor = "pointer";
    row.style.padding = "2px 0";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = true;
    cb.style.width = "16px";
    cb.style.height = "16px";
    const span = document.createElement("span");
    span.className = "mono";
    span.textContent = labelFn(it);
    row.appendChild(cb);
    row.appendChild(span);
    body.appendChild(row);
    rows.push({it: it, cb: cb});
  });
  setConfirmButtons("确认添加", true);
  confirmAction = function () {
    const selected = rows.filter(function (b) { return b.cb.checked; }).map(function (b) { return b.it; });
    return onOkSelected(selected);
  };
  document.getElementById("confirmModal").style.display = "flex";
}

function hideConfirm() {
  document.getElementById("confirmModal").style.display = "none";
  confirmAction = null;
  setConfirmButtons("确定", true);
}

// ---------- 注册审核弹窗（两个 QQ 同步弹窗：群 / 用户） ----------
// 自动发现的 openid 只记录不进同步名单；主动注册后才出现在这里。
// 每条申请显示 QQ号/群号 + 昵称/群名 + OpenID，右侧 √ 通过 / × 拒绝：
// 通过 → 按现有同步流程加入设置列表（默认未启用）；拒绝 → 申请消失。
let regReviewKind = null;

async function openRegistrationReview(kind) {
  regReviewKind = kind;
  // 隐藏"确定"，显示"关闭"（审核按钮在每条申请右侧）
  setConfirmButtons("", true, "关闭", false);
  document.getElementById("confirmTitle").textContent = "加载中…";
  document.getElementById("confirmBody").innerHTML = "";
  document.getElementById("confirmModal").style.display = "flex";
  let data = null;
  try {
    data = await jget("/api/registrations");
  } catch (err) { /* 接口不可用时按空列表展示 */ }
  renderRegistrationReview(data);
}

function renderRegistrationReview(data) {
  const body = document.getElementById("confirmBody");
  const items = (data && data[regReviewKind]) || [];
  document.getElementById("confirmTitle").textContent =
    (regReviewKind === "groups" ? "群注册审核" : "用户注册审核")
    + (items.length ? "（" + items.length + " 条待审）" : "");
  body.innerHTML = "";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.style.cssText = "color:#8b93a1;padding:8px 0;";
    empty.textContent = "没有待审核的注册申请";
    body.appendChild(empty);
  }
  items.forEach(function (it) {
    const label = regReviewKind === "groups"
      ? "群号 " + (it.qq_id || "?") + " · " + (it.group_name || "未命名")
      : "QQ " + (it.qq_id || "?") + " · " + (it.nickname || "未命名");
    const row = document.createElement("div");
    row.style.cssText = "display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #2a3140;";
    const info = document.createElement("div");
    info.style.cssText = "flex:1;min-width:0;";
    info.innerHTML =
      '<div>' + escapeHtml(label) + '</div>' +
      '<div class="mono" style="font-size:11px;color:#8b93a1;word-break:break-all;">' + escapeHtml(it.openid) + '</div>';
    row.appendChild(info);
    const ok = document.createElement("button");
    ok.className = "sec";
    ok.textContent = "√";
    ok.title = "通过";
    ok.style.cssText = "color:#8dc891;border-color:#3f6b45;";
    ok.addEventListener("click", function () { reviewRegistration(it.openid, "approve"); });
    const no = document.createElement("button");
    no.className = "danger";
    no.textContent = "×";
    no.title = "拒绝";
    no.addEventListener("click", function () { reviewRegistration(it.openid, "reject"); });
    row.appendChild(ok);
    row.appendChild(no);
    body.appendChild(row);
  });
}

async function reviewRegistration(openid, action) {
  const r = await jpost("/api/registrations/review", {kind: regReviewKind, openid: openid, action: action});
  if (!r.ok) {
    showAlert("审核失败", r.msg || "操作失败");
    return;
  }
  if (r.settings) {
    settings = r.settings;
    renderSettings();
    await refreshIdentities();
  }
  const list = await jget("/api/registrations");
  // 审核过程中弹窗被关闭（如弹出了失败提示）则不再重绘
  if (document.getElementById("confirmModal").style.display === "flex") {
    renderRegistrationReview(list);
  }
}

// ---------- 频道筛选弹窗 ----------
let filterEditIndex = -1;

function openFilterModal(index) {
  filterEditIndex = index;
  const c = settings.discord_channels[index];
  document.getElementById("filterChanName").textContent = "# " + (c.name || c.id);
  document.getElementById("filterUsernames").value = (c.filter_usernames || []).join(", ");
  document.getElementById("filterKeywords").value = (c.filter_keywords || []).join(", ");
  document.getElementById("filterModal").style.display = "flex";
}

function closeFilterModal() {
  document.getElementById("filterModal").style.display = "none";
  filterEditIndex = -1;
}

document.getElementById("filterSave").addEventListener("click", async function () {
  if (filterEditIndex < 0) return;
  const c = settings.discord_channels[filterEditIndex];
  c.filter_usernames = document.getElementById("filterUsernames").value
    .split(",").map(function (s) { return s.trim(); }).filter(Boolean);
  c.filter_keywords = document.getElementById("filterKeywords").value
    .split(",").map(function (s) { return s.trim(); }).filter(Boolean);
  closeFilterModal();
  await saveAndReload();
});

document.getElementById("filterCancel").addEventListener("click", closeFilterModal);

// ---------- 新增条目弹窗（QQ 群 / Discord 频道 / 私聊用户） ----------
// 字段以现有数据结构为准：群/用户只有 remark，频道只有 name，名称类信息
// 一律来自身份库/真实名称（只读展示），此处不新增存储字段。
let addKind = null;

const ADD_KINDS = {
  group: {
    title: "手动新增",
    idLabel: "Group OpenID",
    idPlaceholder: "群 OpenID",
    idRequiredMsg: "请填写群 openid",
    showName: false,
    showRemark: true,
  },
  chan: {
    title: "手动新增",
    idLabel: "Channel ID",
    idPlaceholder: "频道 ID",
    idRequiredMsg: "请填写频道 ID",
    showName: true,
    showRemark: false,
  },
  user: {
    title: "新增私聊权限",
    idLabel: "User OpenID",
    idPlaceholder: "用户 openid",
    idRequiredMsg: "请填写用户 openid",
    showName: false,
    showRemark: true,
  },
};

function openAddModal(kind) {
  const cfg = ADD_KINDS[kind];
  if (!cfg) return;
  addKind = kind;
  document.getElementById("addModalTitle").textContent = cfg.title;
  document.getElementById("addFieldNameWrap").style.display = cfg.showName ? "" : "none";
  document.getElementById("addFieldNameLabel").textContent = cfg.showName ? "频道名称（可选）" : "";
  document.getElementById("addFieldName").value = "";
  document.getElementById("addFieldRemarkWrap").style.display = cfg.showRemark ? "" : "none";
  document.getElementById("addFieldRemark").value = "";
  document.getElementById("addFieldIdLabel").textContent = cfg.idLabel;
  document.getElementById("addFieldId").value = "";
  document.getElementById("addFieldId").placeholder = cfg.idPlaceholder;
  document.getElementById("addModal").style.display = "flex";
}

function closeAddModal() {
  document.getElementById("addModal").style.display = "none";
  addKind = null;
}

document.getElementById("addCancel").addEventListener("click", closeAddModal);

document.getElementById("addSave").addEventListener("click", async function () {
  if (!addKind) return;
  const cfg = ADD_KINDS[addKind];
  const id = document.getElementById("addFieldId").value.trim();
  if (!id) return showAlert("输入提示", cfg.idRequiredMsg);
  if (addKind === "group") {
    settings.qq_group_openids.push({openid: id, enabled: true, remark: document.getElementById("addFieldRemark").value.trim(), is_test: false});
    toggleFgMember(getActiveFg(), id, "groups", true);   // 手动新增群 → 自动加入当前转发组
  } else if (addKind === "chan") {
    settings.discord_channels.push({id: id, name: document.getElementById("addFieldName").value.trim(), enabled: true, is_test: false});
    toggleFgMember(getActiveFg(), id, "channels", true); // 手动新增频道 → 自动加入当前转发组
  } else if (addKind === "user") {
    settings.qq_user_openids.push({openid: id, enabled: true, remark: document.getElementById("addFieldRemark").value.trim()});
  }
  closeAddModal();
  await saveAndReload();
});

async function saveAndReload() {
  await jpost("/api/settings/save", settings);
  settings = await jget("/api/settings");
  renderSettings();
  await refreshChannelNames();
  await refreshStatus();
}

document.addEventListener("click", async function (e) {
  const btn = e.target.closest("button");
  if (!btn) return;
  if (btn.id === "confirmOk") {
    const fn = confirmAction;
    hideConfirm();
    if (fn) await fn();
    return;
  }
  if (btn.id === "confirmCancel") {
    hideConfirm();
    return;
  }
  if (btn.dataset.hint) {
    const tip = document.getElementById(btn.dataset.hint);
    showAlert(btn.dataset.title, (tip && tip.textContent) || "当前无附加说明。");
    return;
  }
  if (btn.dataset.fold) {
    const el = document.getElementById(btn.dataset.fold);
    const folded = el.style.display === "none";
    el.style.display = folded ? "" : "none";
    btn.textContent = folded ? "▾" : "▸";
    return;
  }
  const k = btn.dataset.kind;
  if (k === "fgTab") {
    setActiveFg(btn.dataset.id);
    return;
  }
  if (k === "fgRename") {
    startFgRename();
    return;
  }
  if (k === "fgDel") {
    const list = getFgList();
    if (list.length <= 1) { showAlert("删除转发组", "至少保留 1 个转发组，无法删除最后一个"); return; }
    const fg = getActiveFg();
    showConfirmDlg("删除转发组", "删除转发组「" + fg.name + "」将移除其频道→群路由关系，频道和群本身不受影响。确定删除？", async function () {
      // 确认时基于最新 settings 解析，避免弹窗期间对象被替换
      const curList = getFgList();
      const target = curList.find(function (x) { return x.id === fg.id; });
      if (!target) return;
      curList.splice(curList.indexOf(target), 1);
      if (activeFgId === fg.id) {
        const next = curList[0] || null;
        activeFgId = next ? next.id : null;
        try { localStorage.setItem(FG_STORAGE_KEY, activeFgId || ""); } catch (e) {}
      }
      await saveAndReload();
    }, "删除");
    return;
  }
  if (btn.id === "btnAddFg") {
    addForwardingGroup();
    return;
  }
  if (btn.id === "btnAddFgChan") {
    openFgAddChooser("chan");
    return;
  }
  if (btn.id === "btnAddFgGroup") {
    openFgAddChooser("group");
    return;
  }
  if (btn.id === "fgAddPick") {
    hideFgAddChooser();
    openFgAddPicker(fgAddKind);
    return;
  }
  if (btn.id === "fgAddScan") {
    // 不立即关闭二级窗口：先进入处理中状态（按钮禁用+半透明），
    // 等 /api/channels/refresh 完成后再关闭窗口并继续原有后续流程；
    // 失败时恢复可用并显示现有错误提示。
    if (btn.disabled) return;        // 处理中：防重复触发
    setFgAddModalBusy(true);
    try {
      await scanReadableChannels(null);
    } finally {
      setFgAddModalBusy(false);
      hideFgAddChooser();
    }
    return;
  }
  if (btn.id === "fgAddManual") {
    hideFgAddChooser();
    openAddModal(fgAddKind);
    return;
  }
  if (btn.id === "fgAddCancel") {
    hideFgAddChooser();
    return;
  }
  if (k === "fgGroupRemove") {
    removeFgMember(btn.dataset.id, "groups");
    return;
  } else if (k === "userDel") {
    const idx = Number(btn.dataset.i);
    const u = settings.qq_user_openids[idx];
    const label = (u.name || "").trim() || (u.remark || "").trim() || identityName("users", u.openid) || u.openid;
    showConfirmDlg("删除用户", "确定要删除用户「" + label + "」吗？", async function () {
      settings.qq_user_openids.splice(idx, 1);
      await saveAndReload();
    }, "删除");
  } else if (k === "chanFilter") {
    openFilterModal(Number(btn.dataset.i));
  } else if (k === "fgChanRemove") {
    removeFgMember(btn.dataset.id, "channels");
  } else if (btn.id === "btnAddUser") {
    openAddModal("user");
  } else if (btn.id === "btnSync") {
    // 群注册审核：自动发现的 openid 只记录，注册后才进入待审名单
    await openRegistrationReview("groups");
  } else if (btn.id === "btnSyncUsers") {
    // 用户注册审核：自动发现的 openid 只记录，注册后才进入待审名单
    await openRegistrationReview("users");
  } else if (btn.id === "btnQQPlatform") {
    window.open("https://q.qq.com/qqbot/dashboard/", "_blank");
  } else if (btn.id === "btnLogLevel") {
    const level = document.getElementById("logLevel").value;
    const r = await jpost("/api/bot/loglevel", {level: level});
    if (!r.ok) showAlert("设置失败", r.msg || "设置失败");
    await refreshStatus();
  } else if (btn.id === "btnDiscordDev") {
    window.open("https://discord.com/developers/home", "_blank");
  } else if (btn.id === "btnClearLog") {
    const r = await jpost("/api/bot/log/clear");
    if (!r.ok) showAlert("清空失败", r.msg || "清空失败");
    await refreshStatus();
  } else if (btn.id === "btnBackfillRun") {
    // 与 QQ 私聊 backfill run 一致：先查待补发缺口，无消息时直接告知，避免空触发
    btn.disabled = true;
    try {
      const p = await jget("/api/backfill/pending");
      if (p.ok && !p.total) {
        showAlert("补发", "当前无可补发的消息");
        await refreshBackfill();
      } else {
        const r = await jpost("/api/backfill/run");
        showAlert("补发", r.ok ? (r.msg || "补发已触发") : (r.msg || "补发失败"));
        await refreshBackfill();
      }
    } finally {
      btn.disabled = false;
    }
  } else if (btn.id === "btnBackfillRefresh") {
    btn.disabled = true;
    try {
      await refreshBackfill();
      showAlert("刷新状态", "已读取 Discord 实时状态");
    } finally {
      btn.disabled = false;
    }
  } else if (btn.id === "btnBackfillClear") {
    showConfirmDlg("清空待补发", "清空待补发？未转发的旧消息将不再补发，此操作不可撤销。", async function () {
      btn.disabled = true;
      try {
        const r = await jpost("/api/backfill/clear");
        showAlert("清空结果", r.ok ? (r.cleared ? ("已清空 " + r.cleared + " 个频道的待补发") : "当前无可清空的待补发") : (r.msg || "清空失败"));
        await refreshBackfill();
      } finally {
        btn.disabled = false;
      }
    });
  } else if (btn.id === "btnStart") {
    await jpost("/api/bot/start");
    await sleep(800); await refreshStatus();
  } else if (btn.id === "btnStop") {
    await jpost("/api/bot/stop");
    await sleep(800); await refreshStatus();
  } else if (btn.id === "btnRestart") {
    await jpost("/api/bot/restart");
    await sleep(1500); await refreshStatus();
  } else if (btn.id === "btnRestartPanel") {
    btn.disabled = true;
    try {
      const r = await jpost("/api/panel/restart");
      if (r.ok) {
        await sleep(3000);
        location.reload();
      } else {
        btn.disabled = false;
        showAlert("重启失败", r.msg || "重启失败");
      }
    } catch (err) {
      btn.disabled = false;
      showAlert("重启失败", "请求失败，面板可能已离线");
    }
  } else if (btn.id === "modeTest" || btn.id === "modeForward" || btn.id === "modeCustom") {
    const mode = {modeTest: "test", modeForward: "forward", modeCustom: "custom"}[btn.id];
    const r = await jpost("/api/mode/apply", {mode: mode});
    if (r.ok) {
      settings = r.settings;
      renderSettings();
    }
    await refreshStatus();
  }
});

document.addEventListener("change", async function (e) {
  const el = e.target;
  if (el.id === "chkAutostart") {
    const r = await jpost("/api/autostart/set", {enabled: el.checked});
    if (!r.ok) showAlert("设置失败", "设置开机自启失败");
    el.checked = !!r.enabled;
    return;
  }
  if (el.id === "backfillEnabled") {
    settings.backfill_enabled = el.checked;
    await saveAndReload();
    return;
  }
  if (el.id === "backfillLimit") {
    let v = parseInt(el.value, 10);
    if (!(v > 0)) v = 10;
    settings.backfill_limit = v;
    el.value = v;
    await saveAndReload();
    return;
  }
  if (el.id === "groupAllEnabled") {
    settings.qq_group_openids.forEach(function (g) { g.enabled = el.checked; });
    await saveAndReload();
    return;
  }
  if (el.id === "groupAllTest") {
    settings.qq_group_openids.forEach(function (g) { g.is_test = el.checked; });
    await saveAndReload();
    return;
  }
  if (el.id === "chanAllEnabled") {
    settings.discord_channels.forEach(function (c) { c.enabled = el.checked; });
    await saveAndReload();
    return;
  }
  if (el.id === "chanAllTest") {
    settings.discord_channels.forEach(function (c) { c.is_test = el.checked; });
    await saveAndReload();
    return;
  }
  if (el.id === "userAllEnabled") {
    settings.qq_user_openids.forEach(function (u) { u.enabled = el.checked; });
    await saveAndReload();
    return;
  }
  if (!el.dataset.kind) return;
  if (el.dataset.kind === "group") {
    settings.qq_group_openids[Number(el.dataset.i)].enabled = el.checked;
    await saveAndReload();
  } else if (el.dataset.kind === "groupIsTest") {
    settings.qq_group_openids[Number(el.dataset.i)].is_test = el.checked;
    await saveAndReload();
  } else if (el.dataset.kind === "chan") {
    settings.discord_channels[Number(el.dataset.i)].enabled = el.checked;
    await saveAndReload();
  } else if (el.dataset.kind === "chanIsTest") {
    settings.discord_channels[Number(el.dataset.i)].is_test = el.checked;
    await saveAndReload();
  } else if (el.dataset.kind === "user") {
    settings.qq_user_openids[Number(el.dataset.i)].enabled = el.checked;
    await saveAndReload();
  }
});

document.addEventListener("blur", async function (e) {
  const el = e.target;
  if (el.dataset.kind === "chanName") {
    settings.discord_channels[Number(el.dataset.i)].name = el.value;
    await saveAndReload();
  }
}, true);

// 频道名称：正常状态显示「名称 ✎」，点击 ✎ 才显示输入框进入编辑；
// 失焦后沿用上方既有 chanName 保存逻辑（保存并重渲染，自动恢复为「名称 ✎」）。
function startChanNameEdit(i) {
  const c = settings.discord_channels[Number(i)];
  if (!c) return;
  const holder = document.querySelector('.chan-name[data-kind="chanNameDisplay"][data-i="' + i + '"]');
  if (!holder) return;
  const input = document.createElement("input");
  input.type = "text";
  input.value = c.name || "";
  input.dataset.kind = "chanName";
  input.dataset.i = String(i);
  holder.replaceWith(input);
  input.focus();
  input.select();
}
document.addEventListener("click", function (e) {
  const edit = e.target.closest('[data-kind="chanNameEdit"]');
  if (edit) {
    startChanNameEdit(edit.dataset.i);
  }
});

function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

let lastBackfillSeq = 0;

async function refreshStatus() {
  try {
    const st = await jget("/api/status");
    renderStatus(st);
    // 同步补发开关/上限（QQ 私聊指令可能改动 settings.json）；
    // 正在编辑的控件不覆盖，避免打断用户输入
    const bfEnabled = document.getElementById("backfillEnabled");
    if (
      bfEnabled
      && st.backfill_enabled !== undefined
      && document.activeElement !== bfEnabled
    ) {
      bfEnabled.checked = st.backfill_enabled !== false;
    }
    const bfLimit = document.getElementById("backfillLimit");
    if (
      bfLimit
      && st.backfill_limit !== undefined
      && document.activeElement !== bfLimit
    ) {
      bfLimit.value = Number(st.backfill_limit) > 0 ? st.backfill_limit : 10;
    }
    // 补发/清除成功（含 QQ 私聊触发）后立即刷新待补发数量
    if (st.backfill_seq !== lastBackfillSeq) {
      lastBackfillSeq = st.backfill_seq;
      await refreshBackfill();
    }
  } catch (err) {
    document.getElementById("badge").textContent = "面板连接断开";
  }
}

async function init() {
  try {
    settings = await jget("/api/settings");
    renderSettings();
  } catch (err) { /* ignore */ }
  await refreshChannelNames();
  await refreshStatus();
  setInterval(refreshStatus, 3000);
  await refreshBackfill();
  setInterval(refreshBackfill, 300000);
  await refreshIdentities();
  setInterval(refreshIdentities, 30000);
}

init();
