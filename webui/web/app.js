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
const filter = { states: new Set(STATES.map((s) => s.k)), inGame: false, inProfile: false, camps: new Set() };

// отображаемые названия лагерей (внутренние ключи остаются как есть — это только показ)
const CAMP_LABELS = {
  redux: 'ПБ «Свободная Бухта»',
  universe: 'Space Rangers Universe (Community)',
  original: 'Original',
};
const campLabel = (c) => CAMP_LABELS[c] || c;
// короткие метки-бейджи лагеря (как UNI/REDUX на вики). 'shared' — служебный
// лагерь (Redux+Uni+Orig одновременно): бейджа/фильтра «Общ» больше нет, такие
// моды показываются без метки лагеря.
const CAMP_BADGE = { redux: 'REDUX', universe: 'UNI', original: 'ORIG' };
function labelBadges(labels) {
  const cs = (labels || []).filter((c) => c !== 'shared');
  if (!cs.length) return '';
  return ' ' + cs.map((c) =>
    `<span class="lbl lbl-${esc(c)}" title="${esc(campLabel(c))}">${esc(CAMP_BADGE[c] || c.toUpperCase())}</span>`
  ).join('');
}

// данные диалога добавления
let CAMPPACKS = {};
let packsByUnit = {};
let modsByKey = {};                  // key(папка) → {key,name,camps,desc} для диалога добавления
let ALLMODS = [];                    // плоский список всех модов (для поиска по названию)
let SELECTED_SEARCH = null;          // выбранный в поиске мод {id, source, name, camps}

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
  buildCampChips();
  buildLegend();
  wireUI();
  refreshTree();
  checkSelfUpdate();                  // фоновая проверка новой версии лаунчера
}

async function checkSelfUpdate() {
  let r;
  try { r = await api().check_self_update(); } catch (e) { return; }
  if (!r || !r.ok || !r.update) return;
  $('verBadge').textContent = `⬆ доступна v${r.version}`;
  $('verBadge').classList.add('upd');
  appendLog(`⬆ Доступна новая версия лаунчера: ${r.version} (у вас ${r.current}).`
    + (r.url ? ' Скачать: ' + r.url : ' Обновите лаунчер.'), 'acc');
  toast(`Доступна новая версия лаунчера: ${r.version}`, 'ok');
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
  $('verBadge').textContent = (STATE.is_rwt ? 'SR Mods Launcher (RWT)' : 'SR Mods Launcher')
    + (STATE.version ? ' v' + STATE.version : '');
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
  $('verBadge').onclick = openSettings;   // клик по версии/«доступна vX» → Настройки (проверка)
  $('verBadge').style.cursor = 'pointer';
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
    toast('Перечитываю каталог и проверяю обновления…', 'ok');
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
  $('installBtn').onclick = onInstallClick;
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
  $('setUpdateBtn').onclick = manualCheckUpdate;
  $('forkAddBtn').onclick = addFork;
  // профили
  $('createProfBtn').onclick = createProfile;
  $('profCloseBtn').onclick = () => hide('profileOverlay');
  $('confirmCancel').onclick = () => hide('confirmOverlay');
  // добавление
  document.querySelectorAll('#addModeSeg button').forEach((b) => b.onclick = () => setAddMode(b.dataset.amode));
  $('addSearchInp').oninput = renderSearchResults;
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
  $('ctxCancelAdd').onclick = () => { const p = ctxPidx; hideCtxMenu(); if (p !== null) cancelAdd(p); };
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
  const n = (filter.states.size < STATES.length ? 1 : 0) + (filter.inGame ? 1 : 0)
    + (filter.inProfile ? 1 : 0) + (filter.camps.size ? 1 : 0);
  $('filterCount').textContent = n ? String(n) : '';
  renderTree();
}
function passFilter(node) {
  if (!filter.states.has(node.status_class)) return false;
  if (filter.inGame && !node.in_game) return false;
  if (filter.inProfile && !node.in_profile) return false;
  if (filter.camps.size) {                        // быстрый фильтр по метке лагеря
    const labs = node.labels || [];
    if (!labs.some((c) => filter.camps.has(c))) return false;
  }
  return true;
}
// быстрые фильтры-чипы по метке лагеря
function buildCampChips() {
  const camps = ['universe', 'redux', 'original'];   // без «Общ»/shared — это Redux+Uni+Orig
  $('campChips').innerHTML = camps.map((c) =>
    `<button class="chip-f lbl-${c}" data-camp="${c}" title="Показать только «${esc(campLabel(c))}»">${esc(CAMP_BADGE[c] || c)}</button>`).join('');
  $('campChips').querySelectorAll('.chip-f').forEach((b) => b.onclick = () => {
    const c = b.dataset.camp;
    filter.camps.has(c) ? filter.camps.delete(c) : filter.camps.add(c);
    b.classList.toggle('on', filter.camps.has(c));
    applyFilter();
  });
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

function colValue(n) { return STATE.tree_mode === 'section' ? n.folder : n.section; }

function renderTree() {
  for (const k in NODE) delete NODE[k];
  const body = $('treeBody');
  const camps = (TREE && TREE.camps) || [];
  if (!camps.length) {
    body.innerHTML = `<div class="tree-empty">Пока пусто.<br>
      Нажмите «➕ Добавить мод», чтобы собрать набор, затем «🧩 Установить все».</div>`;
    updateActionButtons();
    return;
  }
  const q = ($('searchInp').value || '').trim().toLowerCase();
  const visible = (n) => passFilter(n) &&
    (!q || n.label.toLowerCase().includes(q) || (n.name || '').toLowerCase().includes(q)
      || (n.desc || '').toLowerCase().includes(q));

  // Лагерь БОЛЬШЕ НЕ уровень дерева: объединяем все лагеря, группируем только по
  // пакам/разделам; лагерь виден в бейдже мода и через быстрые фильтры по метке.
  const directAll = [];
  const packMap = new Map();                    // label пака/раздела -> [моды]
  for (const camp of camps) {
    camp.mods.forEach((n) => directAll.push(n));
    camp.packs.forEach((pk) => {
      if (!packMap.has(pk.label)) packMap.set(pk.label, []);
      packMap.get(pk.label).push(...pk.mods);
    });
  }
  const directVis = directAll.filter(visible);
  const packs = [...packMap.entries()]
    .map(([label, mods]) => ({ label, mods: mods.filter(visible) }))
    .filter((x) => x.mods.length)
    .sort((a, b) => a.label.localeCompare(b.label, 'ru'));

  const rows = [];
  for (const n of directVis) { NODE[n.iid] = n; rows.push(leafRow(n, 1)); }
  for (const { label, mods } of packs) {
    const pkey = 'p:' + label;
    const pCol = collapsed.has(pkey);
    rows.push(groupRow('pack', label, pkey, pCol, mods.length, statusCounts(mods)));
    if (pCol) continue;
    for (const n of mods) { NODE[n.iid] = n; rows.push(leafRow(n, 2)); }
  }
  body.innerHTML = rows.length ? rows.join('')
    : `<div class="tree-empty">Ничего не подходит под фильтр.<br>Снимите фильтр или измените поиск.</div>`;

  body.querySelectorAll('.row.group').forEach((el) => el.onclick = () => toggleCollapse(el.dataset.key));
  body.querySelectorAll('.row.leaf').forEach((el) => el.onclick = (e) => onLeafClick(e, el.dataset.iid));
  body.querySelectorAll('.toggle.click').forEach((el) =>
    el.onclick = (e) => { e.stopPropagation(); onToggleClick(el); });
  body.querySelectorAll('.info-btn').forEach((el) =>
    el.onclick = (e) => { e.stopPropagation(); openModInfo(el.dataset.mid); });
  body.querySelectorAll('.var-opt').forEach((el) =>
    el.onclick = (e) => { e.stopPropagation(); onVariantClick(el); });
  body.querySelectorAll('.row.leaf').forEach((el) =>
    el.oncontextmenu = (e) => { e.preventDefault(); openCtxMenu(e, el.dataset.iid); });
  updateActionButtons();
}

function groupRow(kind, label, key, isCol, count, counts) {
  const icon = kind === 'camp' ? '🗂' : '■';
  const tw = isCol ? '▸' : '▾';
  const lvl = 'lvl1';                     // паки/разделы теперь верхний уровень (лагерь убран)
  // в свёрнутом состоянии содержимое не видно → показываем разбивку по статусам
  // (цветные цифры через «/»); в развёрнутом достаточно общего счётчика.
  const cell = (isCol && counts) ? statusCountHtml(counts) : `<span class="tag">${count}</span>`;
  return `<div class="row group ${kind} ${lvl}" data-key="${esc(key)}">
    <div class="name"><span class="tw">${tw}</span><span class="label">${icon} ${esc(label)}</span></div>
    <div class="cell"></div><div class="cell"></div><div class="cell"></div>
    <div class="cell">${cell}</div></div>`;
}
// разбивка узлов по статусам (status_class -> количество)
function statusCounts(mods, packsVis) {
  const all = [...(mods || [])];
  (packsVis || []).forEach((x) => all.push(...x.mods));
  const c = {};
  all.forEach((n) => { c[n.status_class] = (c[n.status_class] || 0) + 1; });
  return c;
}
// цветные цифры по статусам через «/» (в порядке STATES), с подсказкой
function statusCountHtml(counts) {
  const parts = STATES.filter((s) => counts[s.k]).map((s) =>
    `<span class="sc b-${s.k}" title="${esc(s.label)}: ${counts[s.k]}">${counts[s.k]}</span>`);
  return parts.length
    ? `<span class="scount">${parts.join('<span class="sc-sep">/</span>')}</span>`
    : '<span class="tag">0</span>';
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
      <span class="label-wrap"><span class="label">${esc(disp)}${labelBadges(n.labels)}${alt}</span>${variantSwitch(n)}${desc}</span>${info}</div>
    <div class="cell">${esc(colValue(n) || '')}</div>
    <div class="cell">${inGame}</div>
    <div class="cell">${inProf}</div>
    <div class="cell"><span class="badge b-${n.status_class}">${esc(n.status)}</span></div></div>`;
}

// переключатель варианта мода (Pol/Shu) в одной папке: смена = пометка «обновление»
function variantSwitch(n) {
  if (!n.variants || n.variants.length < 2) return '';
  const opts = n.variants.map((v) => {
    const on = v.key === n.chosen ? ' on' : '';
    const col = (v.camps && v.camps.length) ? ' lbl-' + v.camps[0] : '';
    const tag = (v.camps || []).map((c) => CAMP_BADGE[c] || c).join(',');
    return `<button class="var-opt${on}${col}" data-mid="${esc(n.mid)}" data-key="${esc(v.key)}"
      title="Вариант «${esc(v.name)}» [${esc(tag)}]${on ? ' — выбран' : ' — выбрать (потребует перекачки)'}">${esc(v.name)}</button>`;
  }).join('');
  return `<span class="var-switch" title="Вариант мода в этой папке: можно только один. Смена = перекачать при обновлении.">${opts}</span>`;
}
function onVariantClick(el) {
  if (busy) return;
  if (el.classList.contains('on')) return;
  const mid = el.dataset.mid, key = el.dataset.key;
  // оптимистично: сразу меняем выбранный вариант и метку лагеря в модели+DOM, не
  // дожидаясь ответа бэкенда — «я же уже выбрал мод с другой меткой».
  setVariantVisual(mid, key);
  api().set_variant(mid, key).then((r) => {
    if (r && r.ok) refreshTree(); else if (r) { toast(r.error, 'err'); refreshTree(); }
  });
}
// мгновенно отразить выбор варианта Pol/Shu: chosen + метки лагеря по всем узлам mid
function setVariantVisual(mid, key) {
  const apply = (n) => {
    if (n.mid !== mid || !n.variants) return;
    const v = n.variants.find((x) => x.key === key);
    if (!v) return;
    n.chosen = key;
    n.labels = (v.camps || []).slice();
  };
  for (const k in NODE) apply(NODE[k]);
  (TREE.camps || []).forEach((c) => {
    [...c.mods, ...c.packs.flatMap((p) => p.mods)].forEach(apply);
  });
  renderTree();
}
function toggleCollapse(key) { if (busy) return; collapsed.has(key) ? collapsed.delete(key) : collapsed.add(key); renderTree(); }
function collapseAll() {
  (TREE.camps || []).forEach((c) => c.packs.forEach((p) => collapsed.add('p:' + p.label)));
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
// индекс записи сборки из iid: 'p3' (обычная) или 'p3#Кат/Мод' (мод развёрнутого лагеря)
function pidxOf(iid) { const m = /^p(\d+)(#|$)/.exec(iid || ''); return m ? parseInt(m[1]) : null; }
function selectedPidx() {
  const out = new Set();
  [...selected].forEach((i) => { const n = pidxOf(i); if (n !== null) out.add(n); });
  return [...out];
}
// все узлы-моды дерева (включая свёрнутые группы, которых нет в NODE)
function allNodes() {
  const out = [];
  ((TREE && TREE.camps) || []).forEach((c) => {
    out.push(...c.mods);
    c.packs.forEach((p) => out.push(...p.mods));
  });
  return out;
}
function updateActionButtons() {
  const pidx = selectedPidx();
  const mergeSel = [...selected].some((i) => NODE[i] && NODE[i].mergeable);
  const hasUpdates = allNodes().some((n) => n.mergeable && n.status_class === 'upd');
  const hasSet = !!(TREE && TREE.queue && TREE.queue.count);
  $('addBtn').disabled = busy;
  $('removeBtn').disabled = busy || !pidx.length;
  // единая кнопка установки (как «Обновить»): есть выделение → «выбранное», иначе → «всю сборку»
  const ib = $('installBtn');
  if (pidx.length) {
    ib.textContent = '⬇ Установить выбранное';
    ib.disabled = busy;
  } else {
    ib.textContent = '🧩 Установить все';
    ib.disabled = busy || !hasSet;
  }
  const mb = $('mergeBtn');
  if (mergeSel) {                                  // есть выделение → обновить выбранные
    mb.textContent = '🔀 Обновить выбранные';
    mb.disabled = busy;
  } else {                                         // нет выделения → обновить все с обновлением
    mb.textContent = '🔀 Обновить все';
    mb.disabled = busy || !hasUpdates;
  }
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
// единая кнопка: с выделением — ставим выбранное, без — всю сборку (как «Обновить»)
function onInstallClick() {
  if (busy) return;
  if (selectedPidx().length) installSelected();
  else installWholeSet();
}
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
  confirmBox('Установить все', body,
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
// «Отменить добавление» из контекстного меню: убрать запись сборки (мод/пак/лагерь)
function cancelAdd(pidx) {
  api().remove_pidx([pidx]).then(() => { selected.clear(); refreshTree(); toast('Добавление отменено', 'ok'); });
}
function handleOpStart(r) { if (!r || !r.ok) toast((r && r.error) || 'Не удалось запустить', 'err'); }

function doReindex() { api().reindex().then(handleOpStart); }

async function doClearMods() {
  const info = await api().mods_info();
  if (!info.ok) { toast(info.error, 'err'); return; }
  if (!info.count) {
    toast(info.kept ? 'Удалять нечего — остались только базовые моды игры' : 'Папка Mods уже пуста', 'ok');
    return;
  }
  const keptNote = info.kept
    ? `<br><span style="color:var(--ok)">Базовые моды игры (${info.kept} файл.) будут сохранены.</span>` : '';
  // ШАГ 1
  confirmBox('Очистить папку Mods?',
    `Будут удалены установленные моды из папки игры:<br>
     <span style="font-family:var(--mono);font-size:12px;color:var(--muted)">${esc(info.path)}</span><br>
     Файлов к удалению: <b>${info.count}</b>.${keptNote}<br><br>
     <span style="color:var(--danger)">Действие необратимо.</span> Сама игра не затрагивается.`,
    () => {
      // ШАГ 2 — кнопка согласия переставлена (okSwap), чтобы исключить случайный двойной клик
      confirmBox('Точно удалить моды?',
        `Подтвердите ещё раз: удалить <b>${info.count}</b> файл(ов) модов?<br>
         <span style="color:var(--muted)">Отменить будет нельзя.</span>`,
        () => api().clear_mods().then((r) => {
          if (r.ok) {
            toast(`Удалено файлов: ${r.removed}` + (r.kept ? ` · сохранено базовых: ${r.kept}` : ''), 'ok');
            refreshTree();
          } else toast(r.error, 'err');
        }),
        null,
        { okLabel: `🗑 Да, удалить ${info.count}`, cancelLabel: 'Нет, отмена', okDanger: true, okSwap: true });
    },
    null, { okLabel: 'Далее…' });
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
const PRESET_CAMPS = ['universe', 'redux', 'original'];   // вшитые пресеты «всё одной метки»
function openProfiles() {
  $('newProfName').value = '';
  renderProfList();
  renderPresets();
  show('profileOverlay');
}
function renderPresets() {
  $('presetRow').innerHTML = PRESET_CAMPS.map((c) =>
    `<button class="pbtn" data-camp="${esc(c)}" title="Добавить все моды лагеря «${esc(campLabel(c))}» в сборку">
       <span class="lbl lbl-${esc(c)}">${esc(CAMP_BADGE[c] || c.toUpperCase())}</span> Всё «${esc(campLabel(c))}»
     </button>`).join('');
  $('presetRow').querySelectorAll('.pbtn').forEach((b) => b.onclick = () => addPreset(b.dataset.camp));
}
async function addPreset(camp) {
  const r = await api().add_mod({ mode: 'src', camp, pack: null, mod: '' });   // весь лагерь
  if (!r || !r.ok) { toast((r && r.error) || 'Не удалось', 'err'); return; }
  refreshTree();
  toast(`Добавлено «всё ${campLabel(camp)}» — нажмите «🧩 Установить все»`, 'ok');
}
function renderProfList() {
  const box = $('profList');
  const profs = STATE.profiles || [];
  box.innerHTML = profs.map((p) => {
    const cur = p === STATE.current_profile;
    const del = (p === 'default')
      ? '<span style="width:26px"></span>'
      : `<button class="btn ghost danger prof-del" data-prof="${esc(p)}" title="Удалить сборку">✕</button>`;
    return `<div class="prof-row${cur ? ' cur' : ''}" data-prof="${esc(p)}">
      <span class="prof-mark">${cur ? '●' : '○'}</span>
      <span class="prof-name">${esc(p)}</span>${del}</div>`;
  }).join('');
  box.querySelectorAll('.prof-row').forEach((el) => el.onclick = (e) => {
    if (e.target.closest('.prof-del')) return;          // клик по ✕ не переключает
    const name = el.dataset.prof;
    if (name !== STATE.current_profile) pickProfile(name);
  });
  box.querySelectorAll('.prof-del').forEach((b) => b.onclick = (e) => {
    e.stopPropagation(); deleteProfileByName(b.dataset.prof);
  });
}
async function pickProfile(name) {
  STATE = await api().switch_profile(name);
  applyState(); selected.clear(); refreshTree();
  renderProfList();                                     // окно остаётся открытым
  toast(`Сборка: ${name}`, 'ok');
}
async function createProfile() {
  const name = $('newProfName').value.trim(); if (!name) return;
  const r = await api().new_profile(name);
  if (!r.ok) { toast(r.error, 'err'); return; }
  STATE = r.state; applyState(); selected.clear(); refreshTree();
  $('newProfName').value = '';
  renderProfList();                                     // НЕ закрываем — остаёмся в списке
  toast('Сборка создана', 'ok');
}
function deleteProfileByName(name) {
  confirmBox('Удалить сборку?',
    `Удалить сборку «<b>${esc(name)}</b>»? Действие необратимо.<br>
     <span style="color:var(--muted)">Файлы модов на диске не трогаются — удаляется только набор.</span>`,
    async () => {
      const r = await api().delete_profile(name);
      if (!r.ok) { toast(r.error, 'err'); return; }
      STATE = r.state; applyState(); selected.clear(); refreshTree();
      renderProfList(); toast('Сборка удалена', 'ok');
    });
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
  $('setUpdateStatus').innerHTML = `Текущая: v${esc(STATE.version || '?')}`;
  FORKS = (STATE.forks || []).map((f) => ({ repo: f.repo, has_token: f.has_token, token: '' }));
  renderForks();
  show('settingsOverlay');
}
// ручная проверка обновления лаунчера (кнопка в Настройках)
async function manualCheckUpdate() {
  const st = $('setUpdateStatus');
  st.innerHTML = 'Проверяю…';
  let r;
  try { r = await api().check_self_update(); } catch (e) { r = null; }
  if (!r || !r.ok) { st.innerHTML = `<span style="color:var(--warn)">Не удалось проверить${r && r.error ? ': ' + esc(r.error) : ''}</span>`; return; }
  if (r.update) {
    st.innerHTML = `<span style="color:var(--accent)">Доступна v${esc(r.version)}</span> (у вас v${esc(r.current)}). `
      + (r.url ? `<a href="#" id="setUpdDl">Скачать</a>` : 'Обновите лаунчер вручную.');
    const dl = $('setUpdDl');
    if (dl) dl.onclick = (e) => { e.preventDefault(); api().open_url(r.url); };
    // подсветим бейдж версии в подвале
    $('verBadge').textContent = `⬆ доступна v${r.version}`; $('verBadge').classList.add('upd');
  } else {
    st.innerHTML = `<span style="color:var(--ok)">У вас последняя версия (v${esc(r.current)}).</span>`;
  }
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
  SELECTED_SEARCH = null;
  $('addSearchInp').value = '';
  $('addStatus').textContent = 'Загрузка списка паков…';
  $('addCamp').innerHTML = '<option value="">— выберите лагерь —</option>';
  $('addPack').innerHTML = ''; $('addPack').disabled = true;
  $('addMod').innerHTML = ''; $('addMod').disabled = true;
  $('addUrl').value = '';
  setAddMode('search');                 // по умолчанию — поиск по названию
  show('addOverlay');
  const r = await api().get_camp_packs();
  if (!r.ok) { $('addStatus').textContent = 'Ошибка: ' + r.error; return; }
  CAMPPACKS = r.camps || {};
  Object.keys(CAMPPACKS).sort().forEach((c) => {
    const o = document.createElement('option'); o.value = c; o.textContent = campLabel(c); $('addCamp').appendChild(o);
  });
  $('addStatus').textContent = `Лагерей: ${Object.keys(CAMPPACKS).length}`;
}
function setAddMode(mode) {
  document.querySelectorAll('#addModeSeg button').forEach((b) => b.classList.toggle('on', b.dataset.amode === mode));
  $('addSearch').style.display = mode === 'search' ? 'block' : 'none';
  $('addSrc').style.display = mode === 'src' ? 'block' : 'none';
  $('addFork').style.display = mode === 'fork' ? 'block' : 'none';
  $('addOverlay').dataset.mode = mode;
  if (mode === 'search') loadAllMods();
}
async function loadAllMods() {
  if (ALLMODS.length) { renderSearchResults(); return; }
  $('addSearchList').innerHTML = '<div class="sub">Загрузка каталога…</div>';
  let r; try { r = await api().get_all_mods(); } catch (e) { r = null; }
  if (!r || !r.ok) { $('addSearchList').innerHTML = `<div class="sub">${esc((r && r.error) || 'Каталог недоступен')}</div>`; return; }
  ALLMODS = r.mods || [];
  renderSearchResults();
}
function renderSearchResults() {
  const q = ($('addSearchInp').value || '').trim().toLowerCase();
  let list = ALLMODS;
  if (q) list = ALLMODS.filter((m) => m.name.toLowerCase().includes(q)
    || (m.desc || '').toLowerCase().includes(q) || m.id.toLowerCase().includes(q));
  const total = list.length;
  list = list.slice(0, 200);
  if (!list.length) { $('addSearchList').innerHTML = '<div class="sub">Ничего не найдено.</div>'; return; }
  $('addSearchList').innerHTML = list.map((m) =>
    `<div class="add-row${SELECTED_SEARCH && SELECTED_SEARCH.id === m.id ? ' sel' : ''}" data-id="${esc(m.id)}">
       <div class="ar-main"><span class="ar-name">${esc(m.name)}</span>${labelBadges(m.camps)}</div>
       ${m.desc ? `<div class="ar-desc">${esc(m.desc)}</div>` : ''}
     </div>`).join('') + (total > 200 ? `<div class="sub">…и ещё ${total - 200} — уточните запрос.</div>` : '');
  $('addSearchList').querySelectorAll('.add-row').forEach((el) => el.onclick = () => {
    $('addSearchList').querySelectorAll('.add-row').forEach((x) => x.classList.remove('sel'));
    el.classList.add('sel');
    SELECTED_SEARCH = ALLMODS.find((x) => x.id === el.dataset.id) || null;
  });
}
function onAddCamp() {
  const camp = $('addCamp').value;
  const packs = CAMPPACKS[camp] || [];
  packsByUnit = {};
  $('addPack').innerHTML = '<option value="">★ весь лагерь</option>';
  packs.forEach((p) => {
    packsByUnit[p.unit] = p;
    const o = document.createElement('option');
    o.value = p.unit;
    o.textContent = `${p.name}  [${p.unit}]` + (p.game_root ? '  ⚙ патчит игру' : '');
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
  const campTag = p.camp ? `  [${CAMP_BADGE[p.camp] || p.camp.toUpperCase()}]` : '';
  modsByKey = {};
  (r.mods || []).forEach((m) => {
    modsByKey[m.key] = m;
    const o = document.createElement('option');
    o.value = m.key;
    // показываем имя варианта ЭТОГО лагеря (redux→Pol*), а не имя папки (Shu*)
    o.textContent = (m.name || m.key) + campTag;
    $('addMod').appendChild(o);
  });
  $('addMod').disabled = false;
  $('addStatus').textContent = `Модов в паке: ${(r.mods || []).length}`;
}
async function onAddModSel() {
  const mid = $('addMod').value;
  if (!mid || mid === '_base') { $('addStatus').textContent = mid ? 'Общие файлы игры' : ''; return; }
  // краткое описание берём из варианта лагеря (уже пришло с get_unit_mods) — иначе
  // get_mod_info по папке вернул бы Shu-вариант для redux-пака
  const m = modsByKey[mid];
  if (m && (m.name || m.desc)) {
    $('addStatus').innerHTML = m.desc
      ? `<b>${esc(m.name || mid.split('/').pop())}</b> — ${esc(m.desc).replace(/\n/g, ' ')}`
      : `<b>${esc(m.name || mid.split('/').pop())}</b>`;
    return;
  }
  $('addStatus').textContent = mid;
}
async function doAdd() {
  const mode = $('addOverlay').dataset.mode || 'search';
  let payload;
  if (mode === 'search') {
    if (!SELECTED_SEARCH) { toast('Выберите мод из списка', 'err'); return; }
    payload = { mode: 'search', id: SELECTED_SEARCH.id,
      source: SELECTED_SEARCH.source || '', name: SELECTED_SEARCH.name };
  } else if (mode === 'fork') {
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
    // ничего не выбрано → обновить ВСЕ моды с обновлением (значок ⬆), включая свёрнутые
    const seen = new Set();
    iids = allNodes().filter((n) => {
      if (!(n.mergeable && n.status_class === 'upd')) return false;
      if (n.mid) { if (seen.has(n.mid)) return false; seen.add(n.mid); }
      return true;
    }).map((n) => n.iid);
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
  // конфликты: единый список-таблица + «установить для всех»
  const conflicts = plan.conflicts || [];
  if (conflicts.length) {
    const head = `<div class="mg-allrow">
        <span class="mg-title" style="margin:0">Как поступить с конфликтами (${conflicts.length}):</span>
        <label class="mg-all">установить для всех:
          <select id="cfAll">
            <option value="">— по отдельности —</option>
            <option value="__mine">оставить мои</option>
            <option value="__theirs">взять новые</option>
          </select>
        </label></div>`;
    const rows = conflicts.map((c, i) => `
      <tr>
        <td class="path" title="${esc(c.relpath)}">${esc(c.display)}</td>
        <td><select data-relpath="${esc(c.relpath)}" id="cf${i}" class="cf-sel">
          ${c.options.map((o) => `<option value="${esc(o.code)}" ${o.code === c.default ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}
        </select></td>
      </tr>`).join('');
    $('mergeConflicts').innerHTML = head +
      `<table class="mg-conflict-table"><thead><tr><th>Файл</th><th>Решение</th></tr></thead>
       <tbody>${rows}</tbody></table>`;
    // «установить для всех»: mine/keep — оставить мои, theirs/delete — взять новые
    $('cfAll').onchange = (e) => {
      const intent = e.target.value;
      if (!intent) return;
      const prefer = intent === '__mine' ? ['mine', 'keep'] : ['theirs', 'delete'];
      $('mergeConflicts').querySelectorAll('.cf-sel').forEach((s) => {
        const codes = [...s.options].map((o) => o.value);
        const pick = prefer.find((c) => codes.includes(c));
        if (pick) s.value = pick;
      });
    };
  } else {
    $('mergeConflicts').innerHTML = '';
  }
  $('mergeRemember').checked = false;
  show('mergeOverlay');
}
function doMergeApply() {
  const decisions = {};
  $('mergeConflicts').querySelectorAll('select.cf-sel').forEach((s) => { decisions[s.dataset.relpath] = s.value; });
  const remember = $('mergeRemember').checked;
  hide('mergeOverlay');
  api().apply_merge(decisions, remember);
}

// ───────── прогресс/лог ─────────
// контролы таблицы/тулбара, блокируемые на время операции (прокрутку не трогаем)
const BUSY_CTRLS = ['addBtn', 'installBtn', 'mergeBtn', 'compatBtn', 'removeBtn',
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
  const ts = document.createElement('span');
  ts.className = 'l-ts';
  ts.textContent = logStamp() + ' ';
  line.appendChild(ts);
  line.appendChild(document.createTextNode(msg));
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}
function logStamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `[${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}]`;
}

// ───────── контекстное меню (ПКМ по моду) ─────────
let ctxMid = null, ctxPidx = null;
function openCtxMenu(e, iid) {
  const n = NODE[iid];
  ctxMid = (n && n.mid) || null;
  ctxPidx = pidxOf(iid);                 // запись сборки (добавленная) — можно отменить
  $('ctxOpenMod').style.display = ctxMid ? '' : 'none';
  $('ctxCancelAdd').style.display = (ctxPidx !== null) ? '' : 'none';
  const menu = $('ctxMenu');
  menu.classList.remove('hidden');
  const w = menu.offsetWidth || 200, h = menu.offsetHeight || 80;
  menu.style.left = Math.min(e.clientX, window.innerWidth - w - 6) + 'px';
  menu.style.top = Math.min(e.clientY, window.innerHeight - h - 6) + 'px';
}
function hideCtxMenu() { $('ctxMenu').classList.add('hidden'); }

// ───────── окно (i): информация о моде ─────────
async function openModInfo(mid, variant) {
  if (!mid) return;
  $('infoTitle').textContent = mid.split('/').pop();
  $('infoBody').innerHTML = '<div class="sub">Загрузка…</div>';
  show('infoOverlay');
  let r;
  try { r = await api().get_mod_info(mid, variant || null); } catch (e) { r = null; }
  if (!r || !r.ok) { $('infoBody').innerHTML = '<div class="sub">Нет информации (ModuleInfo не найден).</div>'; return; }
  const i = r.info;
  $('infoTitle').textContent = i.name || mid.split('/').pop();
  const para = (t) => esc(t).replace(/\n/g, '<br>');
  const chips = (arr) => (arr && arr.length)
    ? arr.map((x) => `<span class="chip">${esc(x)}</span>`).join(' ') : '<span class="sub">—</span>';
  const rows = [];
  // переключатель вариантов Pol/Shu прямо в окне (i): посмотреть описание обоих
  if (i.variants && i.variants.length > 1) {
    const tabs = i.variants.map((v) => {
      const on = v.key === i.variant_key ? ' on' : '';
      const tag = (v.camps || []).filter((c) => c !== 'shared')
        .map((c) => CAMP_BADGE[c] || c).join(',');
      return `<button class="var-opt info-var${on}" data-key="${esc(v.key)}"
        title="Показать описание варианта «${esc(v.name)}»${tag ? ' [' + esc(tag) + ']' : ''}">${esc(v.name)}${tag ? ' <span class="sub">' + esc(tag) + '</span>' : ''}</button>`;
    }).join('');
    rows.push(`<div class="i-row"><div class="i-k">Вариант</div><div class="i-v"><span class="var-switch">${tabs}</span></div></div>`);
  }
  if (i.authors) rows.push(`<div class="i-row"><div class="i-k">Авторы</div><div class="i-v">${para(i.authors)}</div></div>`);
  if (i.small) rows.push(`<div class="i-row"><div class="i-k">Кратко</div><div class="i-v">${para(i.small)}</div></div>`);
  // полное описание: HTML с цветовой разметкой (<clr>) приходит готовым из бэкенда
  // (color_to_html — текст уже экранирован, оставлены только наши <span>/<br>)
  if (i.full) rows.push(`<div class="i-row"><div class="i-k">Описание</div><div class="i-v">${i.full_html || para(i.full)}</div></div>`);
  if (i.requires && i.requires.length) rows.push(`<div class="i-row"><div class="i-k">Требует</div><div class="i-v">${chips(i.requires)}</div></div>`);
  if (i.conflicts && i.conflicts.length) rows.push(`<div class="i-row"><div class="i-k">Конфликтует</div><div class="i-v">${chips(i.conflicts)}</div></div>`);
  if (i.section) rows.push(`<div class="i-row"><div class="i-k">Раздел</div><div class="i-v">${esc(i.section)}</div></div>`);
  rows.push(`<div class="i-row"><div class="i-k">Расположение</div><div class="i-v"><code>${esc(i.location || '')}</code></div></div>`);
  const multi = i.variants && i.variants.length > 1;
  if (!i.installed) rows.push(`<div class="note" style="margin-top:8px">${multi
    ? 'Показан вариант из каталога — на диске может стоять другой.'
    : 'Мод ещё не установлен — показаны данные из каталога (без описания).'}</div>`);
  $('infoBody').innerHTML = rows.join('');
  // переключение вариантов внутри окна (i)
  $('infoBody').querySelectorAll('.info-var').forEach((el) =>
    el.onclick = () => { if (!el.classList.contains('on')) openModInfo(mid, el.dataset.key); });
}

// ───────── утилиты ─────────
function show(id) { $(id).classList.remove('hidden'); }
function hide(id) { $(id).classList.add('hidden'); }
function confirmBox(title, html, onOk, onCancel, opts) {
  opts = opts || {};
  $('confirmTitle').textContent = title;
  $('confirmBody').innerHTML = html;
  const ok = $('confirmOk');
  const cancel = $('confirmCancel');
  ok.style.display = opts.okHidden ? 'none' : '';
  ok.textContent = opts.okLabel || 'Продолжить';
  cancel.textContent = opts.cancelLabel || 'Отмена';
  ok.classList.toggle('danger', !!opts.okDanger);
  // okSwap: переставить кнопку согласия в ПРОТИВОПОЛОЖНОЕ место (для второго
  // подтверждения опасных действий) — чтобы случайный двойной клик не «протолкнул» оба
  ok.parentElement.style.flexDirection = opts.okSwap ? 'row-reverse' : '';
  show('confirmOverlay');
  ok.onclick = () => { hide('confirmOverlay'); if (onOk) onOk(); };
  cancel.onclick = () => { hide('confirmOverlay'); if (onCancel) onCancel(); };
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
