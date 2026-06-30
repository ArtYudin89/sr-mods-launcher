'use strict';
// ───────── мост к Python ─────────
const api = () => window.pywebview.api;
const $ = (id) => document.getElementById(id);

let STATE = {};
let TREE = null;
const selected = new Set();          // выбранные iid листьев
const collapsed = new Set();         // свёрнутые узлы
const NODE = {};                     // iid -> узел (для быстрых проверок)
let busy = false;
let reconcileTimer = null;

// состояния и их описание (для легенды и фильтра)
const STATES = [
  { k: 'ok', label: 'установлен', badge: 'b-ok', tip: 'мод установлен — всё в порядке' },
  { k: 'upd', label: 'обновление', badge: 'b-upd', tip: 'для мода вышла новая версия' },
  { k: 'queued', label: 'добавлен', badge: 'b-queued', tip: 'добавлен в сборку, но ещё не установлен' },
  { k: 'avail', label: 'доступен', badge: 'b-avail', tip: 'есть в каталоге — можно установить' },
  { k: 'miss', label: 'недоступен', badge: 'b-miss', tip: 'в сборке есть, но в каталоге не найден' },
  { k: 'unknown', label: 'не в каталоге', badge: 'b-unknown', tip: 'установлен, но в каталоге его нет' },
  { k: 'load', label: 'каталог грузится', badge: 'b-load', tip: 'каталог ещё загружается' },
];
const filter = { states: new Set(STATES.map((s) => s.k)), inGame: false, inProfile: false };

// данные диалога добавления
let CAMPPACKS = {};
let packsByUnit = {};

// ───────── события из Python ─────────
window.__emit = (event, data) => {
  switch (event) {
    case 'log': appendLog(data); break;
    case 'tree_dirty': refreshTree(); break;
    case 'op_begin': onOpBegin(data); break;
    case 'op_end': onOpEnd(data); break;
    case 'progress': onProgress(data); break;
    case 'merge_plan': onMergePlan(data); break;
    case 'deps_confirm': onDepsConfirm(data); break;
  }
};

// ───────── инициализация ─────────
window.addEventListener('pywebviewready', init);

async function init() {
  STATE = await api().get_state();
  applyState();
  buildStateChecks();
  buildLegend();
  wireUI();
  refreshTree();
}

function applyState() {
  document.documentElement.dataset.theme = STATE.theme || 'dark';
  $('themeBtn').textContent = STATE.theme === 'light' ? '☀' : '🌙';
  const sel = $('profileSel');
  sel.innerHTML = '';
  (STATE.profiles || []).forEach((p) => {
    const o = document.createElement('option');
    o.value = o.textContent = p;
    if (p === STATE.current_profile) o.selected = true;
    sel.appendChild(o);
  });
  const gp = $('gamePath');
  if (STATE.game_path) { gp.textContent = '📁 ' + STATE.game_path; gp.classList.remove('unset'); }
  else { gp.textContent = 'Папка игры не выбрана'; gp.classList.add('unset'); }
  $('setupHint').style.display = STATE.game_path ? 'none' : 'flex';
  document.querySelectorAll('#viewSeg button').forEach((b) =>
    b.classList.toggle('on', b.dataset.mode === STATE.tree_mode));
  document.querySelectorAll('#nameSeg button').forEach((b) =>
    b.classList.toggle('on', b.dataset.nmode === (STATE.name_mode || 'folder')));
  updateColHeader();
  $('verBadge').textContent = STATE.is_rwt ? 'тестовая сборка (RWT)' : 'SR Mods Launcher';
  $('repoDot').classList.toggle('on', !!STATE.repo);
  $('tokenDot').classList.toggle('on', !!STATE.has_token);
  $('verboseChk').checked = !!STATE.log_verbose;
}

function updateColHeader() {
  // показываем измерение, ОБРАТНОЕ группировке
  $('colDim').textContent = (STATE.tree_mode === 'section') ? 'Папка' : 'Раздел';
}

// ───────── проводка ─────────
function wireUI() {
  $('themeBtn').onclick = toggleTheme;
  $('launchBtn').onclick = launchGame;
  $('gamePath').onclick = browseGame;
  $('chooseGameBtn').onclick = browseGame;
  $('settingsBtn').onclick = openSettings;
  $('profileSel').onchange = (e) => switchProfile(e.target.value);
  $('profileMenuBtn').onclick = openProfiles;

  document.querySelectorAll('#viewSeg button').forEach((b) => b.onclick = () => setViewMode(b.dataset.mode));
  document.querySelectorAll('#nameSeg button').forEach((b) => b.onclick = () => setNameMode(b.dataset.nmode));
  $('searchInp').oninput = renderTree;
  $('expandBtn').onclick = () => { collapsed.clear(); renderTree(); };
  $('collapseBtn').onclick = collapseAll;
  $('indexBtn').onclick = doReindex;
  $('clearModsBtn').onclick = doClearMods;
  $('refreshBtn').onclick = async () => {
    toast('Обновляю каталог и проверяю обновления…', 'ok');
    try { await api().refresh_remote(); } catch (e) {}
    const s = await api().get_state(); STATE = s; applyState();
    refreshTree();                          // бейджи «⬆ обновление» придут с tree_dirty по завершении сверки
  };

  // фильтр
  $('filterBtn').onclick = (e) => { e.stopPropagation(); $('filterPop').classList.toggle('hidden'); };
  $('fAll').onclick = () => { STATES.forEach((s) => filter.states.add(s.k)); syncStateChecks(); applyFilter(); };
  $('fNone').onclick = () => { filter.states.clear(); syncStateChecks(); applyFilter(); };
  $('fImportant').onclick = () => {
    filter.states = new Set(STATES.map((s) => s.k)); filter.states.delete('ok');
    syncStateChecks(); applyFilter();
  };
  $('fInGame').onchange = (e) => { filter.inGame = e.target.checked; applyFilter(); };
  $('fInProfile').onchange = (e) => { filter.inProfile = e.target.checked; applyFilter(); };
  $('legendBtn').onclick = () => $('legendBar').classList.toggle('hidden');

  // действия
  $('addBtn').onclick = openAdd;
  $('installBtn').onclick = installSelected;
  $('installSetBtn').onclick = installWholeSet;
  $('mergeBtn').onclick = startMerge;
  $('compatBtn').onclick = checkCompat;
  $('removeBtn').onclick = removeSelected;

  $('modcfgReadBtn').onclick = () => api().modcfg_to_profile().then((r) => {
    if (r.ok) { toast(`Считано ${r.count} модов из игры`, 'ok'); refreshTree(); } else toast(r.error, 'err');
  });
  $('modcfgWriteBtn').onclick = modcfgWrite;
  $('clearLogBtn').onclick = () => { $('logBox').innerHTML = ''; };
  $('verboseChk').onchange = (e) => api().set_verbose(e.target.checked);
  $('cancelBtn').onclick = () => api().cancel();

  // настройки
  $('setCancelBtn').onclick = () => hide('settingsOverlay');
  $('setSaveBtn').onclick = saveSettings;
  $('setBrowseBtn').onclick = async () => { const p = await api().browse_game(); if (p) $('setGamePath').value = p; };
  $('forkAddBtn').onclick = addFork;
  // профили
  $('createProfBtn').onclick = createProfile;
  $('deleteProfBtn').onclick = deleteProfile;
  $('profCloseBtn').onclick = () => hide('profileOverlay');
  $('confirmCancel').onclick = () => hide('confirmOverlay');
  // добавление
  document.querySelectorAll('#addModeSeg button').forEach((b) => b.onclick = () => setAddMode(b.dataset.amode));
  $('addCancelBtn').onclick = () => hide('addOverlay');
  $('addOkBtn').onclick = doAdd;
  $('addCamp').onchange = onAddCamp;
  $('addPack').onchange = onAddPack;
  $('addMod').onchange = onAddModSel;
  // обновление
  $('mergeApplyBtn').onclick = doMergeApply;
  $('mergeSkipBtn').onclick = () => { hide('mergeOverlay'); api().merge_skip(); };

  // закрытие по клику на фон — только для «безопасных» модалок
  // (confirm/merge завязаны на состояние бэкенда → закрываются только кнопками)
  ['settingsOverlay', 'profileOverlay', 'addOverlay', 'infoOverlay', 'compatOverlay'].forEach((id) =>
    $(id).onclick = (e) => { if (e.target === $(id)) $(id).classList.add('hidden'); });
  $('infoCloseBtn').onclick = () => hide('infoOverlay');
  $('compatCloseBtn').onclick = () => hide('compatOverlay');
  // контекстное меню (ПКМ)
  $('ctxOpenMod').onclick = () => { if (ctxMid) api().open_mod_folder(ctxMid); hideCtxMenu(); };
  $('ctxOpenMods').onclick = () => { api().open_mods_folder(); hideCtxMenu(); };
  document.addEventListener('click', hideCtxMenu);
  document.addEventListener('scroll', hideCtxMenu, true);
  // Esc закрывает верхнюю открытую модалку / поповер
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!$('ctxMenu').classList.contains('hidden')) { hideCtxMenu(); return; }
    if (!$('filterPop').classList.contains('hidden')) { $('filterPop').classList.add('hidden'); return; }
    for (const id of ['infoOverlay', 'compatOverlay', 'addOverlay', 'profileOverlay', 'settingsOverlay']) {
      if (!$(id).classList.contains('hidden')) { hide(id); return; }
    }
    if (!$('confirmOverlay').classList.contains('hidden')) { $('confirmCancel').click(); return; }
    if (!$('mergeOverlay').classList.contains('hidden')) { $('mergeSkipBtn').click(); return; }
  });
  // закрыть фильтр-поповер по клику вне
  document.addEventListener('click', (e) => {
    if (!$('filterPop').classList.contains('hidden') &&
        !$('filterPop').contains(e.target) && e.target !== $('filterBtn')) {
      $('filterPop').classList.add('hidden');
    }
  });
}

// ───────── фильтр ─────────
function buildStateChecks() {
  $('stateChecks').innerHTML = STATES.map((s) => `
    <label class="pop-chk"><input type="checkbox" class="fstate" value="${s.k}" ${filter.states.has(s.k) ? 'checked' : ''}>
      <span class="badge ${s.badge}">${s.label}</span></label>`).join('');
  document.querySelectorAll('.fstate').forEach((c) => c.onchange = () => {
    c.checked ? filter.states.add(c.value) : filter.states.delete(c.value);
    applyFilter();
  });
}
function syncStateChecks() {
  document.querySelectorAll('.fstate').forEach((c) => c.checked = filter.states.has(c.value));
}
function applyFilter() {
  const n = (filter.states.size < STATES.length ? 1 : 0) + (filter.inGame ? 1 : 0) + (filter.inProfile ? 1 : 0);
  $('filterCount').textContent = n ? String(n) : '';
  renderTree();
}
function passFilter(node) {
  if (!filter.states.has(node.status_class)) return false;
  if (filter.inGame && !node.in_game) return false;
  if (filter.inProfile && !node.in_profile) return false;
  return true;
}

// ───────── легенда ─────────
function buildLegend() {
  const parts = STATES.map((s) =>
    `<span class="lg"><span class="badge ${s.badge}">${s.label}</span> — ${s.tip}</span>`);
  parts.push('<span class="lg"><span class="toggle ro on">✓</span> «В игре» — мод подключён в самой игре</span>');
  parts.push('<span class="lg"><span class="toggle on">✓</span> «В сборке» — мод входит в текущую сборку (кликабельно)</span>');
  $('legendBar').innerHTML = parts.join('');
}

// ───────── тема ─────────
function toggleTheme() {
  const next = (document.documentElement.dataset.theme === 'light') ? 'dark' : 'light';
  document.documentElement.dataset.theme = next;
  $('themeBtn').textContent = next === 'light' ? '☀' : '🌙';
  STATE.theme = next;
  api().set_theme(next);
}

// ───────── дерево ─────────
async function refreshTree() {
  TREE = await api().get_tree(false);
  STATE.base = TREE.base;
  renderTree();
  const q = TREE.queue || {};
  $('queueLbl').textContent = q.count ? `в сборке: ${q.count}${q.size ? ' · ~' + q.size : ''}` : '';
}

function groupKey(camp, pack) { return pack ? `c:${camp}|p:${pack}` : `c:${camp}`; }
function colValue(n) { return STATE.tree_mode === 'section' ? n.folder : n.section; }

function renderTree() {
  for (const k in NODE) delete NODE[k];
  const body = $('treeBody');
  const camps = (TREE && TREE.camps) || [];
  if (!camps.length) {
    body.innerHTML = `<div class="tree-empty">Пока пусто.<br>
      Нажмите «➕ Добавить мод», чтобы собрать набор, затем «🧩 Установить всю сборку».</div>`;
    updateActionButtons();
    return;
  }
  const q = ($('searchInp').value || '').trim().toLowerCase();
  const visible = (n) => passFilter(n) &&
    (!q || n.label.toLowerCase().includes(q) || (n.name || '').toLowerCase().includes(q));

  const rows = [];
  for (const camp of camps) {
    const directMods = camp.mods.filter(visible);
    const packsVis = camp.packs.map((pk) => ({ pk, mods: pk.mods.filter(visible) }))
      .filter((x) => x.mods.length);
    if (!directMods.length && !packsVis.length) continue;

    const ckey = groupKey(camp.label, null);
    const cCol = collapsed.has(ckey);
    const cnt = directMods.length + packsVis.reduce((a, x) => a + x.mods.length, 0);
    rows.push(groupRow('camp', camp.label, ckey, cCol, cnt));
    if (cCol) continue;
    for (const n of directMods) { NODE[n.iid] = n; rows.push(leafRow(n, 2)); }
    for (const { pk, mods } of packsVis) {
      const pkey = groupKey(camp.label, pk.label);
      const pCol = collapsed.has(pkey);
      rows.push(groupRow('pack', pk.label, pkey, pCol, mods.length));
      if (pCol) continue;
      for (const n of mods) { NODE[n.iid] = n; rows.push(leafRow(n, 3)); }
    }
  }
  body.innerHTML = rows.length ? rows.join('')
    : `<div class="tree-empty">Ничего не подходит под фильтр.<br>Снимите фильтр или измените поиск.</div>`;

  body.querySelectorAll('.row.group').forEach((el) => el.onclick = () => toggleCollapse(el.dataset.key));
  body.querySelectorAll('.row.leaf').forEach((el) => el.onclick = (e) => onLeafClick(e, el.dataset.iid));
  body.querySelectorAll('.toggle.click').forEach((el) =>
    el.onclick = (e) => { e.stopPropagation(); onToggleClick(el); });
  body.querySelectorAll('.info-btn').forEach((el) =>
    el.onclick = (e) => { e.stopPropagation(); openModInfo(el.dataset.mid); });
  body.querySelectorAll('.row.leaf').forEach((el) =>
    el.oncontextmenu = (e) => { e.preventDefault(); openCtxMenu(e, el.dataset.iid); });
  updateActionButtons();
}

function groupRow(kind, label, key, isCol, count) {
  const icon = kind === 'camp' ? '🗂' : '■';
  const tw = isCol ? '▸' : '▾';
  const lvl = kind === 'camp' ? 'lvl1' : 'lvl2';
  return `<div class="row group ${kind} ${lvl}" data-key="${esc(key)}">
    <div class="name"><span class="tw">${tw}</span><span class="label">${icon} ${esc(label)}</span></div>
    <div class="cell"></div><div class="cell"></div><div class="cell"></div>
    <div class="cell"><span class="tag">${count}</span></div></div>`;
}

function leafRow(n, lvl) {
  const sel = selected.has(n.iid) ? ' sel' : '';
  const inGame = `<span class="toggle ro${n.in_game ? ' on' : ''}" title="${n.in_game ? 'подключён в игре' : 'не подключён в игре'}">${n.in_game ? '✓' : ''}</span>`;
  const inProf = n.mid
    ? `<span class="toggle click${n.in_profile ? ' on' : ''}" data-mid="${esc(n.mid)}" data-on="${n.in_profile ? 1 : 0}" title="нажмите, чтобы включить/выключить в сборке">${n.in_profile ? '✓' : ''}</span>`
    : '';
  const info = n.has_info
    ? `<span class="info-btn" data-mid="${esc(n.mid)}" title="Подробнее о моде">ⓘ</span>` : '';
  const desc = n.desc ? `<div class="mdesc">${esc(n.desc)}</div>` : '';
  const disp = (STATE.name_mode === 'module' && n.name) ? n.name : n.label;
  // во втором режиме рядом показываем папку мелким, чтобы не терять ориентир
  const alt = (STATE.name_mode === 'module' && n.name && n.name !== n.label)
    ? `<span class="alt-name" title="имя папки на диске">${esc(n.label)}</span>` : '';
  return `<div class="row leaf lvl${lvl}${sel}" data-iid="${esc(n.iid)}">
    <div class="name"><span class="tw">·</span>
      <span class="label-wrap"><span class="label">${esc(disp)}${alt}</span>${desc}</span>${info}</div>
    <div class="cell">${esc(colValue(n) || '')}</div>
    <div class="cell">${inGame}</div>
    <div class="cell">${inProf}</div>
    <div class="cell"><span class="badge b-${n.status_class}">${esc(n.status)}</span></div></div>`;
}

function toggleCollapse(key) { if (busy) return; collapsed.has(key) ? collapsed.delete(key) : collapsed.add(key); renderTree(); }
function collapseAll() {
  (TREE.camps || []).forEach((c) => {
    collapsed.add(groupKey(c.label, null));
    c.packs.forEach((p) => collapsed.add(groupKey(c.label, p.label)));
  });
  renderTree();
}

// ───────── выбор ─────────
function onLeafClick(e, iid) {
  if (!iid) return;
  if (e.ctrlKey || e.metaKey) {
    selected.has(iid) ? selected.delete(iid) : selected.add(iid);
  } else {
    const only = selected.size === 1 && selected.has(iid);
    selected.clear();
    if (!only) selected.add(iid);
  }
  renderTree();
}
function selectedPidx() {
  return [...selected].filter((i) => /^p\d+$/.test(i)).map((i) => parseInt(i.slice(1)));
}
function updateActionButtons() {
  const pidx = selectedPidx();
  const mergeSel = [...selected].some((i) => NODE[i] && NODE[i].mergeable);
  const hasUpdates = Object.keys(NODE).some((i) => NODE[i].mergeable && NODE[i].status_class === 'upd');
  const hasSet = !!(TREE && TREE.queue && TREE.queue.count);
  $('addBtn').disabled = busy;
  $('installBtn').disabled = busy || !pidx.length;
  $('removeBtn').disabled = busy || !pidx.length;
  const mb = $('mergeBtn');
  if (mergeSel) {                                  // есть выделение → обновить выбранные
    mb.textContent = '🔀 Обновить выбранные';
    mb.disabled = busy;
  } else {                                         // нет выделения → обновить все с обновлением
    mb.textContent = '🔀 Обновить все';
    mb.disabled = busy || !hasUpdates;
  }
  $('installSetBtn').disabled = busy || !hasSet;
  $('compatBtn').disabled = busy;
}

// ───────── toggle «в сборке» с проверкой зависимостей/конфликтов ─────────
function setToggleVisual(mid, on) {
  // обновить все строки/узлы с этим mid (модель в памяти + DOM)
  for (const k in NODE) if (NODE[k].mid === mid) NODE[k].in_profile = on;
  (TREE.camps || []).forEach((c) => {
    [...c.mods, ...c.packs.flatMap((p) => p.mods)].forEach((n) => { if (n.mid === mid) n.in_profile = on; });
  });
  document.querySelectorAll(`.toggle.click[data-mid="${cssEsc(mid)}"]`).forEach((t) => {
    t.classList.toggle('on', on); t.textContent = on ? '✓' : ''; t.dataset.on = on ? '1' : '0';
  });
}
function applyToggleNow(mid, on, add, disable) {
  setToggleVisual(mid, on);
  (add || []).forEach((m) => setToggleVisual(m, true));
  (disable || []).forEach((m) => setToggleVisual(m, false));
  api().toggle_enabled(mid, on, add || [], disable || []).then(refreshTree);
}
function onToggleClick(el) {
  if (busy) return;                       // во время операции не трогаем подключение
  const mid = el.dataset.mid;
  const on = el.dataset.on !== '1';
  if (on) {
    // включение: каскад зависимостей/конфликтов/нехватки
    api().plan_enable(mid).then((r) => {
      const c = r && r.cascade;
      if (!c) { applyToggleNow(mid, true); return; }
      showCascade(mid, c);
    }).catch(() => applyToggleNow(mid, true));
  } else {
    // выключение: каскад зависящих модов (их надо отключить следом)
    api().plan_disable(mid).then((r) => {
      const c = r && r.cascade;
      if (!c) { applyToggleNow(mid, false); return; }
      showDisableCascade(mid, c);
    }).catch(() => applyToggleNow(mid, false));
  }
}

function showCascade(mid, c) {
  const myName = nameOfMid(mid);
  let body = `Подключение мода <b>${esc(myName)}</b> повлечёт изменения:`;
  if (c.add && c.add.length) {
    const names = c.add.map((m, i) => esc((c.add_names && c.add_names[i]) || m));
    body += `<div class="csc add"><b>➕ подключатся (зависимости):</b><br>${names.join(', ')}</div>`;
  }
  if (c.disable && c.disable.length) {
    const names = c.disable.map((m, i) => esc((c.disable_names && c.disable_names[i]) || m));
    body += `<div class="csc del"><b>➖ отключатся (конфликт):</b><br>${names.join(', ')}</div>`;
  }
  if (c.missing && c.missing.length) {
    body += `<div class="note" style="border-left-color:var(--danger);margin-top:8px">
      ⛔ Невозможно подключить: отсутствует необходимый мод — <b>${esc(c.missing.join(', '))}</b>.<br>
      Сначала добавьте его в сборку или установите.</div>`;
  }
  if (c.block) {                           // обязательной зависимости нет нигде — блок
    confirmBox('Нельзя подключить мод', body, null, null,
      { okHidden: true, cancelLabel: 'Понятно' });
    return;
  }
  body += `<div style="margin-top:10px">Продолжить?</div>`;
  confirmBox('Подключить мод?', body, () => applyToggleNow(mid, true, c.add, c.disable));
}

function showDisableCascade(mid, c) {
  const myName = nameOfMid(mid);
  const names = (c.disable || []).map((m, i) => esc((c.disable_names && c.disable_names[i]) || m));
  const body = `Отключение мода <b>${esc(myName)}</b> повлечёт изменения:
    <div class="csc del"><b>➖ также отключатся (зависят от него):</b><br>${names.join(', ')}</div>
    <div style="margin-top:10px">Продолжить?</div>`;
  confirmBox('Отключить мод?', body, () => applyToggleNow(mid, false, [], c.disable));
}

function nameOfMid(mid) {
  for (const k in NODE) if (NODE[k].mid === mid) {
    return (STATE.name_mode === 'module' && NODE[k].name) ? NODE[k].name : NODE[k].label;
  }
  return mid.split('/').pop();
}
function cssEsc(s) { return String(s).replace(/["\\]/g, '\\$&'); }

// ───────── действия установки/удаления ─────────
function installSelected() {
  const pidx = selectedPidx();
  if (!pidx.length) { toast('Выберите строку сборки (📋) в списке', 'err'); return; }
  confirmBox('Установить выбранное?',
    `Будет скачано и установлено: <b>${pidx.length}</b> позиц.<br>Это может занять время и трафик. Продолжить?`,
    () => api().install(pidx).then(handleOpStart));
}
function installWholeSet() {
  // запускаем резолв зависимостей; подтверждение покажется по событию deps_confirm
  api().install_set_with_deps().then(handleOpStart);
}
function onDepsConfirm(d) {
  const order = d.order || [], deps = new Set(d.added_deps || []);
  const lines = order.map((mid) => '• ' + mid + (deps.has(mid) ? '   (зависимость)' : ''));
  if ((d.count || 0) > order.length) lines.push(`… и ещё ${d.count - order.length}`);
  let head = `Будет установлено модов из каталога: <b>${d.count || 0}</b>`;
  if (d.bulk) head += ` + паков/лагерей: <b>${d.bulk}</b>`;
  if (deps.size) head += ` <span style="color:var(--muted)">(из них зависимостей: ${deps.size})</span>`;
  let body = head + '.';
  if (lines.length) body += `<div style="margin-top:8px;font-family:var(--mono);font-size:12px;max-height:190px;overflow:auto;color:var(--muted)">${lines.map(esc).join('<br>')}</div>`;
  if (d.missing_deps && d.missing_deps.length)
    body += `<div class="note">⚠ Не найдены в каталоге: ${esc(d.missing_deps.join(', '))}</div>`;
  if (d.conflicts && d.conflicts.length)
    body += `<div class="note">⚠ Конфликты (показаны, не снимаются): ${d.conflicts.map((p) => esc(p[0] + '⟷' + p[1])).join('; ')}</div>`;
  if (!order.length && !d.bulk) body += '<div class="note">Нечего устанавливать.</div>';
  confirmBox('Установить всю сборку', body,
    () => api().confirm_install_deps(),
    () => api().cancel_deps());
}
function removeSelected() {
  const pidx = selectedPidx();
  if (!pidx.length) return;
  confirmBox('Убрать из сборки?',
    `Будет убрано из сборки: <b>${pidx.length}</b> позиц.<br>
     <span style="color:var(--muted)">Файлы на диске не удаляются — снимается только пометка «в сборке».</span>`,
    () => api().remove_pidx(pidx).then(() => { selected.clear(); refreshTree(); }));
}
function handleOpStart(r) { if (!r || !r.ok) toast((r && r.error) || 'Не удалось запустить', 'err'); }

function doReindex() { api().reindex().then(handleOpStart); }

async function doClearMods() {
  const info = await api().mods_info();
  if (!info.ok) { toast(info.error, 'err'); return; }
  if (!info.count) { toast('Папка Mods уже пуста', 'ok'); return; }
  confirmBox('Очистить папку Mods?',
    `Будут удалены <b>ВСЕ</b> файлы из папки модов игры:<br>
     <span style="font-family:var(--mono);font-size:12px;color:var(--muted)">${esc(info.path)}</span><br>
     Файлов: <b>${info.count}</b>.<br><br>
     <span style="color:var(--danger)">Действие необратимо.</span> Сама игра не затрагивается — только моды.`,
    () => api().clear_mods().then((r) => {
      if (r.ok) { toast(`Удалено файлов: ${r.removed}`, 'ok'); refreshTree(); }
      else toast(r.error, 'err');
    }));
}

// ───────── совместимость ─────────
async function checkCompat() {
  $('compatBody').innerHTML = '<div class="sub">Проверяю…</div>';
  show('compatOverlay');
  let r;
  try { r = await api().check_compat(); }
  catch (e) { $('compatBody').innerHTML = '<div class="sub">Ошибка проверки.</div>'; return; }
  const items = (r && r.items) || [];
  const ic = { ok: '✓', warn: '⚠', info: 'ⓘ' };
  $('compatBody').innerHTML = items.map((it) =>
    `<div class="compat-row ${it.level}"><span class="ci">${ic[it.level] || '•'}</span>
       <span>${esc(it.text)}</span></div>`).join('');
  const warns = items.filter((it) => it.level === 'warn').length;
  toast(warns ? `Замечаний: ${warns}` : 'Проблем не найдено ✓', warns ? 'err' : 'ok');
}

// ───────── ModCFG ─────────
function modcfgWrite() {
  confirmBox('Записать сборку в игру?',
    `В игре будут подключены ровно те моды, что отмечены «в сборке».<br>
     Текущее подключение в игре будет перезаписано. Продолжить?`,
    () => api().profile_to_modcfg().then((r) => {
      if (!r.ok) { toast(r.error, 'err'); return; }
      let m = `Подключено в игре: ${r.count}`;
      if (r.missing && r.missing.length) m += ` (но ${r.missing.length} нет на диске)`;
      toast(m, r.missing && r.missing.length ? 'err' : 'ok');
      refreshTree();
    }));
}

// ───────── запуск/папка ─────────
async function launchGame() { const r = await api().launch_game(); r.ok ? toast('Запускаю игру…', 'ok') : toast(r.error, 'err'); }
async function browseGame() {
  const p = await api().browse_game();
  if (p) { STATE.game_path = p; applyState(); refreshTree(); toast('Папка игры выбрана', 'ok'); }
}

// ───────── профили/настройки ─────────
async function switchProfile(name) { STATE = await api().switch_profile(name); applyState(); selected.clear(); refreshTree(); }
function openProfiles() {
  $('delProfName').textContent = STATE.current_profile;
  $('delProfNote').style.display = STATE.current_profile === 'default' ? 'none' : 'block';
  $('newProfName').value = '';
  show('profileOverlay');
}
async function createProfile() {
  const name = $('newProfName').value.trim(); if (!name) return;
  const r = await api().new_profile(name);
  if (!r.ok) { toast(r.error, 'err'); return; }
  STATE = r.state; applyState(); selected.clear(); refreshTree(); hide('profileOverlay'); toast('Сборка создана', 'ok');
}
async function deleteProfile() {
  const r = await api().delete_profile(STATE.current_profile);
  if (!r.ok) { toast(r.error, 'err'); return; }
  STATE = r.state; applyState(); selected.clear(); refreshTree(); hide('profileOverlay'); toast('Сборка удалена', 'ok');
}
let FORKS = [];   // [{repo, token?, has_token}] — редактируемая копия списка форков
function renderForks() {
  const box = $('forkList');
  if (!FORKS.length) { box.innerHTML = '<div class="sub" style="opacity:.7">Форки не добавлены.</div>'; return; }
  box.innerHTML = FORKS.map((f, i) => `
    <div class="fork-row" style="display:flex;align-items:center;gap:6px;margin:4px 0">
      <span style="opacity:.6;width:18px;text-align:right">${i + 1}.</span>
      <span style="flex:1;font-family:monospace">${esc(f.repo)}${f.has_token ? ' 🔒' : ''}</span>
      <button class="btn ghost" data-fk="up" data-i="${i}" title="Выше (приоритетнее)" ${i === 0 ? 'disabled' : ''}>↑</button>
      <button class="btn ghost" data-fk="down" data-i="${i}" title="Ниже" ${i === FORKS.length - 1 ? 'disabled' : ''}>↓</button>
      <button class="btn ghost danger" data-fk="del" data-i="${i}" title="Удалить">✕</button>
    </div>`).join('');
  box.querySelectorAll('button[data-fk]').forEach((b) => b.onclick = () => {
    const i = +b.dataset.i, act = b.dataset.fk;
    if (act === 'del') FORKS.splice(i, 1);
    else if (act === 'up' && i > 0) { [FORKS[i - 1], FORKS[i]] = [FORKS[i], FORKS[i - 1]]; }
    else if (act === 'down' && i < FORKS.length - 1) { [FORKS[i + 1], FORKS[i]] = [FORKS[i], FORKS[i + 1]]; }
    renderForks();
  });
}
function addFork() {
  const repo = $('forkRepo').value.trim();
  if (!repo) { toast('Укажите owner/repo дополнительного репозитория', 'err'); return; }
  if (FORKS.some((f) => f.repo === repo)) { toast('Такой репозиторий уже в списке', 'err'); return; }
  const token = $('forkToken').value.trim();
  FORKS.push({ repo, token, has_token: !!token });
  $('forkRepo').value = ''; $('forkToken').value = '';
  renderForks();
}
function openSettings() {
  $('setGamePath').value = STATE.game_path || '';
  $('setBase').value = STATE.base || '';
  $('setRepo').value = STATE.repo || '';
  $('setToken').value = '';
  $('forkRepo').value = ''; $('forkToken').value = '';
  FORKS = (STATE.forks || []).map((f) => ({ repo: f.repo, has_token: f.has_token, token: '' }));
  renderForks();
  show('settingsOverlay');
}
async function saveSettings() {
  const token = $('setToken').value;
  // форки сохраняем отдельным вызовом; пустой token сохранит ранее введённый (бэкенд)
  const fr = await api().set_forks(FORKS.map((f) => ({ repo: f.repo, token: f.token || '' })));
  STATE = await api().save_settings($('setGamePath').value, $('setRepo').value,
    token === '' ? null : token, $('setBase').value);
  if (fr && fr.forks) STATE.forks = fr.forks;
  applyState(); refreshTree(); hide('settingsOverlay'); toast('Настройки сохранены', 'ok');
}
async function setViewMode(mode) {
  document.querySelectorAll('#viewSeg button').forEach((b) => b.classList.toggle('on', b.dataset.mode === mode));
  STATE.tree_mode = mode; updateColHeader();
  await api().set_tree_mode(mode);
  refreshTree();
}
async function setNameMode(mode) {
  document.querySelectorAll('#nameSeg button').forEach((b) => b.classList.toggle('on', b.dataset.nmode === mode));
  STATE.name_mode = mode;
  await api().set_name_mode(mode);
  renderTree();                         // имя уже есть в узлах — перерисовки достаточно
}

// ───────── добавление мода ─────────
async function openAdd() {
  setAddMode('src');
  $('addStatus').textContent = 'Загрузка списка паков…';
  $('addCamp').innerHTML = '<option value="">— выберите лагерь —</option>';
  $('addPack').innerHTML = ''; $('addPack').disabled = true;
  $('addMod').innerHTML = ''; $('addMod').disabled = true;
  $('addUrl').value = '';
  show('addOverlay');
  const r = await api().get_camp_packs();
  if (!r.ok) { $('addStatus').textContent = 'Ошибка: ' + r.error; return; }
  CAMPPACKS = r.camps || {};
  Object.keys(CAMPPACKS).sort().forEach((c) => {
    const o = document.createElement('option'); o.value = o.textContent = c; $('addCamp').appendChild(o);
  });
  $('addStatus').textContent = `Лагерей: ${Object.keys(CAMPPACKS).length}`;
}
function setAddMode(mode) {
  document.querySelectorAll('#addModeSeg button').forEach((b) => b.classList.toggle('on', b.dataset.amode === mode));
  $('addSrc').style.display = mode === 'src' ? 'block' : 'none';
  $('addFork').style.display = mode === 'fork' ? 'block' : 'none';
  $('addOverlay').dataset.mode = mode;
}
function onAddCamp() {
  const camp = $('addCamp').value;
  const packs = CAMPPACKS[camp] || [];
  packsByUnit = {};
  $('addPack').innerHTML = '<option value="">★ весь лагерь</option>';
  packs.forEach((p) => {
    packsByUnit[p.unit] = p;
    const o = document.createElement('option');
    o.value = p.unit; o.textContent = `${p.name}  [${p.unit}]`;
    $('addPack').appendChild(o);
  });
  $('addPack').disabled = !camp;
  $('addMod').innerHTML = ''; $('addMod').disabled = true;
}
async function onAddPack() {
  $('addMod').innerHTML = ''; $('addMod').disabled = true;
  const unit = $('addPack').value;
  if (!unit) return;                          // весь лагерь
  const p = packsByUnit[unit]; if (!p) return;
  $('addStatus').textContent = 'Загрузка модов пака…';
  const r = await api().get_unit_mods(p.camp, p.unit);
  if (!r.ok) { $('addStatus').textContent = 'Ошибка: ' + r.error; return; }
  $('addMod').innerHTML = '<option value="">★ весь пак</option>';
  (r.mods || []).forEach((m) => { const o = document.createElement('option'); o.value = o.textContent = m; $('addMod').appendChild(o); });
  $('addMod').disabled = false;
  $('addStatus').textContent = `Модов в паке: ${(r.mods || []).length}`;
}
async function onAddModSel() {
  const mid = $('addMod').value;
  if (!mid || mid === '_base') { $('addStatus').textContent = mid ? 'Общие файлы игры' : ''; return; }
  $('addStatus').textContent = '…';
  let r; try { r = await api().get_mod_info(mid); } catch (e) { r = null; }
  const i = r && r.ok ? r.info : null;
  $('addStatus').innerHTML = i && (i.small || i.full)
    ? `<b>${esc(i.name || mid.split('/').pop())}</b> — ${esc(i.small || i.full).replace(/\n/g, ' ')}`
    : esc(mid);
}
async function doAdd() {
  const mode = $('addOverlay').dataset.mode || 'src';
  let payload;
  if (mode === 'fork') {
    const url = $('addUrl').value.trim();
    if (!url) { toast('Укажите ссылку на дескриптор', 'err'); return; }
    payload = { mode: 'fork', url };
  } else {
    const camp = $('addCamp').value;
    if (!camp) { toast('Выберите лагерь', 'err'); return; }
    const unit = $('addPack').value;
    const pack = unit ? packsByUnit[unit] : null;
    payload = { mode: 'src', camp, pack: pack ? { camp: pack.camp, unit: pack.unit, name: pack.name } : null, mod: $('addMod').value };
  }
  const r = await api().add_mod(payload);
  if (!r.ok) { toast(r.error, 'err'); return; }
  hide('addOverlay'); refreshTree(); toast('Добавлено в сборку', 'ok');
}

// ───────── обновление с сохранением правок ─────────
function startMerge() {
  let iids = [...selected].filter((i) => NODE[i] && NODE[i].mergeable);
  if (!iids.length) {
    // ничего не выбрано → обновить ВСЕ моды с обновлением (значок ⬆), без дублей по mid
    const seen = new Set();
    iids = Object.keys(NODE).filter((i) => {
      const n = NODE[i];
      if (!(n.mergeable && n.status_class === 'upd')) return false;
      if (n.mid) { if (seen.has(n.mid)) return false; seen.add(n.mid); }
      return true;
    });
    if (!iids.length) {
      toast('Обновлений не найдено. Нажмите «⟳ Обновить», чтобы проверить.', 'err');
      return;
    }
  }
  api().start_merge(iids).then((r) => { if (!r.ok) toast(r.error, 'err'); });
}
function onMergePlan(plan) {
  const s = plan.summary || {};
  $('mergeHead').innerHTML =
    `<b>${esc(plan.id || '?')}</b> · версия ${esc(plan.version_old || '—')} → ${esc(plan.version_new || '—')}<br>
     авто-слить: ${s.automerge || 0} · обновить: ${s.update || 0} · добавить: ${s.add || 0} ·
     удалить: ${s.deleted_clean || 0} · ваших правок сохранится: ${s.player_only || 0} ·
     <b style="color:var(--warn)">конфликтов: ${s.conflicts || 0}</b>`;
  // группы (кроме «без изменений»)
  const groups = plan.groups || {}; const labels = plan.labels || {};
  const order = ['conflict_text', 'conflict_binary', 'conflict_deleted', 'automerge', 'update', 'add', 'deleted_clean', 'player_only'];
  let html = '';
  for (const st of order) {
    const files = groups[st]; if (!files || !files.length) continue;
    html += `<div class="mg-group"><div class="mg-title">${esc(labels[st] || st)} (${files.length})</div>
      <div class="mg-files">${files.map(esc).join('<br>')}</div></div>`;
  }
  $('mergeGroups').innerHTML = html;
  // конфликты с выбором решения
  const conflicts = plan.conflicts || [];
  $('mergeConflicts').innerHTML = conflicts.length
    ? `<div class="mg-title" style="margin-top:10px">Как поступить с конфликтами (${conflicts.length}):</div>` +
      conflicts.map((c, i) => `
        <div class="mg-conflict">
          <div class="path">${esc(c.display)}</div>
          <select data-relpath="${esc(c.relpath)}" id="cf${i}">
            ${c.options.map((o) => `<option value="${esc(o.code)}" ${o.code === c.default ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}
          </select>
        </div>`).join('')
    : '';
  $('mergeRemember').checked = false;
  show('mergeOverlay');
}
function doMergeApply() {
  const decisions = {};
  $('mergeConflicts').querySelectorAll('select').forEach((s) => { decisions[s.dataset.relpath] = s.value; });
  const remember = $('mergeRemember').checked;
  hide('mergeOverlay');
  api().apply_merge(decisions, remember);
}

// ───────── прогресс/лог ─────────
// контролы таблицы/тулбара, блокируемые на время операции (прокрутку не трогаем)
const BUSY_CTRLS = ['addBtn', 'installBtn', 'installSetBtn', 'mergeBtn', 'compatBtn', 'removeBtn',
  'launchBtn', 'indexBtn', 'clearModsBtn', 'refreshBtn', 'searchInp', 'expandBtn', 'collapseBtn', 'filterBtn'];
function setBusyControls(on) {
  document.body.classList.toggle('busy', on);
  BUSY_CTRLS.forEach((id) => { const e = $(id); if (e) e.disabled = on; });
  document.querySelectorAll('#viewSeg button, #nameSeg button').forEach((b) => b.disabled = on);
  if (on) $('filterPop').classList.add('hidden');     // закрыть фильтр, чтобы не меняли
}
function onOpBegin() {
  busy = true;
  setBusyControls(true);
  $('progressCard').classList.remove('hidden');
  $('progBar').classList.add('pulse');
  $('progText').textContent = 'Подготовка…';
  $('progRight').textContent = '';
}
function onOpEnd(d) {
  busy = false;
  setBusyControls(false);
  $('progBar').classList.remove('pulse');
  $('progBar').querySelector('i').style.width = '100%';
  $('progText').textContent = (d && d.status) || 'Готово';
  setTimeout(() => $('progressCard').classList.add('hidden'), 1600);
  updateActionButtons();
  toast((d && d.status) || 'Готово', (d && d.status === 'Ошибка') ? 'err' : 'ok');
}
function onProgress(d) {
  const bar = $('progBar');
  if (d.mode === 'parts') {
    bar.classList.remove('pulse');
    bar.querySelector('i').style.width = (d.pct || 0) + '%';
    $('progText').textContent = `${d.ctx || 'Установка'} · часть ${d.parts || ''}`;
    $('progRight').textContent = d.gb && d.gb !== '0.00' ? `↓ ${d.gb} ГБ` : '';
  } else if (d.mode === 'pct') {
    bar.classList.remove('pulse');
    bar.querySelector('i').style.width = (d.pct || 0) + '%';
    $('progRight').textContent = (d.pct || 0) + '%';
  }
}
function appendLog(msg, cls) {
  const box = $('logBox');
  const line = document.createElement('div');
  let klass = cls;
  if (!klass) {
    const s = String(msg);
    if (/ОШИБКА|error/i.test(s)) klass = 'err';
    else if (/^===|^---|готово/i.test(s)) klass = 'acc';
    else if (/✓/.test(s)) klass = 'ok';
  }
  if (klass) line.className = 'l-' + klass;
  line.textContent = msg;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

// ───────── контекстное меню (ПКМ по моду) ─────────
let ctxMid = null;
function openCtxMenu(e, iid) {
  const n = NODE[iid];
  ctxMid = (n && n.mid) || null;
  $('ctxOpenMod').style.display = ctxMid ? '' : 'none';
  const menu = $('ctxMenu');
  menu.classList.remove('hidden');
  const w = menu.offsetWidth || 200, h = menu.offsetHeight || 80;
  menu.style.left = Math.min(e.clientX, window.innerWidth - w - 6) + 'px';
  menu.style.top = Math.min(e.clientY, window.innerHeight - h - 6) + 'px';
}
function hideCtxMenu() { $('ctxMenu').classList.add('hidden'); }

// ───────── окно (i): информация о моде ─────────
async function openModInfo(mid) {
  if (!mid) return;
  $('infoTitle').textContent = mid.split('/').pop();
  $('infoBody').innerHTML = '<div class="sub">Загрузка…</div>';
  show('infoOverlay');
  let r;
  try { r = await api().get_mod_info(mid); } catch (e) { r = null; }
  if (!r || !r.ok) { $('infoBody').innerHTML = '<div class="sub">Нет информации (ModuleInfo не найден).</div>'; return; }
  const i = r.info;
  $('infoTitle').textContent = i.name || mid.split('/').pop();
  const para = (t) => esc(t).replace(/\n/g, '<br>');
  const chips = (arr) => (arr && arr.length)
    ? arr.map((x) => `<span class="chip">${esc(x)}</span>`).join(' ') : '<span class="sub">—</span>';
  const rows = [];
  if (i.authors) rows.push(`<div class="i-row"><div class="i-k">Авторы</div><div class="i-v">${para(i.authors)}</div></div>`);
  if (i.small) rows.push(`<div class="i-row"><div class="i-k">Кратко</div><div class="i-v">${para(i.small)}</div></div>`);
  if (i.full) rows.push(`<div class="i-row"><div class="i-k">Описание</div><div class="i-v">${para(i.full)}</div></div>`);
  if (i.requires && i.requires.length) rows.push(`<div class="i-row"><div class="i-k">Требует</div><div class="i-v">${chips(i.requires)}</div></div>`);
  if (i.conflicts && i.conflicts.length) rows.push(`<div class="i-row"><div class="i-k">Конфликтует</div><div class="i-v">${chips(i.conflicts)}</div></div>`);
  if (i.section) rows.push(`<div class="i-row"><div class="i-k">Раздел</div><div class="i-v">${esc(i.section)}</div></div>`);
  rows.push(`<div class="i-row"><div class="i-k">Расположение</div><div class="i-v"><code>${esc(i.location || '')}</code></div></div>`);
  if (!i.installed) rows.push('<div class="note" style="margin-top:8px">Мод ещё не установлен — показаны данные из каталога (без описания).</div>');
  $('infoBody').innerHTML = rows.join('');
}

// ───────── утилиты ─────────
function show(id) { $(id).classList.remove('hidden'); }
function hide(id) { $(id).classList.add('hidden'); }
function confirmBox(title, html, onOk, onCancel, opts) {
  opts = opts || {};
  $('confirmTitle').textContent = title;
  $('confirmBody').innerHTML = html;
  const ok = $('confirmOk');
  ok.style.display = opts.okHidden ? 'none' : '';
  ok.textContent = opts.okLabel || 'Продолжить';
  $('confirmCancel').textContent = opts.cancelLabel || 'Отмена';
  show('confirmOverlay');
  ok.onclick = () => { hide('confirmOverlay'); if (onOk) onOk(); };
  $('confirmCancel').onclick = () => { hide('confirmOverlay'); if (onCancel) onCancel(); };
}
function toast(msg, kind) {
  const t = document.createElement('div');
  t.className = 'toast' + (kind ? ' ' + kind : '');
  t.textContent = msg;
  $('toastWrap').appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 2600);
}
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
