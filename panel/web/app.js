const SVG_FUNNEL = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z"/></svg>';
const SVG_TRASH = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/></svg>';
const SVG_GRIP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>';
// 统一笔图标：空心笔、笔尖朝左下角（转发组重命名 / 频道名称编辑共用）
const SVG_PEN = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>';
// 主题按钮图标：实心月亮（夜间）/ 太阳（日间），跟随按钮文字色
const SVG_MOON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
const SVG_SUN = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>';

// 关于作者 —— 社交媒体链接（QQ / Bilibili 链接留待填写；GitHub 固定）
const ABOUT_LINKS = {
  qq:       "https://qm.qq.com/q/KOaCzqYuES",
  bilibili: "https://space.bilibili.com/437986248",
  github:   "https://github.com/EraserOVO",
};
function openAboutLink(url, label) {
  if (!url) {
    showAlert("关于作者", label + " 链接还未填写，请在 app.js 顶部的 ABOUT_LINKS 中配置。");
    return;
  }
  window.open(url, "_blank");
}

let settings = null;
// 主题偏好：夜间/日间模式，持久化到 localStorage，默认夜间（与旧版视觉一致）
const THEME_KEY = "wuppo_theme";
const THEME_DARK = "dark";
const THEME_LIGHT = "light";
function applyTheme(theme) {
  document.body.classList.toggle("theme-light", theme === THEME_LIGHT);
  const btn = document.getElementById("btnTheme");
  if (btn) {
    btn.innerHTML = theme === THEME_LIGHT ? SVG_SUN : SVG_MOON;
  }
  try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}
}
function toggleTheme() {
  const next = document.body.classList.contains("theme-light") ? THEME_DARK : THEME_LIGHT;
  applyTheme(next);
}
(function initTheme() {
  let t = THEME_DARK;
  try { t = localStorage.getItem(THEME_KEY) || THEME_DARK; } catch (e) {}
  if (t !== THEME_LIGHT) t = THEME_DARK;
  applyTheme(t);
})();
// Discord 频道真实名称映射 {频道ID: 真实名}，由 /api/channel-names 拉取（仅展示，不写入 settings）
let channelNames = {};
// 转发组：当前活动组 id 持久化到 localStorage，刷新后恢复；
// 组内成员（channels/groups）随 settings 整体保存
const FG_STORAGE_KEY = "wuppo_active_fg";
const MAX_FG = 10;
// 「测试组」：固定 id / 名称，不可删除、不可改名，计入转发组数量上限
const TEST_FG_ID = "test";
const TEST_FG_NAME = "测试组";
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
  renderAutostart(document.getElementById("btnAutostart"), st.autostart);
}

// 开机自启按钮：√ / × 用 SVG 图标绘制（不使用文字字符），状态记录在 data-on 上供点击切换判断
function renderAutostart(btn, on) {
  if (!btn) return;
  btn.dataset.on = on ? "1" : "0";
  var icon = on
    ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>'
    : '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';
  btn.innerHTML = icon + "<span>开机自启</span>";
}

// 离线补发开关按钮：与开机自启同款（√/× SVG + 文字），data-on 记录状态供点击切换
function renderBackfillToggle(on) {
  const btn = document.getElementById("btnBackfillToggle");
  if (!btn) return;
  btn.dataset.on = on ? "1" : "0";
  var icon = on
    ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>'
    : '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';
  btn.innerHTML = icon + "<span>启用补发</span>";
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
  // 「从已注册项新增」：从全局已注册实体中选择加入当前转发组
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
      "当前转发组已包含全部已注册的" + (isGroup ? "群" : "频道") + "，没有可添加的条目"
    );
    return;
  }
  showConfirmSelect(
    "从已注册项新增",
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
    },
    {
      defaultChecked: false,
      onRowDelete: function (item) { deleteRegisteredItem(kind, item); },
    }
  );
}

// 白名单「＋ 新增」：从已注册用户中选择加入白名单（勾选 = 放行 enabled）
function openUserAddPicker() {
  const candidates = (settings.qq_user_openids || []).filter(function (u) { return u.enabled !== true; });
  if (!candidates.length) {
    showAlert("从已注册项新增", "当前没有未加入白名单的已注册用户");
    return;
  }
  showConfirmSelect(
    "从已注册项新增",
    candidates,
    function (u) {
      const uname = u.name || u.remark || identityName("users", u.openid) || "";
      return (uname + " " + u.openid).trim();
    },
    async function (selected) {
      selected.forEach(function (u) { u.enabled = true; });
      await saveAndReload();
    },
    {
      defaultChecked: false,
      onRowDelete: function (u) { deleteRegisteredItem("user", u); },
    }
  );
}

// 「从已注册项新增」窗口行尾「×」：删除该条目的注册记录（从全局实体列表移除；
// 保存时面板 _normalize_forwarding_groups 会剔除它在各转发组中的悬空引用）。
// 与表格行内"删除"（仅移出当前转发组、保留全局实体）严格区分。删除后需重新走
// 注册流程（重新扫描 / 重新注册审核）才能再次加入转发组或白名单。
function deleteRegisteredItem(kind, item) {
  const isGroup = kind === "group";
  const isChan = kind === "chan";
  const id = String(isChan ? item.id : item.openid);
  const name = isGroup
    ? (item.name || item.remark || identityName("groups", item.openid) || "")
    : isChan
      ? (item.name || "")
      : (item.name || item.remark || identityName("users", item.openid) || "");
  const label = ((name || "") + " " + id).trim();
  const kindName = isGroup ? "群" : isChan ? "频道" : "用户";
  hideConfirm();
  showConfirmDlg("删除注册记录",
    "确定删除「" + label + "」的注册记录？\n删除后该" + kindName + "不再出现在「从已注册项新增」列表中（若已加入转发组也会一并移出），再次使用需重新注册。",
    async function () {
      if (isGroup) {
        const idx = settings.qq_group_openids.findIndex(function (o) { return String(o.openid) === id; });
        if (idx >= 0) settings.qq_group_openids.splice(idx, 1);
      } else if (isChan) {
        const idx = settings.discord_channels.findIndex(function (o) { return String(o.id) === id; });
        if (idx >= 0) settings.discord_channels.splice(idx, 1);
      } else {
        const idx = settings.qq_user_openids.findIndex(function (o) { return String(o.openid) === id; });
        if (idx >= 0) settings.qq_user_openids.splice(idx, 1);
      }
      await saveAndReload();
      if (isGroup || isChan) openFgAddPicker(kind);
      else openUserAddPicker();
    }, "删除");
}
// 「从全部可读频道注册」：整合原「同步可读频道」——扫描 Bot 可读的频道，
// 弹窗勾选后登记为全局频道（默认未启用，只进入已注册项、不自动加入当前转发组）。
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
  if (!pending.length) { showAlert("注册结果", "没有发现新频道"); return; }
  showConfirmSelect("全部可读频道", pending, function (ch) { return ch.name + " (" + ch.id + ")"; }, async function (selected) {
    if (!selected.length) { showAlert("注册结果", "未选择任何频道"); return; }
    let added = 0;
    selected.forEach(function (ch) {
      const id = String(ch.id);
      if (known[id]) return;
      settings.discord_channels.push({id: id, name: String(ch.name || "").replace(/^#/, ""), enabled: false});
      known[id] = true;
      added++;
    });
    if (added) {
      await saveAndReload();
    } else {
      renderSettings();
    }
    showAlert("注册结果", "已注册 " + added + " 个新频道（默认未启用）");
  }, {defaultChecked: false, groupBy: function (ch) { return ch.guild; }});
}
function renderFgTabs() {
  const wrap = document.getElementById("fgTabs");
  if (!wrap) return;
  const list = getFgList();
  getActiveFg();   // 顺带校正 activeFgId（失效则回退并持久化）
  wrap.innerHTML = "";
  // 渲染顺序：测试组始终排在最左侧，其余组保持原顺序
  const ordered = list.slice().sort(function (a, b) {
    if (a.id === TEST_FG_ID) return -1;
    if (b.id === TEST_FG_ID) return 1;
    return 0;
  });
  ordered.forEach(function (fg) {
    const isActive = fg.id === activeFgId;
    const isTest = fg.id === TEST_FG_ID;
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "fgtab" + (isActive ? " active" : "");
    tab.dataset.kind = "fgTab";
    tab.dataset.id = fg.id;
    tab.textContent = fg.name;
    wrap.appendChild(tab);
    if (isActive) {
      // 重命名/删除只对当前组可用（与 tab 并列，避免按钮嵌套）；测试组无入口
      if (!isTest) {
        const rename = document.createElement("button");
        rename.type = "button";
        rename.className = "fgctrl rowicon";
        rename.dataset.kind = "fgRename";
        rename.innerHTML = SVG_PEN;
        wrap.appendChild(rename);
        if (list.length > 1) {
          const del = document.createElement("button");
          del.type = "button";
          del.className = "fgctrl rowicon";
          del.dataset.kind = "fgDel";
          del.innerHTML = SVG_TRASH;   // 与频道/群行内删除按钮同一白色垃圾桶图标
          wrap.appendChild(del);
        }
      }
    }
  });
  // 达到上限(10)后直接不显示新增按钮，删除一个后自动恢复
  if (list.length < MAX_FG) {
    const add = document.createElement("button");
    add.type = "button";
    add.className = "fgtab-add";
    add.id = "btnAddFg";
    add.textContent = "＋ 添加转发组";
    wrap.appendChild(add);
  }
}
function addForwardingGroup() {
  const list = getFgList();
  if (list.length >= MAX_FG) { showAlert("添加转发组", "最多 " + MAX_FG + " 个转发组"); return; }
  // 空组：不复制任何成员；名称自动取未占用的「转发组N」（从 1 起找）
  let n = 1;
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
  // 「测试组」为固定转发组，不允许重命名
  if (!fg || fgRenameActive || fg.id === TEST_FG_ID) return;
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
    gb.innerHTML = '<tr class="fgempty-row"><td colspan="6" class="fgempty-msg">还未新增群，点击下方「新增」按钮来新增第一个群吧！</td></tr>';
  }
  grpRows.forEach((g) => {
    const tr = document.createElement("tr");
    tr.setAttribute("data-id", String(g.openid));
    const gi = settings.qq_group_openids.indexOf(g);
    // 名称/账号：注册审核通过时写入（无注册信息的手动条目为空），
    // 名称缺省时回退显示身份库自动识别的群名
    const gname = g.name || identityName("groups", g.openid) || "";
    const gacc = g.qq_id || "";
    tr.innerHTML =
      '<td class="drag-handle" title="拖拽排序">' + SVG_GRIP + '</td>' +
      '<td style="text-align:center"><input type="checkbox" data-kind="group" data-i="' + gi + '"' + (g.enabled ? " checked" : "") + '></td>' +
      '<td>' +
        (gname ? '<div style="color:var(--fg);font-size:12px;word-break:break-all;">' + escapeHtml(gname) + '</div>' : '') +
      '</td>' +
      '<td style="font-size:12px;">' + escapeHtml(gacc) + '</td>' +
      '<td class="mono">' + escapeHtml(g.openid) + '</td>' +
      '<td style="text-align:right"><button class="danger rowicon" data-kind="fgGroupRemove" data-id="' + escapeHtml(String(g.openid)) + '">' + SVG_TRASH + '</button></td>';
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
    cb.innerHTML =
      '<tr class="fgempty-row"><td colspan="5" class="fgempty-msg">还未新增频道，点击下方「新增」按钮来新增第一个频道吧！</td></tr>';
  }
  chanRows.forEach((c) => {
    const tr = document.createElement("tr");
    tr.setAttribute("data-id", String(c.id));
    const gi = settings.discord_channels.indexOf(c);
    const realName = channelNames[c.id] || "";
    tr.innerHTML =
      '<td class="drag-handle" title="拖拽排序">' + SVG_GRIP + '</td>' +
      '<td style="text-align:center"><input type="checkbox" data-kind="chan" data-i="' + gi + '"' + (c.enabled ? " checked" : "") + '></td>' +
      '<td>' +
        '<span class="chan-name" data-kind="chanNameDisplay" data-i="' + gi + '">' +
          '<span class="chan-name-text">' + escapeHtml(c.name || "") + '</span>' +
          '<span class="chan-name-edit" data-kind="chanNameEdit" data-i="' + gi + '" title="编辑频道名称">' + SVG_PEN + '</span>' +
        '</span>' +
        (realName ? '<div style="color:var(--name-green);font-size:12px;">' + escapeHtml(realName) + '</div>' : '') +
      '</td>' +
      '<td class="mono">' + escapeHtml(c.id) + '</td>' +
      '<td><div style="display:flex;gap:4px;justify-content:flex-end;">' +
        '<button class="sec rowicon' + ((c.filter_usernames && c.filter_usernames.length) || (c.filter_keywords && c.filter_keywords.length) ? ' filter-active' : '') + '" data-kind="chanFilter" data-i="' + gi + '">' + SVG_FUNNEL + '</button>' +
        '<button class="danger rowicon" data-kind="fgChanRemove" data-id="' + escapeHtml(String(c.id)) + '">' + SVG_TRASH + '</button>' +
      '</div></td>';
    cb.appendChild(tr);
  });
  const ub = document.getElementById("userBody");
  if (ub) {
    ub.innerHTML = "";
    if (!(settings.qq_user_openids || []).length) {
      ub.innerHTML = '<tr class="fgempty-row"><td colspan="6" class="fgempty-msg">还未新增账号，点击下方「新增」按钮来新增第一个账号吧！</td></tr>';
    }
    (settings.qq_user_openids || []).forEach((u) => {
      const ui = settings.qq_user_openids.indexOf(u);   // 原始索引：增删改仍按 settings 数组定位
      const tr = document.createElement("tr");
      tr.setAttribute("data-id", String(u.openid));
      // 名称/账号：注册审核通过时写入（无注册信息的手动条目为空），
      // 名称缺省时回退显示身份库自动识别的昵称
      const uname = u.name || identityName("users", u.openid) || "";
      const uacc = u.qq_id || "";
      tr.innerHTML =
        '<td class="drag-handle" title="拖拽排序">' + SVG_GRIP + '</td>' +
        '<td style="text-align:center"><input type="checkbox" data-kind="user" data-i="' + ui + '"' + (u.enabled ? " checked" : "") + '></td>' +
        '<td>' +
          (uname ? '<div style="color:var(--fg);font-size:12px;word-break:break-all;">' + escapeHtml(uname) + '</div>' : '') +
        '</td>' +
        '<td style="font-size:12px;">' + escapeHtml(uacc) + '</td>' +
        '<td class="mono">' + escapeHtml(u.openid) + '</td>' +
        '<td style="text-align:right"><button class="danger rowicon" data-kind="userDel" data-i="' + ui + '">' + SVG_TRASH + '</button></td>';
      ub.appendChild(tr);
    });
  }
  // 离线补发设置
  renderBackfillToggle(settings.backfill_enabled !== false);
  const bfLimit = document.getElementById("backfillLimit");
  if (bfLimit) bfLimit.value = Number(settings.backfill_limit) > 0 ? settings.backfill_limit : 10;
  // 表头全选框状态仅反映「当前转发组」实际显示的成员（而非全部全局实体），
  // 避免隐藏的非本组成员导致半选误判：全部→勾选，全不→取消，部分→半选，无成员→两者均 false
  syncHeaderAll("groupAllEnabled", grpRows, "enabled");
  syncHeaderAll("chanAllEnabled", chanRows, "enabled");
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

// ---------------- 表格行拖拽排序（鼠标事件实现 + 数据数组持久化） ----------------
// 作用区域：转发组卡片的「Discord 转发频道 / QQ 接收群」与「私聊权限」三个表格。
// 拖拽真正重排 settings 数据数组（qq_group_openids / discord_channels / qq_user_openids），
// 并通过 saveAndReload() 走现有保存流程持久化（刷新后保持）；行只能在同一表格内排序。
let dndState = null;   // {body, tr, id, startX, startY} —— 正在拖拽的源行

document.addEventListener("mousedown", function (e) {
  const handle = e.target.closest(".drag-handle");
  if (!handle) return;                                    // 只允许按住手柄拖拽
  const tr = handle.closest("tr[data-id]");
  if (!tr) return;
  const body = tr.parentElement;
  if (!body || !body.id) return;
  e.preventDefault();                                     // 防止文本选择干扰拖动
  dndState = {body: body, tr: tr, id: tr.getAttribute("data-id"), startX: e.clientX, startY: e.clientY};
  tr.classList.add("drag-src");
});

document.addEventListener("mousemove", function (e) {
  if (!dndState) return;
  e.preventDefault();
  // 指针移动超过阈值才算拖拽（区分“单击手柄”）
  if (Math.abs(e.clientX - dndState.startX) < 3 && Math.abs(e.clientY - dndState.startY) < 3) return;
  const body = dndState.body;
  // 清除上次的插入指示
  body.querySelectorAll("tr.drop-before, tr.drop-after").forEach(function (r) {
    r.classList.remove("drop-before", "drop-after");
  });
  // 计算指针下的目标行（限制在同一区域）
  const under = document.elementFromPoint(e.clientX, e.clientY);
  const tr = under ? under.closest("tr[data-id]") : null;
  if (!tr || tr.parentElement !== body || tr === dndState.tr) return;
  const r = tr.getBoundingClientRect();
  const after = e.clientY > r.top + r.height / 2;
  tr.classList.add(after ? "drop-after" : "drop-before");
});

document.addEventListener("mouseup", function (e) {
  if (!dndState) return;
  e.preventDefault();
  const st = dndState;
  dndState = null;
  st.tr.classList.remove("drag-src");
  const body = st.body;
  body.querySelectorAll("tr.drop-before, tr.drop-after").forEach(function (r) {
    r.classList.remove("drop-before", "drop-after");
  });
  // 拖动距离太小视为单击，不重排
  if (Math.abs(e.clientX - st.startX) < 3 && Math.abs(e.clientY - st.startY) < 3) return;
  if (!st.tr.isConnected) return;                          // 行已被重建，放弃
  // 计算目标行（跨区域直接忽略）
  const under = document.elementFromPoint(e.clientX, e.clientY);
  const targetTr = under ? under.closest("tr[data-id]") : null;
  const targetBody = targetTr ? targetTr.parentElement : (under ? under.closest("tbody") : null);
  if (!targetBody || targetBody !== body) return;          // 禁止跨区域拖动
  if (targetTr && targetTr !== st.tr) {
    const r = targetTr.getBoundingClientRect();
    const after = e.clientY > r.top + r.height / 2;
    body.insertBefore(st.tr, after ? targetTr.nextSibling : targetTr);
  } else if (!targetTr && body.lastElementChild && body.lastElementChild !== st.tr) {
    body.appendChild(st.tr);                               // 拖到区域末尾空白处
  } else {
    return;                                                // 落在自己行上，无变化
  }
  // 读取拖拽后的显示顺序，重排对应 settings 数据数组并走现有保存流程
  const ids = [];
  body.querySelectorAll("tr[data-id]").forEach(function (r) { ids.push(r.getAttribute("data-id")); });
  if (!applyDndOrder(body.id, ids)) return;
  saveAndReload();                                         // 现有保存：POST settings + 重渲染
});

// 将拖拽后的显示顺序写回 settings 对应数据数组（成员按新顺序，非成员保持原相对位置）
function applyDndOrder(bodyId, newIds) {
  if (!newIds.length) return false;
  const pos = {};
  newIds.forEach(function (id, i) { pos[String(id)] = i; });
  let arr = null, memberSet = null;
  if (bodyId === "chanBody") {
    arr = settings.discord_channels;
    memberSet = memberIdSet(getActiveFg() && getActiveFg().channels);
  } else if (bodyId === "groupBody") {
    arr = settings.qq_group_openids;
    memberSet = memberIdSet(getActiveFg() && getActiveFg().groups);
  } else if (bodyId === "userBody") {
    arr = settings.qq_user_openids;
    memberSet = memberIdSet(arr && arr.map(function (u) { return u.openid; }));  // 用户表全量排序
  } else {
    return false;
  }
  if (!arr || !memberSet) return false;
  const getId = function (o) { return String(o.openid !== undefined ? o.openid : o.id); };
  // 校验：newIds 必须与当前可见成员一一对应（含数量一致），防止脏数据
  const members = arr.filter(function (o) { return memberSet[getId(o)]; });
  if (members.length !== newIds.length) return false;
  const byId = {};
  members.forEach(function (o) { byId[getId(o)] = o; });
  const ordered = newIds.map(function (id) { return byId[String(id)]; }).filter(Boolean);
  if (ordered.length !== newIds.length) return false;
  // 重排：成员按新顺序，非成员保持原相对位置（在原数组的成员槽位依次填入新顺序）
  let mi = 0;
  const result = [];
  arr.forEach(function (o) {
    if (memberSet[getId(o)]) result.push(ordered[mi++]);
    else result.push(o);
  });
  if (bodyId === "chanBody") settings.discord_channels = result;
  else if (bodyId === "groupBody") settings.qq_group_openids = result;
  else settings.qq_user_openids = result;
  return true;
}

// {id1: true, id2: true, ...} —— 用于判断某实体是否为当前区域的成员
function memberIdSet(arr) {
  const m = {};
  (arr || []).forEach(function (id) { m[String(id)] = true; });
  return m;
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

let backfillTotal = 0;  // 当前待补发总数，供「立即补发/清空」按钮可用状态判断

function updateBackfillButtons() {
  const disable = backfillTotal <= 0;
  ["btnBackfillRun", "btnBackfillClear"].forEach(function (id) {
    const b = document.getElementById(id);
    if (b) b.disabled = disable;
  });
}

function renderBackfill(data) {
  const el = document.getElementById("backfillPending");
  if (!el) return;
  // 仅显示数字：接口不可用/未运行/无待补发均显示 0
  backfillTotal = (data && data.ok) ? (data.total || 0) : 0;
  // 超过 99 条只显示 99+，按钮可用状态仍按真实数量判断
  el.textContent = backfillTotal > 99 ? "99+" : backfillTotal;
  el.style.color = backfillTotal > 0 ? "var(--pending)" : "";
  // 无待补发时禁用「立即补发」与「清空待补发」（disabled 自带半透明）
  updateBackfillButtons();
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

function showConfirmSelect(title, items, labelFn, onOkSelected, opts) {
  opts = opts || {};
  const defaultChecked = opts.defaultChecked !== false;
  const groupBy = opts.groupBy || null;
  document.getElementById("confirmTitle").textContent = title;
  const body = document.getElementById("confirmBody");
  body.innerHTML = "";
  const rows = [];
  function renderRow(it) {
    const row = document.createElement("label");
    row.style.display = "flex";
    row.style.alignItems = "center";
    row.style.gap = "8px";
    row.style.cursor = "pointer";
    row.style.padding = "2px 0";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = defaultChecked;
    cb.style.width = "16px";
    cb.style.height = "16px";
    const span = document.createElement("span");
    span.className = "mono";
    span.style.flex = "1";
    span.style.minWidth = "0";
    span.textContent = labelFn(it);
    row.appendChild(cb);
    row.appendChild(span);
    if (opts.onRowDelete) {
      // 「×」删除按钮：贴近窗口右边缘，删除该条目的注册记录
      const del = document.createElement("button");
      del.type = "button";
      del.textContent = "×";
      del.className = "danger rowicon";
      del.style.flex = "none";
      del.title = "删除注册记录";
      del.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        opts.onRowDelete(it);
      });
      row.appendChild(del);
    }
    body.appendChild(row);
    rows.push({it: it, cb: cb});
  }
  if (groupBy) {
    // 按分组字段聚合渲染：每个分组标题下方列出该组条目（保持组首次出现顺序）
    const order = [];
    const groups = {};
    items.forEach(function (it) {
      const g = String(groupBy(it) || "其他");
      if (!Object.prototype.hasOwnProperty.call(groups, g)) {
        groups[g] = [];
        order.push(g);
      }
      groups[g].push(it);
    });
    order.forEach(function (g) {
      const gh = document.createElement("div");
      gh.className = "sel-group-title";
      gh.textContent = g;
      body.appendChild(gh);
      groups[g].forEach(renderRow);
    });
  } else {
    items.forEach(renderRow);
  }
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
    empty.style.cssText = "color:var(--muted);padding:8px 0;";
    empty.textContent = "没有待审核的注册申请";
    body.appendChild(empty);
  }
  items.forEach(function (it) {
    const label = regReviewKind === "groups"
      ? "群号 " + (it.qq_id || "?") + " · " + (it.group_name || "未命名")
      : "QQ " + (it.qq_id || "?") + " · " + (it.nickname || "未命名");
    const row = document.createElement("div");
    row.style.cssText = "display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--panel-border);";
    const info = document.createElement("div");
    info.style.cssText = "flex:1;min-width:0;";
    info.innerHTML =
      '<div>' + escapeHtml(label) + '</div>' +
      '<div class="mono" style="font-size:11px;color:var(--muted);word-break:break-all;">' + escapeHtml(it.openid) + '</div>';
    row.appendChild(info);
    const ok = document.createElement("button");
    ok.className = "sec";
    ok.textContent = "√";
    ok.style.cssText = "color:var(--name-green);border-color:var(--ok-border);";
    ok.addEventListener("click", function () { reviewRegistration(it.openid, "approve"); });
    const no = document.createElement("button");
    no.className = "danger";
    no.textContent = "×";
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
  if (btn.classList.contains("modal-x")) {
    const closeMap = {
      confirmModal: hideConfirm,
      filterModal: closeFilterModal,
      aboutModal: function () { document.getElementById("aboutModal").style.display = "none"; },
    };
    const closer = closeMap[btn.dataset.close];
    if (closer) closer();
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
    btn.classList.toggle("folded", !folded);
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
    const fg = getActiveFg();
    // 「测试组」为固定转发组，不允许删除；普通转发组可全部删除（至少保留测试组）
    if (!fg || fg.id === TEST_FG_ID) return;
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
    openFgAddPicker("chan");
    return;
  }
  if (btn.id === "btnAddFgGroup") {
    openFgAddPicker("group");
    return;
  }
  if (btn.id === "btnScanChannels") {
    scanReadableChannels(btn);
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
    openUserAddPicker();
  } else if (btn.id === "btnSync") {
    // 群注册审核：自动发现的 openid 只记录，注册后才进入待审名单
    await openRegistrationReview("groups");
  } else if (btn.id === "btnSyncUsers") {
    // 用户注册审核：自动发现的 openid 只记录，注册后才进入待审名单
    await openRegistrationReview("users");
  } else if (btn.id === "btnQQPlatform") {
    window.open("https://q.qq.com/qqbot/dashboard/", "_blank");
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
      updateBackfillButtons();
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
        updateBackfillButtons();
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
  } else if (btn.id === "btnAutostart") {
    const enabled = btn.dataset.on !== "1";
    const r = await jpost("/api/autostart/set", {enabled: enabled});
    if (!r.ok) showAlert("设置失败", "设置开机自启失败");
    renderAutostart(btn, !!r.enabled);
  } else if (btn.id === "btnBackfillToggle") {
    settings.backfill_enabled = btn.dataset.on !== "1";
    await saveAndReload();
  } else if (btn.id === "btnAbout") {
    document.getElementById("aboutModal").style.display = "flex";
  } else if (btn.id === "aboutQq") {
    openAboutLink(ABOUT_LINKS.qq, "QQ");
  } else if (btn.id === "aboutBilibili") {
    openAboutLink(ABOUT_LINKS.bilibili, "Bilibili");
  } else if (btn.id === "aboutGithub") {
    openAboutLink(ABOUT_LINKS.github, "GitHub");
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
  } else if (btn.id === "btnTheme") {
    toggleTheme();
  }
});

document.addEventListener("change", async function (e) {
  const el = e.target;
  if (el.id === "backfillLimit") {
    let v = parseInt(el.value, 10);
    if (!(v > 0)) v = 10;
    if (v > 30) v = 30;
    settings.backfill_limit = v;
    el.value = v;
    await saveAndReload();
    return;
  }
  if (el.id === "logLevel") {
    const r = await jpost("/api/bot/loglevel", {level: el.value});
    if (!r.ok) showAlert("设置失败", r.msg || "设置失败");
    await refreshStatus();
    return;
  }
  if (el.id === "groupAllEnabled") {
    settings.qq_group_openids.forEach(function (g) { g.enabled = el.checked; });
    await saveAndReload();
    return;
  }
  if (el.id === "chanAllEnabled") {
    settings.discord_channels.forEach(function (c) { c.enabled = el.checked; });
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
  } else if (el.dataset.kind === "chan") {
    settings.discord_channels[Number(el.dataset.i)].enabled = el.checked;
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
    if (st.backfill_enabled !== undefined) {
      renderBackfillToggle(st.backfill_enabled !== false);
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

let pollingStarted = false;

async function init() {
  // 防重复启动：无论 init() 被调用多少次（重复加载/脚本二次执行），
  // 轮询定时器只创建一套，避免 /api/status → /api/health 出现多重轮询。
  if (pollingStarted) return;
  pollingStarted = true;
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
