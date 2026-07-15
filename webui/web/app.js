'use strict';
// ───────── мост к Python ─────────
const api = () => window.pywebview.api;
const $ = (id) => document.getElementById(id);

let STATE = {};
let TREE = null;
// ДВА независимых понятия (как в проводнике/почте):
//  • selected — ВЫБОР: строки, отмеченные чекбоксом (Ctrl/Shift), для МАССОВЫХ действий.
//  • activeIid — ВЫДЕЛЕНИЕ: одна активная (текущая) строка, подсвечена; драйвит связи и (i).
const selected = new Set();          // «выбрано» чекбоксом — iid листьев (для действий)
let activeIid = null;                // «выделена» — активная строка (одна), драйвит связи
const collapsed = new Set();         // свёрнутые узлы
const NODE = {};                     // iid -> узел (для быстрых проверок)
let busy = false;
let reconcileTimer = null;
// доступность: клавиатурная навигация по дереву (roving tabindex)
let treeCursor = null;      // data-iid/data-key строки, на которой сейчас «курсор»
let _treeRefocus = false;   // после перерисовки вернуть фокус на курсор (только при действии с клавиатуры)

// состояния и их описание (для легенды и фильтра)
const STATES = [
  { k: 'ok', label: 'установлен', badge: 'b-ok', tip: 'мод установлен — всё в порядке' },
  { k: 'upd', label: 'обновление', badge: 'b-upd', tip: 'для мода вышла новая версия' },
  { k: 'queued', label: 'добавлен', badge: 'b-queued', tip: 'добавлен в профиль, но ещё не установлен' },
  { k: 'avail', label: 'доступен', badge: 'b-avail', tip: 'есть в каталоге — можно установить' },
  { k: 'miss', label: 'недоступен', badge: 'b-miss', tip: 'в профиле есть, но в каталоге не найден' },
  { k: 'unknown', label: 'не в каталоге', badge: 'b-unknown', tip: 'установлен, но в каталоге его нет' },
  { k: 'load', label: 'каталог грузится', badge: 'b-load', tip: 'каталог ещё загружается' },
];
const filter = { states: new Set(STATES.map((s) => s.k)), inGame: false, inProfile: false, camps: new Set(), tags: new Set(), onlyHidden: false, noTags: false };
let anchorIid = null;                // якорь для диапазона ВЫБОРА по Shift

// отображаемые названия сборок (внутренние ключи остаются как есть — это только показ)
const CAMP_LABELS = {
  redux: 'ПБ «Свободная Бухта»',
  universe: 'Space Rangers Universe (Community)',
  original: 'Original',
};
const campLabel = (c) => CAMP_LABELS[c] || c;
// короткие метки-бейджи сборки (как UNI/REDUX на вики). 'shared' — служебный
// сборка (Redux+Uni+Orig одновременно): бейджа/фильтра «Общ» больше нет, такие
// моды показываются без метки сборки.
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
    case 'merge_silent': onMergeSilent(data); break;
    case 'deps_confirm': onDepsConfirm(data); break;
    case 'self_update_applying': onSelfUpdateApplying(data); break;
    case 'self_update_failed': onSelfUpdateFailed(data); break;
  }
};

// лаунчер сейчас закроется и перезапустится — показать блокирующее сообщение
function onSelfUpdateApplying(data) {
  const d = document.createElement('div');
  d.style.cssText = 'position:fixed;inset:0;z-index:99999;display:flex;align-items:center;'
    + 'justify-content:center;background:rgba(0,0,0,.72);color:#fff;font-size:18px;text-align:center';
  d.innerHTML = `Обновление до v${esc((data && data.version) || '')} установлено.<br>`
    + 'Лаунчер закроется и запустится заново…';
  document.body.appendChild(d);
}
function onSelfUpdateFailed(data) {
  toast('Автообновление не удалось — открываю страницу релиза', 'warn');
  if (data && data.url) api().open_url(data.url);
}

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
  maybeAutoTour();                    // при первом запуске — показать обучающий тур
}

async function checkSelfUpdate() {
  let r;
  try { r = await api().check_self_update(); } catch (e) { return; }
  if (!r || !r.ok || !r.update) return;
  $('verBadge').textContent = `⬆ обновить до v${r.version}`;
  $('verBadge').classList.add('upd');
  $('verBadge').title = r.can_auto ? 'Нажмите, чтобы обновить лаунчер' : 'Нажмите, чтобы скачать';
  $('verBadge').onclick = () => startSelfUpdate(r);   // клик по бейджу = сразу обновление
  appendLog(`⬆ Доступна новая версия лаунчера: ${r.version} (у вас ${r.current}). `
    + (r.can_auto ? 'Нажмите на бейдж версии внизу, чтобы обновить.' : 'Скачать: ' + (r.url || '')), 'acc');
  toast(`Доступна новая версия лаунчера: ${r.version} — нажмите бейдж версии, чтобы обновить`, 'ok');
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
  const vm = $('viewSeg').querySelector(`input[value="${STATE.tree_mode || 'section'}"]`);
  if (vm) vm.checked = true;
  const nm = $('nameSeg').querySelector(`input[value="${STATE.name_mode || 'folder'}"]`);
  if (nm) nm.checked = true;
  // левый рельс: свёрнут/развёрнут (запоминается между запусками)
  const collapsed = !!STATE.rail_collapsed;
  $('filterRail').classList.toggle('collapsed', collapsed);
  $('railToggle').classList.toggle('active', !collapsed);
  updateColHeader();
  $('verBadge').textContent = (STATE.is_rwt ? 'SR Mods Launcher (RWT)' : 'SR Mods Launcher')
    + (STATE.version ? ' v' + STATE.version : '');
  $('repoDot').classList.toggle('on', !!STATE.repo);
  $('tokenDot').classList.toggle('on', !!STATE.has_token);
  $('verboseChk').checked = !!STATE.log_verbose;
  $('logBox').classList.toggle('show-verbose', !!STATE.log_verbose);
  if ($('setDescInList')) $('setDescInList').checked = !!STATE.desc_in_list;
  if ($('setShowHidden2')) $('setShowHidden2').checked = !!STATE.show_hidden;
  if ($('fShowHidden')) $('fShowHidden').checked = !!STATE.show_hidden;
  reflowToolbar();
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
  // справка и обучение
  $('helpBtn').onclick = () => show('helpOverlay');
  $('tourBtn').onclick = () => startTour();
  $('helpCloseBtn').onclick = () => hide('helpOverlay');
  $('helpTourBtn').onclick = () => { hide('helpOverlay'); startTour(); };
  $('helpGitBtn').onclick = () => api().open_url(STATE.help_url || HELP_URL_FALLBACK);
  $('tourNext').onclick = () => tourGo(1);
  $('tourPrev').onclick = () => tourGo(-1);
  $('tourSkip').onclick = endTour;
  // клавиатура в туре: ←/→ — шаги, Tab не выпускает фокус за карточку подсказки
  $('tourOverlay').addEventListener('keydown', (e) => {
    if (e.key === 'ArrowRight') { e.preventDefault(); tourGo(1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); tourGo(-1); }
    else if (e.key === 'Tab') {
      const f = focusables($('tourTip'));
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      const inside = $('tourTip').contains(document.activeElement);
      if (e.shiftKey && (document.activeElement === first || !inside)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (document.activeElement === last || !inside)) { e.preventDefault(); first.focus(); }
    }
  });
  $('verBadge').onclick = openSettings;   // клик по версии/«доступна vX» → Настройки (проверка)
  $('verBadge').style.cursor = 'pointer';
  $('profileSel').onchange = (e) => switchProfile(e.target.value);
  $('profileMenuBtn').onclick = openProfiles;

  $('viewSeg').querySelectorAll('input').forEach((r) => r.onchange = () => setViewMode(r.value));
  $('nameSeg').querySelectorAll('input').forEach((r) => r.onchange = () => setNameMode(r.value));
  $('searchInp').oninput = onSearchInput;
  $('searchClear').onclick = () => { $('searchInp').value = ''; onSearchInput(); $('searchInp').focus(); };
  $('expandBtn').onclick = () => { collapsed.clear(); renderTree(); };
  $('relHead').onclick = toggleRelPanel;
  $('collapseBtn').onclick = collapseAll;
  $('treeBody').addEventListener('keydown', onTreeKey);   // доступность: стрелки/Enter/Space по дереву
  $('clearModsBtn').onclick = doClearMods;
  // левый рельс (скрыть/показать) + вкладки правой панели + меню «Ещё»
  $('railToggle').onclick = toggleRail;
  document.querySelectorAll('.side-tab').forEach((t) => t.onclick = () => setSideTab(t.dataset.tab));
  $('moreBtn').onclick = (e) => { e.stopPropagation(); $('moreMenu').classList.toggle('hidden'); };
  $('moreAdd').onclick = () => { hideMoreMenu(); openAdd(); };
  $('moreInstall').onclick = () => { hideMoreMenu(); onInstallClick(); };
  $('moreMerge').onclick = () => { hideMoreMenu(); startMerge(); };
  $('moreCompat').onclick = () => { hideMoreMenu(); checkCompat(); };
  window.addEventListener('resize', reflowToolbar);
  $('baseSel').onchange = async () => {
    const v = $('baseSel').value;
    await api().set_base(v);
    toast(v ? `База: ${campLabel(v)}` : 'База: авто (по диску)', 'ok');
  };
  $('baseAutoBtn').onclick = async () => {
    const r = await api().autodetect_base();
    if (!r || !r.ok) toast((r && r.error) || 'Не удалось', 'err');
    else toast('Определяю базу по файлам в Mods…', 'ok');
  };
  $('refreshBtn').onclick = startCheckUpdates;

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
  $('fShowHidden').onchange = (e) => toggleShowHidden(e.target.checked);
  $('fOnlyHidden').onchange = (e) => {
    filter.onlyHidden = e.target.checked;
    // скрытые моды не приходят в дерево без «показывать скрытые» → включаем автоматически
    if (filter.onlyHidden && !STATE.show_hidden) { $('fShowHidden').checked = true; toggleShowHidden(true); }
    else applyFilter();
  };
  $('legendBtn').onclick = () => $('legendBar').classList.toggle('hidden');

  // действия
  $('addBtn').onclick = openAdd;
  $('installBtn').onclick = onInstallClick;
  $('mergeBtn').onclick = startMerge;
  $('compatBtn').onclick = checkCompat;
  $('clearSelBtn').onclick = () => { selected.clear(); anchorIid = null; renderTree(); };
  $('removeBtn').onclick = removeSelected;

  $('modcfgReadBtn').onclick = () => api().modcfg_to_profile().then((r) => {
    if (r.ok) { toast(`Считано ${r.count} модов из игры`, 'ok'); refreshTree(); } else toast(r.error, 'err');
  });
  $('modcfgWriteBtn').onclick = modcfgWrite;
  $('disableAllBtn').onclick = disableAllMods;
  $('clearLogBtn').onclick = () => { $('logBox').innerHTML = ''; };
  $('saveLogBtn').onclick = saveLog;
  $('verboseChk').onchange = (e) => {
    // подробные записи в журнале уже накоплены — просто показываем/прячем их
    $('logBox').classList.toggle('show-verbose', e.target.checked);
    $('logBox').scrollTop = $('logBox').scrollHeight;
    api().set_verbose(e.target.checked);
  };
  $('setAlwaysPlan').onchange = (e) => {
    STATE.always_show_plan = e.target.checked;
    api().set_always_show_plan(e.target.checked);
  };
  $('setDescInList').onchange = (e) => {
    STATE.desc_in_list = e.target.checked;
    api().set_desc_in_list(e.target.checked).then(() => refreshTree());
  };
  $('setShowHidden2').onchange = (e) => toggleShowHidden(e.target.checked);
  $('cancelBtn').onclick = () => api().cancel();

  // контекстное меню журнала (ПКМ)
  $('logBox').oncontextmenu = (e) => { e.preventDefault(); openLogCtxMenu(e); };
  $('logCtxSelectAll').onclick = () => { selectLog(); hideLogCtxMenu(); };
  $('logCtxCopy').onclick = () => { copyLog(); hideLogCtxMenu(); };
  $('logCtxSave').onclick = () => { hideLogCtxMenu(); saveLog(); };
  $('logCtxClear').onclick = () => { $('logBox').innerHTML = ''; hideLogCtxMenu(); };

  // настройки
  $('setCancelBtn').onclick = () => hide('settingsOverlay');
  $('setSaveBtn').onclick = saveSettings;
  $('setBrowseBtn').onclick = async () => { const p = await api().browse_game(); if (p) $('setGamePath').value = p; };
  $('setUpdateBtn').onclick = manualCheckUpdate;
  $('forkAddBtn').onclick = addFork;
  // база в настройках меняется сразу (как селектор в панели) + перерисовать порядок
  $('setBase').onchange = async () => { await api().set_base($('setBase').value); loadSetOrder(); };
  $('setOrdAddBtn').onclick = () => {
    const v = $('setOrdAdd').value;
    if (v && !SET_ORDER.includes(v)) { SET_ORDER.push(v); api().set_update_extra(SET_ORDER.slice(1)); renderSetOrder(); }
  };
  // профили
  $('createProfBtn').onclick = createProfile;
  $('openProfilesBtn').onclick = async () => {
    const r = await api().open_profiles_folder();
    if (!r || !r.ok) toast((r && r.error) || 'Не удалось открыть папку', 'err');
  };
  $('importProfBtn').onclick = async () => {
    const r = await api().import_profile();
    if (r && r.ok) {
      await switchProfile(r.name);
      renderProfList();
      toast(`Профиль загружен: ${r.name}`, 'ok');
    } else if (!r || !r.cancelled) {
      toast((r && r.error) || 'Не удалось загрузить профиль', 'err');
    }
  };
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
  ['settingsOverlay', 'profileOverlay', 'addOverlay', 'compatOverlay', 'helpOverlay', 'promptOverlay'].forEach((id) =>
    $(id).onclick = (e) => { if (e.target === $(id)) hide(id); });
  $('compatCloseBtn').onclick = () => hide('compatOverlay');
  // контекстное меню (ПКМ)
  $('ctxOpenMod').onclick = () => { if (ctxMid) api().open_mod_folder(ctxMid); hideCtxMenu(); };
  $('ctxOpenMods').onclick = () => { api().open_mods_folder(); hideCtxMenu(); };
  $('ctxHide').onclick = () => {
    const mids = ctxTargetMids(); hideCtxMenu();
    if (!mids.length) return;
    // хоть один показан → скрываем все выбранные; иначе показываем все
    const anyShown = mids.some((m) => { const n = nodeByMid(m); return n && !n.hidden; });
    api().set_mods_hidden(mids, anyShown).then(() => {
      mids.forEach((m) => selected.delete('d:' + m)); refreshTree();
    });
  };
  $('ctxTags').onclick = () => {
    const mids = ctxTargetMids(); hideCtxMenu();
    if (!mids.length) return;
    if (mids.length > 1) {                       // массово: ДОБАВить теги ко всем выбранным
      tagEditor(`Теги: ${mids.length} мод(ов)`,
        'Эти теги будут ДОБАВЛЕНЫ ко всем выбранным модам (существующие теги останутся).', '')
        .then((tags) => { if (tags && tags.length) api().add_mods_tags(mids, tags).then(() => refreshTree()); });
    } else {
      editTagsFor(mids[0]);                       // один мод: задать точный набор тегов
    }
  };
  $('ctxNote').onclick = () => {
    // заметка — всегда для ОДНОГО мода (строки под курсором), даже при множественном
    // выборе: массовая запись одной заметки многим модам не нужна (отзыв 1).
    const mid = ctxMid; hideCtxMenu();
    if (mid) editNoteFor(mid);
  };
  $('ctxRemoveOne').onclick = () => { const p = ctxPidx; hideCtxMenu(); if (p !== null) cancelAdd(p); };
  $('ctxCancelAdd').onclick = () => {
    hideCtxMenu();
    confirmBox('Отменить все добавления?',
      'Очистит очередь профиля (все добавленные, ещё не установленные записи). Файлы на диске не тронет.',
      () => api().clear_queue().then(() => { selected.clear(); refreshTree(); toast('Все добавления отменены', 'ok'); }),
      null, { okLabel: 'Отменить всё', okDanger: true });
  };
  document.addEventListener('click', () => { hideCtxMenu(); hideLogCtxMenu(); hideMoreMenu(); });
  document.addEventListener('scroll', () => { hideCtxMenu(); hideLogCtxMenu(); hideMoreMenu(); }, true);
  // Esc закрывает верхнюю открытую модалку / поповер
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!$('tourOverlay').classList.contains('hidden')) { endTour(); return; }
    if (!$('ctxMenu').classList.contains('hidden')) { hideCtxMenu(); return; }
    if (!$('logCtxMenu').classList.contains('hidden')) { hideLogCtxMenu(); return; }
    if (!$('moreMenu').classList.contains('hidden')) { hideMoreMenu(); return; }
    if (!$('filterPop').classList.contains('hidden')) { $('filterPop').classList.add('hidden'); return; }
    // Esc закрывает верхнюю (последнюю по z-index) плавающую карточку мода
    if (modCards.size) {
      let top = null, tz = -1;
      modCards.forEach((c, m) => { const z = +c.style.zIndex || 0; if (z >= tz) { tz = z; top = m; } });
      if (top) { closeModCard(top); return; }
    }
    for (const id of ['helpOverlay', 'compatOverlay', 'addOverlay', 'profileOverlay', 'settingsOverlay']) {
      if (!$(id).classList.contains('hidden')) { hide(id); return; }
    }
    if (!$('confirmOverlay').classList.contains('hidden')) { $('confirmCancel').click(); return; }
    if (!$('mergeOverlay').classList.contains('hidden')) { $('mergeSkipBtn').click(); return; }
    if (selected.size) { selected.clear(); anchorIid = null; renderTree(); return; }  // снять выбор (12)
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
    + (filter.inProfile ? 1 : 0) + (filter.camps.size ? 1 : 0) + (filter.tags.size ? 1 : 0)
    + (filter.onlyHidden ? 1 : 0) + (filter.noTags ? 1 : 0);
  $('filterCount').textContent = n ? String(n) : '';
  renderTree();
}
function passFilter(node) {
  if (!filter.states.has(node.status_class)) return false;
  if (filter.inGame && !node.in_game) return false;
  if (filter.inProfile && !node.in_profile) return false;
  if (filter.onlyHidden && !node.hidden) return false;            // «только скрытые» (14)
  if (filter.noTags && (node.tags || []).length) return false;    // «без тега» (16)
  if (filter.camps.size) {                        // быстрый фильтр по метке сборки
    const labs = node.labels || [];
    if (!labs.some((c) => filter.camps.has(c))) return false;
  }
  if (filter.tags.size) {                          // быстрый фильтр по пользовательскому тегу
    const tags = node.tags || [];
    if (!tags.some((t) => filter.tags.has(t))) return false;
  }
  return true;
}
// показать/скрыть скрытые моды (синхронизирует обе галочки: фильтр и настройки)
function toggleShowHidden(val) {
  STATE.show_hidden = !!val;
  if ($('fShowHidden')) $('fShowHidden').checked = !!val;
  if ($('setShowHidden2')) $('setShowHidden2').checked = !!val;
  api().set_show_hidden(!!val).then(() => refreshTree());
}
// быстрые фильтры-чипы по пользовательским тегам (строятся по TREE.all_tags)
function buildTagChips() {
  const tags = (TREE && TREE.all_tags) || [];
  const sec = $('tagSec');
  if (!tags.length) { sec.style.display = 'none'; filter.tags.clear(); filter.noTags = false; return; }
  sec.style.display = '';
  for (const t of [...filter.tags]) if (!tags.includes(t)) filter.tags.delete(t);
  const noTagChip = `<button class="chip-f notag-f${filter.noTags ? ' on' : ''}" data-notag="1" title="Показать только моды БЕЗ ваших тегов">∅ без тега</button>`;
  $('tagChips').innerHTML = noTagChip + tags.map((t) =>
    `<button class="chip-f utag-f${filter.tags.has(t) ? ' on' : ''}" data-tag="${esc(t)}" title="Показать только с тегом «${esc(t)}»">${esc(t)}</button>`).join('');
  $('tagChips').querySelectorAll('.notag-f').forEach((b) => b.onclick = () => {
    filter.noTags = !filter.noTags;
    b.classList.toggle('on', filter.noTags);
    applyFilter();
  });
  $('tagChips').querySelectorAll('.utag-f').forEach((b) => b.onclick = () => {
    const t = b.dataset.tag;
    filter.tags.has(t) ? filter.tags.delete(t) : filter.tags.add(t);
    b.classList.toggle('on', filter.tags.has(t));
    applyFilter();
  });
}
// быстрые фильтры-чипы по метке сборки
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
  parts.push('<span class="lg"><span class="toggle on">✓</span> «В профиле» — мод входит в текущий профиль (кликабельно)</span>');
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
  if ($('baseSel')) $('baseSel').value = TREE.base_manual || '';   // селектор базы в панели
  buildTagChips();
  const hc = TREE.hidden_count || 0;
  if ($('hiddenCnt')) $('hiddenCnt').textContent = hc ? `(${hc})` : '';
  if ($('fShowHidden')) $('fShowHidden').checked = !!TREE.show_hidden;
  renderTree();
  paintRelList();                         // обновить строки-копии в списке связей из свежих узлов
  const q = TREE.queue || {};
  $('queueLbl').textContent = q.count ? `в профиле: ${q.count}${q.size ? ' · ~' + q.size : ''}` : '';
}

function colValue(n) { return STATE.tree_mode === 'section' ? n.folder : n.section; }

// ввод в поле поиска: показать/скрыть крестик очистки + перерисовать дерево
function onSearchInput() {
  const has = !!($('searchInp').value || '').length;
  $('searchClear').classList.toggle('hidden', !has);
  renderTree();
}

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
      || (n.desc || '').toLowerCase().includes(q) || (n.full_desc || '').toLowerCase().includes(q));

  // Сборка БОЛЬШЕ НЕ уровень дерева: объединяем все сборки, группируем только по
  // пакам/разделам; сборка виден в бейдже мода и через быстрые фильтры по метке.
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
    rows.push(groupRow('pack', label, pkey, pCol, mods.length, statusCounts(mods), toggleCounts(mods)));
    if (pCol) continue;
    for (const n of mods) { NODE[n.iid] = n; rows.push(leafRow(n, 2)); }
  }
  body.innerHTML = rows.length ? rows.join('')
    : `<div class="tree-empty">Ничего не подходит под фильтр.<br>Снимите фильтр или измените поиск.</div>`;

  // _treeRefocus: после перерисовки вернуть фокус на строку курсора → стрелки ↑/↓ сразу
  // переключают строки (а не прокручивают список), пока фокус не увели в другое место.
  body.querySelectorAll('.row.group').forEach((el) => el.onclick = () => { treeCursor = el.dataset.key; _treeRefocus = true; toggleCollapse(el.dataset.key); });
  body.querySelectorAll('.row.leaf').forEach((el) => el.onclick = (e) => { treeCursor = el.dataset.iid; _treeRefocus = true; onLeafClick(e, el.dataset.iid); });
  body.querySelectorAll('.row-check').forEach((el) => el.onclick = (e) => {
    e.stopPropagation(); treeCursor = el.dataset.iid; _treeRefocus = true; onLeafClick(e, el.dataset.iid, { check: true }); });
  body.querySelectorAll('.toggle.click').forEach((el) =>
    el.onclick = (e) => { e.stopPropagation(); onToggleClick(el); });
  body.querySelectorAll('.info-btn').forEach((el) =>
    el.onclick = (e) => { e.stopPropagation(); openModInfo(el.dataset.mid); });
  body.querySelectorAll('.utag').forEach((el) =>            // клик по вашему тегу — редактор тегов мода
    el.onclick = (e) => { e.stopPropagation(); if (el.dataset.mid) editTagsFor(el.dataset.mid); });
  body.querySelectorAll('.var-opt').forEach((el) =>
    el.onclick = (e) => { e.stopPropagation(); onVariantClick(el); });
  body.querySelectorAll('.row.leaf').forEach((el) =>
    el.oncontextmenu = (e) => { e.preventDefault(); openCtxMenu(e, el.dataset.iid); });
  applyTreeRoving();
  updateActionButtons();
}

// ───────── доступность: клавиатурная навигация по дереву ─────────
function treeRows() { return [...$('treeBody').querySelectorAll('.row')]; }
function rowId(el) { return el.dataset.iid || el.dataset.key || ''; }
// после каждой перерисовки: ровно одна строка получает tabindex=0 (точка входа Tab),
// остальные — -1; при действии с клавиатуры возвращаем на неё фокус.
function applyTreeRoving() {
  const rows = treeRows();
  if (!rows.length) { treeCursor = null; return; }
  let i = rows.findIndex((r) => rowId(r) === treeCursor);
  if (i < 0) i = 0;
  treeCursor = rowId(rows[i]);
  rows.forEach((r, j) => { r.tabIndex = (j === i ? 0 : -1); });
  if (_treeRefocus) { _treeRefocus = false; try { rows[i].focus(); } catch (e) {} }
}
function moveTreeFocus(i) {
  const rows = treeRows();
  if (!rows.length) return;
  i = Math.max(0, Math.min(rows.length - 1, i));
  treeCursor = rowId(rows[i]);
  rows.forEach((r, j) => { r.tabIndex = (j === i ? 0 : -1); });
  try { rows[i].focus(); } catch (e) {}
  // стрелки перемещают ВЫДЕЛЕНИЕ (активную строку) → связи следуют (отложенно)
  const iid = rows[i].dataset.iid;
  if (iid && !rows[i].classList.contains('group')) activateRow(iid, false, true);
}
function onTreeKey(e) {
  const rows = treeRows();
  if (!rows.length) return;
  let idx = rows.indexOf(e.target.closest ? e.target.closest('.row') : null);
  if (idx < 0) idx = rows.findIndex((r) => rowId(r) === treeCursor);
  if (idx < 0) idx = 0;
  const row = rows[idx];
  const isGroup = row.classList.contains('group');
  const key = e.key;
  if (key === 'ArrowDown') { e.preventDefault(); moveTreeFocus(idx + 1); }
  else if (key === 'ArrowUp') { e.preventDefault(); moveTreeFocus(idx - 1); }
  else if (key === 'Home') { e.preventDefault(); moveTreeFocus(0); }
  else if (key === 'End') { e.preventDefault(); moveTreeFocus(rows.length - 1); }
  else if (key === 'ArrowRight' && isGroup) {
    e.preventDefault();
    if (collapsed.has(row.dataset.key)) { treeCursor = row.dataset.key; _treeRefocus = true; toggleCollapse(row.dataset.key); }
    else moveTreeFocus(idx + 1);            // уже раскрыт → к первому ребёнку
  }
  else if (key === 'ArrowLeft') {
    e.preventDefault();
    if (isGroup && !collapsed.has(row.dataset.key)) { treeCursor = row.dataset.key; _treeRefocus = true; toggleCollapse(row.dataset.key); }
    else { for (let j = idx - 1; j >= 0; j--) if (rows[j].classList.contains('group')) { moveTreeFocus(j); break; } }
  }
  else if (key === ' ' || key === 'Spacebar') {          // Space — ВЫБРАТЬ строку (галочка)
    e.preventDefault();
    if (isGroup) { treeCursor = row.dataset.key; _treeRefocus = true; toggleCollapse(row.dataset.key); }
    else { _treeRefocus = true; onLeafClick(e, row.dataset.iid, { check: true }); }
  }
  else if (key === 'Enter') {                              // Enter — свернуть/раскрыть группу или переключить «в профиле»
    e.preventDefault();
    if (isGroup) { treeCursor = row.dataset.key; _treeRefocus = true; toggleCollapse(row.dataset.key); }
    else {
      const tg = row.querySelector('.toggle.click');
      if (tg) { treeCursor = row.dataset.iid; _treeRefocus = true; setTimeout(() => { _treeRefocus = false; }, 1500); onToggleClick(tg); }
      else { _treeRefocus = true; onLeafClick(e, row.dataset.iid); }   // мод без записи в профиле — просто выделить
    }
  }
  else if ((key === 'i' || key === 'I') && !isGroup) {    // i — подробнее о моде
    const ib = row.querySelector('.info-btn');
    if (ib) { e.preventDefault(); openModInfo(ib.dataset.mid); }
  }
  else if (key === 'ContextMenu' || (e.shiftKey && key === 'F10')) {
    if (!isGroup) {
      e.preventDefault();
      const r = row.getBoundingClientRect();
      openCtxMenu({ clientX: r.left + 24, clientY: r.bottom - 6 }, row.dataset.iid);
    }
  }
}

function groupRow(kind, label, key, isCol, count, counts, tc) {
  const icon = kind === 'camp' ? '🗂' : '■';
  const tw = isCol ? '▸' : '▾';
  const lvl = 'lvl1';                     // паки/разделы теперь верхний уровень (сборка убран)
  // в свёрнутом состоянии содержимое не видно → показываем разбивку по статусам
  // (цветные цифры через «/»); в развёрнутом достаточно общего счётчика.
  const cell = (isCol && counts) ? statusCountHtml(counts) : `<span class="tag">${count}</span>`;
  // сколько модов группы подключено в игре / входит в профиль (0 не показываем)
  const gc = (n, title) => n ? `<span class="gcnt" title="${title}: ${n} из ${count}">${n}</span>` : '';
  const inGame = tc ? gc(tc.g, 'Подключено в игре') : '';
  const inProf = tc ? gc(tc.p, 'В профиле') : '';
  return `<div class="row group ${kind} ${lvl}" data-key="${esc(key)}" role="treeitem" aria-level="1" aria-expanded="${isCol ? 'false' : 'true'}" aria-label="${esc(label)}, группа, ${count}, в игре ${tc ? tc.g : 0}, в профиле ${tc ? tc.p : 0}">
    <div class="name"><span class="tw">${tw}</span><span class="label">${icon} ${esc(label)}</span></div>
    <div class="cell"></div><div class="cell">${inGame}</div><div class="cell">${inProf}</div>
    <div class="cell">${cell}</div></div>`;
}
// сколько модов группы подключено в игре / входит в профиль
function toggleCounts(mods) {
  let g = 0, p = 0;
  (mods || []).forEach((n) => { if (n.in_game) g++; if (n.in_profile) p++; });
  return { g, p };
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

// пользовательские теги мода (отличны по стилю от меток сборок)
function userTags(tags, mid) {
  return (tags || []).map((t) =>
    `<span class="utag" data-mid="${esc(mid || '')}" data-tag="${esc(t)}" title="Ваш тег «${esc(t)}» — клик: редактировать теги мода">${esc(t)}</span>`).join('');
}
function leafRow(n, lvl) {
  const isSel = selected.has(n.iid);            // ВЫБРАНА (галочка, для действий)
  const isAct = n.iid && n.iid === activeIid;   // ВЫДЕЛЕНА (активная строка)
  const sel = (isSel ? ' sel' : '') + (isAct ? ' active' : '');
  const hid = n.hidden ? ' hidden-mod' : '';
  const check = n.iid
    ? `<span class="row-check${isSel ? ' on' : ''}" data-iid="${esc(n.iid)}" title="Выбрать для массовых действий (Ctrl — по одной, Shift — диапазон)">${isSel ? '✓' : ''}</span>`
    : '<span class="row-check ghost"></span>';
  const inGame = `<span class="toggle ro${n.in_game ? ' on' : ''}" title="${n.in_game ? 'подключён в игре' : 'не подключён в игре'}">${n.in_game ? '✓' : ''}</span>`;
  const inProf = n.mid
    ? `<span class="toggle click${n.in_profile ? ' on' : ''}" data-mid="${esc(n.mid)}" data-on="${n.in_profile ? 1 : 0}" title="нажмите, чтобы включить/выключить в профиле">${n.in_profile ? '✓' : ''}</span>`
    : '';
  const info = n.has_info
    ? `<span class="info-btn" data-mid="${esc(n.mid)}" title="Подробнее о моде">ⓘ</span>` : '';
  const noteIc = n.note
    ? `<span class="note-ic" title="Заметка: ${esc(n.note)}">📝</span>` : '';
  // полное описание в списке (настройка) — иначе краткое; ничего, если пусто
  const descText = (STATE.desc_in_list && n.full_desc) ? n.full_desc : n.desc;
  const desc = descText ? `<div class="mdesc${STATE.desc_in_list && n.full_desc ? ' full' : ''}">${esc(descText)}</div>` : '';
  const date = n.date ? `<span class="mdate" title="дата последнего изменения мода разработчиком (если неизвестна — дата файлов на диске); можно выделить и скопировать">${esc(n.date)}</span>` : '';
  const disp = (STATE.name_mode === 'module' && n.name) ? n.name : n.label;
  // во втором режиме рядом показываем папку мелким, чтобы не терять ориентир
  const alt = (STATE.name_mode === 'module' && n.name && n.name !== n.label)
    ? `<span class="alt-name" title="имя папки на диске">${esc(n.label)}</span>` : '';
  const hidMark = n.hidden ? '<span class="hid-mark" title="Скрыт из списка (виден, т.к. включён показ скрытых)">🙈</span>' : '';
  return `<div class="row leaf lvl${lvl}${sel}${hid}" data-iid="${esc(n.iid)}" data-mid="${esc(n.mid || '')}" role="treeitem" aria-level="${lvl}" aria-selected="${isSel ? 'true' : 'false'}" aria-label="${esc(disp)}, ${esc(n.status)}${n.in_profile ? ', в профиле' : ''}${n.hidden ? ', скрыт' : ''}">
    <div class="name">${check}<span class="tw">·</span>
      <span class="label-wrap"><span class="name-line">${hidMark}<span class="label">${esc(disp)}</span>${labelBadges(n.labels)}${userTags(n.tags, n.mid)}${alt}${date}</span>${variantSwitch(n)}${desc}</span>${noteIc}${info}</div>
    <div class="cell">${esc(colValue(n) || '')}</div>
    <div class="cell">${inGame}</div>
    <div class="cell">${inProf}</div>
    <div class="cell"><span class="badge b-${n.status_class}">${esc(n.status)}</span></div></div>`;
}

// метка кнопки варианта: предпочитаем НАЗВАНИЕ БАЗОВОЙ СБОРКИ (а не имя пака). В узком
// переключателе строки — короткий бейдж сборки (REDUX/UNI/ORIG), в карточке (full) —
// полное имя. Если у двух вариантов метка сборки совпала бы (Pol/Shu в одной сборке) —
// оставляем имя мода, чтобы их различать.
function variantLabel(v, all, full) {
  const camp = (v.camps && v.camps.length === 1) ? v.camps[0] : null;
  const dup = camp && all.filter((x) => x.camps && x.camps.length === 1 && x.camps[0] === camp).length > 1;
  if (!camp || dup) return v.name;
  return full ? campLabel(camp) : (CAMP_BADGE[camp] || camp.toUpperCase());
}
// переключатель варианта мода в одной папке: смена = пометка «обновление»
function variantSwitch(n) {
  if (!n.variants || n.variants.length < 2) return '';
  const opts = n.variants.map((v) => {
    const on = v.key === n.chosen ? ' on' : '';
    const col = (v.camps && v.camps.length) ? ' lbl-' + v.camps[0] : '';
    const tag = (v.camps || []).map((c) => CAMP_BADGE[c] || c).join(',');
    const txt = variantLabel(v, n.variants);
    return `<button class="var-opt${on}${col}" data-mid="${esc(n.mid)}" data-key="${esc(v.key)}"
      title="Сборка «${esc(txt)}» [${esc(tag)}]${on ? ' — выбрана' : ' — выбрать (потребует перекачки)'}">${esc(txt)}</button>`;
  }).join('');
  return `<span class="var-switch" title="Вариант мода в этой папке: можно только один. Смена = перекачать при обновлении.">${opts}</span>`;
}
function onVariantClick(el) {
  if (busy) return;
  if (el.classList.contains('on')) return;
  const mid = el.dataset.mid, key = el.dataset.key;
  // оптимистично: сразу меняем выбранный вариант и метку сборки в модели+DOM, не
  // дожидаясь ответа бэкенда — «я же уже выбрал мод с другой меткой».
  setVariantVisual(mid, key);
  api().set_variant(mid, key).then((r) => {
    if (r && r.ok) refreshTree(); else if (r) { toast(r.error, 'err'); refreshTree(); }
  });
}
// мгновенно отразить выбор варианта Pol/Shu: chosen + метки сборки по всем узлам mid
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
let _relTimer = null;
// подсветить активную строку в DOM без полной перерисовки (для навигации стрелками)
function markActiveDom(iid) {
  $('treeBody').querySelectorAll('.row.leaf.active').forEach((r) => r.classList.remove('active'));
  if (iid) {
    const el = [...$('treeBody').querySelectorAll('.row.leaf')].find((r) => r.dataset.iid === iid);
    if (el) el.classList.add('active');
  }
}
// установить активную (ВЫДЕЛЕННУЮ) строку → связи следуют за ней. immediate=false —
// отложенно (навигация стрелками не спамит бэкенд запросами связей на каждый шаг).
function activateRow(iid, immediate, dom) {
  activeIid = iid || null;
  if (dom) markActiveDom(iid);
  const cn = iid ? (NODE[iid] || allNodes().find((x) => x.iid === iid)) : null;
  const mid = cn && cn.mid ? cn.mid : null;
  clearTimeout(_relTimer);
  if (immediate) renderRelList(mid);
  else _relTimer = setTimeout(() => renderRelList(mid), 220);
}
function setActiveRow(iid) { activateRow(iid, true, false); }
// клик по строке. Обычный клик — только ВЫДЕЛЕНИЕ (активная строка). Чекбокс / Ctrl /
// Shift — ВЫБОР (галочки, набор `selected`) для массовых действий. Понятия независимы:
// обычный клик НЕ меняет галочки, чекбокс НЕ переносит фокус связей неожиданно.
function onLeafClick(e, iid, opt) {
  if (!iid) return;
  opt = opt || {};
  const wantSelect = opt.check || e.ctrlKey || e.metaKey || e.shiftKey;   // операция ВЫБОРА
  // Shift — диапазон ВЫБОРА от якоря до текущего по видимому порядку строк
  if (e.shiftKey && anchorIid && anchorIid !== iid) {
    const leaves = [...$('treeBody').querySelectorAll('.row.leaf')].map((r) => r.dataset.iid);
    const a = leaves.indexOf(anchorIid), b = leaves.indexOf(iid);
    if (a >= 0 && b >= 0) {
      const [lo, hi] = a < b ? [a, b] : [b, a];
      for (let i = lo; i <= hi; i++) selected.add(leaves[i]);
      setActiveRow(iid); renderTree();
      return;                            // якорь НЕ двигаем — можно тянуть диапазон дальше
    }
  }
  if (wantSelect) {                       // ВЫБОР: переключить галочку строки
    selected.has(iid) ? selected.delete(iid) : selected.add(iid);
  }
  anchorIid = iid;                        // якорь для будущего Shift-диапазона (любой клик)
  // ВЫДЕЛЕНИЕ: активной становится строка в любом случае (и при выборе тоже)
  setActiveRow(iid);
  renderTree();
}
// индекс записи профиля из iid: 'p3' (обычная) или 'p3#Кат/Мод' (мод развёрнутого сборки)
function pidxOf(iid) { const m = /^p(\d+)(#|$)/.exec(iid || ''); return m ? parseInt(m[1]) : null; }
function selectedPidx() {
  const out = new Set();
  [...selected].forEach((i) => { const n = pidxOf(i); if (n !== null) out.add(n); });
  return [...out];
}
// узел мода по mid (сначала видимые в NODE, затем полный обход дерева)
function nodeByMid(mid) {
  if (!mid) return null;
  for (const k in NODE) if (NODE[k] && NODE[k].mid === mid) return NODE[k];
  return allNodes().find((x) => x.mid === mid) || null;
}
// уникальные mid ВЫБРАННЫХ (галочкой) строк
function selectedMids() {
  const out = [], seen = new Set();
  selected.forEach((iid) => {
    const n = NODE[iid] || allNodes().find((x) => x.iid === iid);
    if (n && n.mid && !seen.has(n.mid)) { seen.add(n.mid); out.push(n.mid); }
  });
  return out;
}
// цели ПКМ-действия: правый клик по ВЫБРАННОЙ строке при наличии выбора → весь выбор;
// иначе — только строка под курсором (конвенция проводника). Убирает баг «тег/скрытие
// применялись лишь к одной строке из нескольких выделенных».
function ctxTargetMids() {
  const selM = selectedMids();
  if (ctxNode && selected.has(ctxNode.iid) && selM.length > 1) return selM;
  return ctxMid ? [ctxMid] : [];
}
// редактор тегов ОДНОГО мода (точный набор: пустое поле убирает все теги)
function editTagsFor(mid) {
  const n = nodeByMid(mid);
  if (!n) return Promise.resolve();
  return tagEditor('Теги мода', 'Через запятую. Пустое поле уберёт все теги.', (n.tags || []).join(', '))
    .then((tags) => {
      if (tags === null) return;
      return api().set_mod_tags(mid, tags).then(() => { refreshTree(); refreshCardMeta(mid); });
    });
}
// редактор заметки ОДНОГО мода (для карточки)
function editNoteFor(mid) {
  const n = nodeByMid(mid);
  if (!n) return Promise.resolve();
  return promptModal('Личная заметка', 'Видна как 📝 в строке мода. Ctrl+Enter — сохранить.',
    n.note || '', true).then((v) => {
    if (v === null) return;
    return api().set_mod_note(mid, v).then(() => { refreshTree(); refreshCardMeta(mid); });
  });
}
// перерисовать тело открытой карточки мода (после правки тегов/заметки — данные из свежих узлов)
function refreshCardMeta(mid) {
  const card = modCards.get(mid);
  if (card && card._info) renderModCard(card, mid, card._info);
}
// модалка ввода тегов с чипами уже существующих тегов (клик по чипу — добавить в поле,
// чтобы переиспользовать написанное имя тега, а не набирать заново — отзыв 13).
function tagEditor(title, hint, initial) {
  const existing = (TREE && TREE.all_tags) || [];
  return promptModal(title, hint, initial, false, { chips: existing }).then((v) => {
    if (v === null) return null;                 // отмена
    return v.split(',').map((s) => s.trim()).filter(Boolean);
  });
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

// ───────── второй список: связи выбранного мода (те же колонки, строки-копии) ─────────
let relMid = null;      // mid, для которого сейчас показаны связи
let relInfo = null;     // закэшированная карточка (requires/dependents/conflicts) — чтобы
                        // перерисовывать строки из свежих узлов дерева без повторной загрузки
function _confArr(i) {
  return (i.conflicts_ref && i.conflicts_ref.length) ? i.conflicts_ref
    : (i.conflicts || []).map((c) => ({ name: c, mid: '' }));
}
// подгрузить связи мода и показать (из клика по строке основного/связанного списка)
async function renderRelList(mid) {
  relMid = mid || null;
  const sub = $('relSub'), body = $('relBody');
  if (!mid) { relInfo = null; sub.textContent = '— выделите мод в списке'; body.innerHTML = ''; return; }
  // панель НЕ разворачиваем автоматически — состояние сворачивания за пользователем (#1);
  // содержимое обновляем всегда, чтобы при разворачивании показать текущий выбор
  sub.textContent = '— загрузка…';
  let r; try { r = await api().get_mod_info(mid, null); } catch (e) { r = null; }
  if (relMid !== mid) return;                  // успели выбрать другой мод
  relInfo = (r && r.ok) ? r.info : null;
  paintRelList();
}
// перерисовать строки из кэша relInfo + СВЕЖИХ узлов дерева (после refresh/переключений)
function paintRelList() {
  const sub = $('relSub'), body = $('relBody');
  if (!relMid || !relInfo) { if (!relMid) sub.textContent = '— выделите мод в списке'; return; }
  const i = relInfo;
  const groups = [
    ['Требует', i.requires, 'Моды, которые нужны этому'],
    ['Нужен для', i.dependents, 'Моды, которые зависят от этого'],
    ['Конфликтует', _confArr(i), 'Несовместимо одновременно'],
  ];
  const total = groups.reduce((s, g) => s + ((g[1] || []).length), 0);
  sub.textContent = `— ${i.name || relMid.split('/').pop()} · связей: ${total}`;
  if (!total) { body.innerHTML = '<div class="tree-empty">У этого мода нет связей (требований, зависимостей, конфликтов).</div>'; return; }
  const byMid = new Map();
  allNodes().forEach((n) => { if (n.mid && !byMid.has(n.mid)) byMid.set(n.mid, n); });
  const rows = [];
  for (const [label, arr, tip] of groups) {
    if (!arr || !arr.length) continue;
    rows.push(relGroupRow(label, arr.length, tip));
    for (const x of arr) {
      const node = x && x.mid ? byMid.get(x.mid) : null;
      rows.push(node ? leafRow(node, 2) : relMissingRow(x));
    }
  }
  body.innerHTML = rows.join('');
  wireRelBody(body);
}
function relGroupRow(label, count, tip) {
  return `<div class="row group rel-group" title="${esc(tip)}">
    <div class="name"><span class="label">${esc(label)}</span></div>
    <div class="cell"></div><div class="cell"></div><div class="cell"></div>
    <div class="cell"><span class="tag">${count}</span></div></div>`;
}
// мод-связь, которого нет в текущем списке (не установлен и не в профиле)
function relMissingRow(x) {
  const nm = (x && (x.name || x.mid)) || '?';
  const info = x && x.mid ? `<span class="info-btn" data-mid="${esc(x.mid)}" title="Подробнее о моде">ⓘ</span>` : '';
  return `<div class="row leaf lvl2 rel-missing" data-mid="${esc((x && x.mid) || '')}">
    <div class="name"><span class="row-check ghost"></span><span class="tw">·</span>
      <span class="label-wrap"><span class="name-line"><span class="label">${esc(nm)}</span></span></span>${info}</div>
    <div class="cell"></div><div class="cell"></div><div class="cell"></div>
    <div class="cell"><span class="badge b-miss" title="Мода нет в вашем списке (не установлен и не в профиле)">нет в списке</span></div></div>`;
}
function wireRelBody(body) {
  body.querySelectorAll('.row.leaf').forEach((el) => {
    el.onclick = (e) => { if (e.target.closest('.info-btn,.toggle.click,.var-opt,.row-check')) return; selectRelMid(el.dataset.mid); };
    el.oncontextmenu = (e) => { if (!el.dataset.iid) return; e.preventDefault(); openCtxMenu(e, el.dataset.iid); };
  });
  body.querySelectorAll('.info-btn').forEach((el) =>
    el.onclick = (e) => { e.stopPropagation(); openModInfo(el.dataset.mid); });
  body.querySelectorAll('.toggle.click').forEach((el) =>
    el.onclick = (e) => { e.stopPropagation(); onToggleClick(el); });
  body.querySelectorAll('.var-opt').forEach((el) =>
    el.onclick = (e) => { e.stopPropagation(); onVariantClick(el); });
}
// выбрать связанный мод: подсветить в основном списке (если есть) + показать ЕГО связи
function selectRelMid(mid) {
  if (!mid) return;
  const node = allNodes().find((n) => n.mid === mid);
  // клик по связи ВЫДЕЛЯЕТ мод (активная строка), галочки-выбор не трогаем
  if (node) { activeIid = node.iid; anchorIid = node.iid; renderTree(); }
  const el = [...$('treeBody').querySelectorAll('.row.leaf')].find((r) => r.dataset.mid === mid);
  if (el) el.scrollIntoView({ block: 'nearest' });
  renderRelList(mid);
}
function toggleRelPanel() {
  const w = $('relWrap');
  const col = w.classList.toggle('collapsed');
  $('relHead').querySelector('.rel-tw').textContent = col ? '▸' : '▾';
}
function updateActionButtons() {
  const pidx = selectedPidx();
  const mergeSel = [...selected].some((i) => NODE[i] && NODE[i].mergeable);
  const hasUpdates = allNodes().some((n) => n.mergeable && n.status_class === 'upd');
  const hasSet = !!(TREE && TREE.queue && TREE.queue.count);
  $('addBtn').disabled = busy;
  $('removeBtn').disabled = busy || !pidx.length;
  // единая кнопка установки (как «Обновить»): есть выделение → «выбранное», иначе → «весь профиль»
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
  // «Снять выбор (N)» — видна только когда есть выбранные галочкой строки (отзыв 12)
  const csb = $('clearSelBtn');
  if (csb) {
    const cnt = selected.size;
    csb.style.display = cnt ? '' : 'none';
    csb.textContent = `✖ Снять выбор (${cnt})`;
    csb.disabled = busy;
  }
  // дубли в меню «Ещё» (для узкого окна) синхронизируем с оригиналами
  const ma = $('moreAdd'); if (ma) { ma.disabled = $('addBtn').disabled; }
  const mi = $('moreInstall'); if (mi) { mi.textContent = ib.textContent; mi.disabled = ib.disabled; }
  const mm = $('moreMerge'); if (mm) { mm.textContent = mb.textContent; mm.disabled = mb.disabled; }
  const mc = $('moreCompat'); if (mc) { mc.disabled = $('compatBtn').disabled; }
  reflowToolbar();                                 // текст кнопок мог измениться → пересчитать перенос
}

// ───────── toggle «в профиле» с проверкой зависимостей/конфликтов ─────────
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
      Сначала добавьте его в профиль или установите.</div>`;
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
// единая кнопка: с выделением — ставим выбранное, без — весь профиль (как «Обновить»)
function onInstallClick() {
  if (busy) return;
  if (selectedPidx().length) installSelected();
  else installWholeSet();
}
function installSelected() {
  const pidx = selectedPidx();
  if (!pidx.length) { toast('Выберите строку профиля (📋) в списке', 'err'); return; }
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
  // Не показываем «сборок: N» (для пользователя это ничего не значит): выводим
  // количество модов, когда оно известно (каталожные моды), а целые сборки/паки
  // называем по-человечески — «все моды сборки …».
  const parts = [];
  if (d.count) {
    let s = `Будет установлено модов: <b>${d.count}</b>`;
    if (deps.size) s += ` <span style="color:var(--muted)">(из них зависимостей: ${deps.size})</span>`;
    parts.push(s);
  }
  (d.bulk_items || []).forEach((b) => {
    if (b.type === 'camp') parts.push(`Все моды сборки: <b>${esc(campLabel(b.camp))}</b>`);
    else if (b.type === 'unit') parts.push(`Весь пак «<b>${esc(b.name || 'пак')}</b>»`);
    else parts.push(`Архив «<b>${esc(b.name || 'архив')}</b>»`);
  });
  let body = parts.length ? parts.join('<br>') + '.' : '';
  if (lines.length) body += `<div style="margin-top:8px;font-family:var(--mono);font-size:12px;max-height:190px;overflow:auto;color:var(--muted)">${lines.map(esc).join('<br>')}</div>`;
  if (d.missing_deps && d.missing_deps.length)
    body += `<div class="note">⚠ Не найдены в каталоге: ${esc(d.missing_deps.join(', '))}</div>`;
  if (d.conflicts && d.conflicts.length)
    body += `<div class="note">⚠ Конфликты (показаны, не снимаются): ${d.conflicts.map((p) => esc(p[0] + '⟷' + p[1])).join('; ')}</div>`;
  if (!parts.length) body += '<div class="note">Нечего устанавливать.</div>';
  confirmBox('Установить все', body,
    () => api().confirm_install_deps(),
    () => api().cancel_deps());
}
function removeSelected() {
  const pidx = selectedPidx();
  if (!pidx.length) return;
  confirmBox('Убрать из профиля?',
    `Будет убрано из профиля: <b>${pidx.length}</b> позиц.<br>
     <span style="color:var(--muted)">Файлы на диске не удаляются — снимается только пометка «в профиле».</span>`,
    () => api().remove_pidx(pidx).then(() => { selected.clear(); refreshTree(); }));
}
// «Отменить добавление» из контекстного меню: убрать запись профиля (мод/пак/сборка)
function cancelAdd(pidx) {
  api().remove_pidx([pidx]).then(() => { selected.clear(); refreshTree(); toast('Добавление отменено', 'ok'); });
}
function handleOpStart(r) { if (!r || !r.ok) toast((r && r.error) || 'Не удалось запустить', 'err'); }

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
  confirmBox('Записать профиль в игру?',
    `В игре будут подключены ровно те моды, что отмечены «в профиле».<br>
     Текущее подключение в игре будет перезаписано. Продолжить?`,
    () => api().profile_to_modcfg().then((r) => {
      if (!r.ok) { toast(r.error, 'err'); return; }
      let m = `Подключено в игре: ${r.count}`;
      if (r.missing && r.missing.length) m += ` (но ${r.missing.length} нет на диске)`;
      toast(m, r.missing && r.missing.length ? 'err' : 'ok');
      refreshTree();
    }));
}
function disableAllMods() {
  confirmBox('Отключить все моды в профиле?',
    `Снимутся <b>все</b> галочки «в профиле».<br>
     <span style="color:var(--muted)">Подключение в самой игре и файлы на диске не меняются.
     Чтобы применить к игре — нажмите «⟸ Записать профиль в игру».</span>`,
    () => api().disable_all_mods().then((r) => {
      if (!r || !r.ok) { toast((r && r.error) || 'Не удалось', 'err'); return; }
      toast('Все моды отключены в профиле', 'ok');
      refreshTree();
    }), null, { okLabel: 'Отключить все', okDanger: true });
}

// ───────── журнал: контекстное меню / копирование / сохранение ─────────
function logText() {
  // копируем/сохраняем ровно то, что видно: подробные строки — только при включённом
  // «Подробном логе» (иначе они скрыты и в файл попадать не должны)
  const showV = $('logBox').classList.contains('show-verbose');
  return [...$('logBox').children]
    .filter((el) => showV || !el.classList.contains('l-v'))
    .map((el) => el.textContent).join('\n');
}
function selectLog() {
  const box = $('logBox');
  const sel = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(box);
  sel.removeAllRanges();
  sel.addRange(range);
}
function copyLog() {
  const sel = String(window.getSelection() || '');
  const text = sel.trim() ? sel : logText();
  const done = () => toast('Скопировано в буфер обмена', 'ok');
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
  } else { fallbackCopy(text, done); }
}
function fallbackCopy(text, done) {
  const ta = document.createElement('textarea');
  ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); done(); } catch (e) { toast('Не удалось скопировать', 'err'); }
  document.body.removeChild(ta);
}
async function saveLog() {
  const text = logText();
  if (!text.trim()) { toast('Журнал пуст', 'err'); return; }
  const r = await api().save_log(text);
  if (r && r.ok) toast('Журнал сохранён', 'ok');
  else if (r && r.cancelled) { /* пользователь отменил */ }
  else toast((r && r.error) || 'Не удалось сохранить', 'err');
}
function openLogCtxMenu(e) {
  hideCtxMenu();
  const menu = $('logCtxMenu');
  menu.classList.remove('hidden');
  const w = menu.offsetWidth || 200, h = menu.offsetHeight || 120;
  menu.style.left = Math.min(e.clientX, window.innerWidth - w - 6) + 'px';
  menu.style.top = Math.min(e.clientY, window.innerHeight - h - 6) + 'px';
  const first = menu.querySelector('button');
  if (first) setTimeout(() => { try { first.focus(); } catch (err) {} }, 20);
}
function hideLogCtxMenu() { $('logCtxMenu').classList.add('hidden'); }

// ───────── рельс / вкладки / меню «Ещё» / переполнение тулбара ─────────
function toggleRail() {
  const collapsed = $('filterRail').classList.toggle('collapsed');
  $('railToggle').classList.toggle('active', !collapsed);
  try { api().set_rail_collapsed(collapsed); } catch (e) {}
  if (collapsed) $('filterPop').classList.add('hidden');
  reflowToolbar();
}
function setSideTab(name) {
  document.querySelectorAll('.side-tab').forEach((t) => t.classList.toggle('on', t.dataset.tab === name));
  $('paneGame').classList.toggle('hidden', name !== 'game');
  $('paneLog').classList.toggle('hidden', name !== 'log');
  if (name === 'log') $('logBox').scrollTop = $('logBox').scrollHeight;
}
function hideMoreMenu() { const m = $('moreMenu'); if (m) m.classList.add('hidden'); }
// Переполнение тулбара: панель ВСЕГДА одна строка (flex-wrap:nowrap). При нехватке
// ширины кнопки прогрессивно уезжают в меню «Ещё»: narrow (Обновить+Совместимость) →
// narrow2 (+Установить) → narrow3 (+Добавить). Замеряем реальное переполнение по
// scrollWidth: пока не влезает — добавляем следующий уровень свёртки.
function reflowToolbar() {
  const tb = $('actionsToolbar');
  if (!tb) return;
  const tiers = ['narrow', 'narrow2', 'narrow3'];
  tiers.forEach((t) => tb.classList.remove(t));
  for (const t of tiers) {
    if (tb.scrollWidth <= tb.clientWidth + 1) break;   // влезает → дальше не сворачиваем
    tb.classList.add(t);                               // ещё переполнено → следующий уровень
  }
}

// ───────── запуск/папка ─────────
async function launchGame() { const r = await api().launch_game(); r.ok ? toast('Запускаю игру…', 'ok') : toast(r.error, 'err'); }
async function browseGame() {
  const p = await api().browse_game();
  if (p) { STATE.game_path = p; applyState(); refreshTree(); toast('Папка игры выбрана', 'ok'); }
}

// ───────── профили/настройки ─────────
async function switchProfile(name) { STATE = await api().switch_profile(name); applyState(); selected.clear(); activeIid = null; relMid = null; renderRelList(null); refreshTree(); }
const PRESET_CAMPS = ['universe', 'redux', 'original'];   // вшитые пресеты «всё одной метки»
function openProfiles() {
  $('newProfName').value = '';
  renderProfList();
  renderPresets();
  show('profileOverlay');
}
function renderPresets() {
  $('presetRow').innerHTML = PRESET_CAMPS.map((c) =>
    `<button class="pbtn" data-camp="${esc(c)}" title="Добавить все моды сборки «${esc(campLabel(c))}» в профиль">
       <span class="lbl lbl-${esc(c)}">${esc(CAMP_BADGE[c] || c.toUpperCase())}</span> Всё «${esc(campLabel(c))}»
     </button>`).join('');
  $('presetRow').querySelectorAll('.pbtn').forEach((b) => b.onclick = () => addPreset(b.dataset.camp));
}
async function addPreset(camp) {
  const r = await api().add_mod({ mode: 'src', camp, pack: null, mod: '' });   // всю сборку
  if (!r || !r.ok) { toast((r && r.error) || 'Не удалось', 'err'); return; }
  if (r.dup) { toast(`«${campLabel(camp)}» уже в профиле`, 'ok'); return; }
  refreshTree();
  toast(`Добавлено «всё ${campLabel(camp)}» — нажмите «🧩 Установить все»`, 'ok');
}

// ───────── проверка обновлений: порядок сборок ─────────
let pendingAfterDetect = null;    // что запустить после автоопределения базы
let ORDER_EDIT = [];              // редактируемый порядок в диалоге (incl. база на [0])
let ORDER_ALL = [];

async function startCheckUpdates() {
  const st = await api().get_update_order();      // {ok, base, order, all_camps}
  if (!st || !st.ok) { toast('Не удалось получить состояние', 'err'); return; }
  if (!st.base) {                                 // п.1 — база не выбрана
    confirmBox('Базовая сборка не выбрана',
      'Для проверки обновлений нужно знать базовую сборку.<br>Определить её автоматически по файлам в папке Mods?',
      () => { pendingAfterDetect = 'check'; api().autodetect_base(); toast('Определяю базу…', 'ok'); },
      null, { okLabel: 'Определить автоматически', cancelLabel: 'Отмена' });
    return;
  }
  if (!st.needs_order) { runUpdateCheck(); return; }       // все моды в базе → окно не нужно
  openOrderDialog(st, (extra) => runUpdateCheck(extra));   // п.2 — окно порядка
}

async function runUpdateCheck(extra) {                     // п.3 — сама проверка
  // extra===undefined → окно пропущено, сохранённый порядок НЕ трогаем; массив → сохранить
  if (Array.isArray(extra)) await api().set_update_extra(extra);   // ДО refresh_remote (он сам
  toast('Перечитываю каталог и проверяю обновления…', 'ok');       // запускает сверку с порядком)
  try { await api().refresh_remote(); } catch (e) {}
  const s = await api().get_state(); STATE = s; applyState();
  refreshTree();
}

function openOrderDialog(st, onConfirm) {
  ORDER_EDIT = (st.order || []).slice();
  ORDER_ALL = (st.all_camps || []).slice();
  confirmBox('Порядок проверки обновлений',
    `<div class="ord-help">Обновление каждого мода проверяется по <b>первой</b> сборке в списке, где этот мод есть. Мод проверяется один раз. Строка 1 — базовая сборка (не меняется).</div>
     <div id="ordList" class="ord-list"></div>
     <div class="ord-add"><select id="ordAdd"></select><button class="mini" id="ordAddBtn">➕ Добавить сборку</button></div>`,
    () => onConfirm(ORDER_EDIT.slice(1)),
    null, { okLabel: '✓ Проверить', cancelLabel: 'Отмена' });
  const addBtn = $('ordAddBtn');
  if (addBtn) addBtn.onclick = () => {
    const v = $('ordAdd').value;
    if (v && !ORDER_EDIT.includes(v)) { ORDER_EDIT.push(v); renderOrderEditor(); }
  };
  renderOrderEditor();
}

function renderOrderEditor() {
  const list = $('ordList'); if (!list) return;
  list.innerHTML = ORDER_EDIT.map((c, i) => `
    <div class="ord-row">
      <span class="ord-num">${i + 1}</span>
      <span class="lbl lbl-${c}">${esc(CAMP_BADGE[c] || c.toUpperCase())}</span>
      <span class="ord-name">${esc(campLabel(c))}${i === 0 ? ' — базовый' : ''}</span>
      <span class="spacer"></span>
      ${i > 1 ? `<button class="mini" data-up="${i}" title="Выше">▲</button>` : ''}
      ${i >= 1 && i < ORDER_EDIT.length - 1 ? `<button class="mini" data-down="${i}" title="Ниже">▼</button>` : ''}
      ${i >= 1 ? `<button class="mini danger" data-del="${i}" title="Убрать">✖</button>` : ''}
    </div>`).join('');
  const rem = ORDER_ALL.filter((c) => !ORDER_EDIT.includes(c));
  const sel = $('ordAdd'), addBtn = $('ordAddBtn');
  if (sel) sel.innerHTML = rem.map((c) => `<option value="${c}">${esc(campLabel(c))}</option>`).join('');
  if (addBtn) addBtn.disabled = !rem.length;
  list.querySelectorAll('[data-del]').forEach((b) => b.onclick = () => { ORDER_EDIT.splice(+b.dataset.del, 1); renderOrderEditor(); });
  list.querySelectorAll('[data-up]').forEach((b) => b.onclick = () => { const i = +b.dataset.up; [ORDER_EDIT[i - 1], ORDER_EDIT[i]] = [ORDER_EDIT[i], ORDER_EDIT[i - 1]]; renderOrderEditor(); });
  list.querySelectorAll('[data-down]').forEach((b) => b.onclick = () => { const i = +b.dataset.down; [ORDER_EDIT[i + 1], ORDER_EDIT[i]] = [ORDER_EDIT[i], ORDER_EDIT[i + 1]]; renderOrderEditor(); });
}

function renderProfList() {
  const box = $('profList');
  const profs = STATE.profiles || [];
  box.innerHTML = profs.map((p) => {
    const cur = p === STATE.current_profile;
    const del = (p === 'default')
      ? '<span style="width:26px"></span>'
      : `<button class="btn ghost danger prof-del" data-prof="${esc(p)}" title="Удалить профиль">✕</button>`;
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
  applyState(); selected.clear(); activeIid = null; relMid = null; renderRelList(null); refreshTree();
  renderProfList();                                     // окно остаётся открытым
  toast(`Профиль: ${name}`, 'ok');
}
async function createProfile() {
  const name = $('newProfName').value.trim(); if (!name) return;
  const r = await api().new_profile(name);
  if (!r.ok) { toast(r.error, 'err'); return; }
  STATE = r.state; applyState(); selected.clear(); activeIid = null; relMid = null; renderRelList(null); refreshTree();
  $('newProfName').value = '';
  renderProfList();                                     // НЕ закрываем — остаёмся в списке
  toast('Профиль создан', 'ok');
}
function deleteProfileByName(name) {
  confirmBox('Удалить профиль?',
    `Удалить профиль «<b>${esc(name)}</b>»? Действие необратимо.<br>
     <span style="color:var(--muted)">Файлы модов на диске не трогаются — удаляется только набор.</span>`,
    async () => {
      const r = await api().delete_profile(name);
      if (!r.ok) { toast(r.error, 'err'); return; }
      STATE = r.state; applyState(); selected.clear(); activeIid = null; relMid = null; renderRelList(null); refreshTree();
      renderProfList(); toast('Профиль удалена', 'ok');
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
  $('setBase').value = (TREE && TREE.base_manual) || '';
  $('setRepo').value = STATE.repo || '';
  $('setToken').value = '';
  $('forkRepo').value = ''; $('forkToken').value = '';
  $('setUpdateStatus').innerHTML = `Текущая: v${esc(STATE.version || '?')}`;
  $('setAlwaysPlan').checked = !!STATE.always_show_plan;
  FORKS = (STATE.forks || []).map((f) => ({ repo: f.repo, has_token: f.has_token, token: '' }));
  renderForks();
  loadSetOrder();
  show('settingsOverlay');
}

// редактор «Порядок проверки обновлений» в Настройках (сохраняется сразу)
let SET_ORDER = [];
async function loadSetOrder() {
  const st = await api().get_update_order();
  if (!st || !st.ok) return;
  SET_ORDER = (st.order || []).slice();
  ORDER_ALL = (st.all_camps || []).slice();
  renderSetOrder();
}
function renderSetOrder() {
  const list = $('setOrdList'); if (!list) return;
  if (!SET_ORDER.length) {
    list.innerHTML = `<div class="ord-empty">Сначала выберите базовую сборку выше.</div>`;
    if ($('setOrdAdd')) $('setOrdAdd').innerHTML = '';
    if ($('setOrdAddBtn')) $('setOrdAddBtn').disabled = true;
    return;
  }
  list.innerHTML = SET_ORDER.map((c, i) => `
    <div class="ord-row">
      <span class="ord-num">${i + 1}</span>
      <span class="lbl lbl-${c}">${esc(CAMP_BADGE[c] || c.toUpperCase())}</span>
      <span class="ord-name">${esc(campLabel(c))}${i === 0 ? ' — базовый' : ''}</span>
      <span class="spacer"></span>
      ${i > 1 ? `<button class="mini" data-up="${i}" title="Выше">▲</button>` : ''}
      ${i >= 1 && i < SET_ORDER.length - 1 ? `<button class="mini" data-down="${i}" title="Ниже">▼</button>` : ''}
      ${i >= 1 ? `<button class="mini danger" data-del="${i}" title="Убрать">✖</button>` : ''}
    </div>`).join('');
  const rem = ORDER_ALL.filter((c) => !SET_ORDER.includes(c));
  const sel = $('setOrdAdd'), addBtn = $('setOrdAddBtn');
  if (sel) sel.innerHTML = rem.map((c) => `<option value="${c}">${esc(campLabel(c))}</option>`).join('');
  if (addBtn) addBtn.disabled = !rem.length;
  const persist = () => { api().set_update_extra(SET_ORDER.slice(1)); renderSetOrder(); };
  list.querySelectorAll('[data-del]').forEach((b) => b.onclick = () => { SET_ORDER.splice(+b.dataset.del, 1); persist(); });
  list.querySelectorAll('[data-up]').forEach((b) => b.onclick = () => { const i = +b.dataset.up; [SET_ORDER[i - 1], SET_ORDER[i]] = [SET_ORDER[i], SET_ORDER[i - 1]]; persist(); });
  list.querySelectorAll('[data-down]').forEach((b) => b.onclick = () => { const i = +b.dataset.down; [SET_ORDER[i + 1], SET_ORDER[i]] = [SET_ORDER[i], SET_ORDER[i + 1]]; persist(); });
}
// запустить обновление лаунчера: авто-скачивание+замена (.exe) или откат на страницу релиза
async function startSelfUpdate(info) {
  let r;
  try { r = await api().download_self_update(); } catch (e) { r = null; }
  if (r && r.ok) {
    // прогресс идёт через op_begin/progress/op_end; по готовности прилетит self_update_applying
    appendLog(`Скачиваю новую версию лаунчера v${info.version}…`, 'acc');
    return;
  }
  // не .exe-сборка или занято/ошибка → откат на ручное скачивание страницы релиза
  if (r && r.error && r.error !== 'dev') { toast(r.error, 'warn'); return; }
  if (info.url) { api().open_url(info.url); toast('Открыл страницу релиза для ручного скачивания', 'ok'); }
}

// ручная проверка обновления лаунчера (кнопка в Настройках)
async function manualCheckUpdate() {
  const st = $('setUpdateStatus');
  st.innerHTML = 'Проверяю…';
  let r;
  try { r = await api().check_self_update(); } catch (e) { r = null; }
  if (!r || !r.ok) { st.innerHTML = `<span style="color:var(--warn)">Не удалось проверить${r && r.error ? ': ' + esc(r.error) : ''}</span>`; return; }
  if (r.update) {
    const label = r.can_auto ? 'Обновить сейчас' : 'Скачать';
    st.innerHTML = `<span style="color:var(--accent)">Доступна v${esc(r.version)}</span> (у вас v${esc(r.current)}). `
      + `<a href="#" id="setUpdDl">${label}</a>`;
    const dl = $('setUpdDl');
    if (dl) dl.onclick = (e) => { e.preventDefault(); startSelfUpdate(r); };
    // подсветим бейдж версии в подвале
    $('verBadge').textContent = `⬆ доступна v${r.version}`; $('verBadge').classList.add('upd');
    $('verBadge').onclick = () => startSelfUpdate(r);
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
  $('addCamp').innerHTML = '<option value="">— выберите сборку —</option>';
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
  $('addStatus').textContent = `Сборок: ${Object.keys(CAMPPACKS).length}`;
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
  $('addPack').innerHTML = '<option value="">★ вся сборка</option>';
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
  if (!unit) return;                          // всю сборку
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
    // показываем имя варианта ЭТОЙ сборки (redux→Pol*), а не имя папки (Shu*)
    o.textContent = (m.name || m.key) + campTag;
    $('addMod').appendChild(o);
  });
  $('addMod').disabled = false;
  $('addStatus').textContent = `Модов в паке: ${(r.mods || []).length}`;
}
async function onAddModSel() {
  const mid = $('addMod').value;
  if (!mid || mid === '_base') { $('addStatus').textContent = mid ? 'Общие файлы игры' : ''; return; }
  // краткое описание берём из варианта сборки (уже пришло с get_unit_mods) — иначе
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
    if (!camp) { toast('Выберите сборку', 'err'); return; }
    const unit = $('addPack').value;
    const pack = unit ? packsByUnit[unit] : null;
    payload = { mode: 'src', camp, pack: pack ? { camp: pack.camp, unit: pack.unit, name: pack.name } : null, mod: $('addMod').value };
  }
  const r = await api().add_mod(payload);
  if (!r.ok) { toast(r.error, 'err'); return; }
  hide('addOverlay'); refreshTree(); toast('Добавлено в профиль', 'ok');
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
      toast('Обновлений не найдено. Нажмите «⟳ Проверить обновления».', 'err');
      return;
    }
  }
  api().start_merge(iids).then((r) => { if (!r.ok) toast(r.error, 'err'); });
}
const MG_WHY = {
  conflict_text: 'и вы, и мод правили этот текст',
  conflict_binary: 'и вы, и мод правили этот файл',
  conflict_deleted: 'мод удалил файл, у вас есть правки',
};
function mgFiles(n) { return n === 1 ? '1 файлу' : `${n} файлам`; }
function plF(n) { n = Math.abs(n); const d = n % 10, dd = n % 100;
  return (dd >= 11 && dd <= 14) ? 'файлов' : d === 1 ? 'файл' : (d >= 2 && d <= 4) ? 'файла' : 'файлов'; }
// сворачиваемый блок «что применится автоматически»
function mgAutoDetail(groups, labels) {
  const order = ['update', 'add', 'automerge', 'deleted_clean', 'player_only'];
  let inner = '', count = 0;
  for (const st of order) {
    const files = groups[st]; if (!files || !files.length) continue;
    count += files.length;
    inner += `<div class="mg-agroup"><div class="mg-atitle">${esc(labels[st] || st)} (${files.length})</div>
      <div class="mg-files">${files.map(esc).join('<br>')}</div></div>`;
  }
  if (!inner) return '';
  return `<details class="mg-auto"><summary>Что применится автоматически (${count})</summary>${inner}</details>`;
}
function onMergePlan(plan) {
  const s = plan.summary || {};
  const conflicts = plan.conflicts || [];
  const groups = plan.groups || {}, labels = plan.labels || {};
  const head = $('mergeHead'), cf = $('mergeConflicts'), gr = $('mergeGroups');
  $('mergeRemember').checked = false;

  if (conflicts.length) {
    // ── режим РЕШЕНИЙ: видны только конфликты, авто-изменения — под спойлером ──
    $('mergeTitle').textContent = `Решения по обновлению · ${plan.id || ''}`;
    head.innerHTML = `<div class="mg-intro">Всё остальное уже готово к установке. Осталось выбрать по ${mgFiles(conflicts.length)}:</div>`;
    cf.innerHTML = conflicts.map((c, i) => {
      const seg = c.options.map((o) =>
        `<button type="button" class="seg-b${o.code === c.default ? ' on' : ''}" data-code="${esc(o.code)}">${esc(o.label)}</button>`).join('');
      return `<div class="mg-cf" data-relpath="${esc(c.relpath)}">
        <div class="mg-cf-h"><span class="mg-num">${i + 1}</span><span class="mg-path" title="${esc(c.relpath)}">${esc(c.display)}</span></div>
        <div class="mg-why">${esc(MG_WHY[c.status] || '')}</div>
        <div class="seg">${seg}</div>
      </div>`;
    }).join('') +
      `<div class="mg-bulk">Быстро для всех:
        <button type="button" class="btn mini" id="cfAllMine">мои везде</button>
        <button type="button" class="btn mini" id="cfAllTheirs">новые везде</button></div>`;
    gr.innerHTML = mgAutoDetail(groups, labels);
    // выбор сегмента внутри строки
    cf.querySelectorAll('.mg-cf .seg').forEach((seg) => seg.querySelectorAll('.seg-b').forEach((b) => {
      b.onclick = () => { seg.querySelectorAll('.seg-b').forEach((x) => x.classList.remove('on')); b.classList.add('on'); };
    }));
    const bulk = (prefer) => cf.querySelectorAll('.mg-cf .seg').forEach((seg) => {
      const btns = [...seg.querySelectorAll('.seg-b')];
      const pick = prefer.map((code) => btns.find((b) => b.dataset.code === code)).find(Boolean);
      if (pick) { btns.forEach((x) => x.classList.remove('on')); pick.classList.add('on'); }
    });
    $('cfAllMine').onclick = () => bulk(['mine', 'keep']);
    $('cfAllTheirs').onclick = () => bulk(['theirs', 'delete']);
    $('mergeRememberWrap').style.display = '';
    $('mergeSkipBtn').textContent = 'Пропустить мод';
    $('mergeApplyBtn').textContent = 'Применить';
  } else {
    // ── режим ВЕРДИКТА: включён «всегда показывать план», конфликтов нет ──
    $('mergeTitle').textContent = 'Обновление мода';
    const repl = (s.update || 0) + (s.automerge || 0), kept = s.player_only || 0, gone = s.deleted_clean || 0;
    const bullets = [];
    if (repl) bullets.push(`обновляется ${repl} ${plF(repl)}`);
    if (s.add) bullets.push(`добавляется ${s.add} ${plF(s.add)}`);
    if (gone) bullets.push(`${gone} ${plF(gone)} больше не нужны новой версии`);
    if (kept) bullets.push(`ваши правки сохранены (${kept} ${plF(kept)})`);
    head.innerHTML = `<div class="mg-verdict"><div class="mg-vic ok">✔</div>
        <div><div class="mg-vt">${esc(plan.id || '')} <span class="mg-ver">· ${esc(plan.version_old || '—')} → ${esc(plan.version_new || '—')}</span></div>
        <div class="mg-vsub">Всё обновится автоматически.</div></div></div>
      <ul class="mg-b">${bullets.map((b) => `<li>${esc(b)}</li>`).join('')}</ul>
      <div class="mg-safe">Ничего из ваших правок не потеряется.</div>`;
    cf.innerHTML = '';
    gr.innerHTML = mgAutoDetail(groups, labels);
    $('mergeRememberWrap').style.display = 'none';
    $('mergeSkipBtn').textContent = 'Пропустить';
    $('mergeApplyBtn').textContent = 'Обновить';
  }
  show('mergeOverlay');
}
function doMergeApply() {
  const decisions = {};
  $('mergeConflicts').querySelectorAll('.mg-cf').forEach((row) => {
    const on = row.querySelector('.seg-b.on') || row.querySelector('.seg-b');
    if (on) decisions[row.dataset.relpath] = on.dataset.code;
  });
  const remember = $('mergeRemember').checked;
  hide('mergeOverlay');
  api().apply_merge(decisions, remember);
}
function onMergeSilent(d) {
  const sub = d && d.sub ? ' · ' + d.sub : '';
  toast('✔ ' + ((d && d.text) || 'Обновлено') + sub, 'ok');
}

// ───────── прогресс/лог ─────────
// контролы таблицы/тулбара, блокируемые на время операции (прокрутку не трогаем)
const BUSY_CTRLS = ['addBtn', 'installBtn', 'mergeBtn', 'compatBtn', 'removeBtn',
  'launchBtn', 'clearModsBtn', 'refreshBtn', 'searchInp', 'expandBtn', 'collapseBtn', 'filterBtn',
  'disableAllBtn', 'modcfgReadBtn', 'modcfgWriteBtn', 'moreAdd', 'moreInstall', 'moreMerge', 'moreCompat'];
function setBusyControls(on) {
  document.body.classList.toggle('busy', on);
  BUSY_CTRLS.forEach((id) => { const e = $(id); if (e) e.disabled = on; });
  document.querySelectorAll('#viewSeg input, #nameSeg input').forEach((b) => b.disabled = on);
  if (on) $('filterPop').classList.add('hidden');     // закрыть фильтр, чтобы не меняли
}
function onOpBegin() {
  busy = true;
  setBusyControls(true);
  setSideTab('log');                       // во время операции показываем журнал
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
  if (pendingAfterDetect === 'check') {     // после автоопределения базы — продолжить флоу
    pendingAfterDetect = null;
    setTimeout(startCheckUpdates, 200);
  }
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
function appendLog(data, cls) {
  // data — либо строка (обычная запись), либо {msg, v:true} (подробная/verbose).
  // Подробные записи хранятся всегда и лишь скрываются классом .l-v, пока не включён
  // «Подробный лог» — поэтому при переключении режима видны и старые подробные строки.
  const verbose = data && typeof data === 'object' && data.v;
  const msg = verbose ? data.msg : data;
  const box = $('logBox');
  const line = document.createElement('div');
  let klass = cls;
  if (!klass) {
    const s = String(msg);
    if (/ОШИБКА|error/i.test(s)) klass = 'err';
    else if (/^===|^---|готово/i.test(s)) klass = 'acc';
    else if (/✓/.test(s)) klass = 'ok';
  }
  line.className = (klass ? 'l-' + klass : '') + (verbose ? ' l-v' : '');
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
let ctxMid = null, ctxPidx = null, ctxNode = null;
function openCtxMenu(e, iid) {
  const n = NODE[iid];
  ctxNode = n || null;
  ctxMid = (n && n.mid) || null;
  ctxPidx = pidxOf(iid);                 // запись профиля (добавленная) — можно отменить
  $('ctxOpenMod').style.display = ctxMid ? '' : 'none';
  // скрытие/теги/заметки — только для настоящих модов (есть mid)
  const hasMid = !!ctxMid;
  $('ctxHide').style.display = hasMid ? '' : 'none';
  $('ctxTags').style.display = hasMid ? '' : 'none';
  $('ctxNote').style.display = hasMid ? '' : 'none';
  if (hasMid) {
    // если правый клик по выбранной строке и выбрано несколько — действия массовые
    const nSel = (n && selected.has(n.iid)) ? selectedMids().length : 1;
    const suf = nSel > 1 ? ` (${nSel})` : '';
    const anyShown = nSel > 1 ? selectedMids().some((m) => { const x = nodeByMid(m); return x && !x.hidden; }) : !n.hidden;
    $('ctxHide').textContent = (anyShown ? '🙈 Скрыть из списка' : '🙈 Показать в списке') + suf;
    $('ctxTags').textContent = '🏷 Теги…' + suf;
    $('ctxNote').textContent = '📝 Заметка…';   // заметка всегда для одной строки — без (N)
  }
  $('ctxRemoveOne').style.display = (ctxPidx !== null) ? '' : 'none';   // убрать эту строку
  $('ctxCancelAdd').style.display = (ctxPidx !== null) ? '' : 'none';   // отменить все добавления
  const menu = $('ctxMenu');
  menu.classList.remove('hidden');
  const w = menu.offsetWidth || 200, h = menu.offsetHeight || 80;
  menu.style.left = Math.min(e.clientX, window.innerWidth - w - 6) + 'px';
  menu.style.top = Math.min(e.clientY, window.innerHeight - h - 6) + 'px';
  // доступность: фокус на первый пункт — меню управляется Enter/Tab/Esc с клавиатуры
  const firstBtn = [...menu.querySelectorAll('button')].find((b) => b.style.display !== 'none');
  if (firstBtn) setTimeout(() => { try { firstBtn.focus(); } catch (err) {} }, 20);
}
function hideCtxMenu() { $('ctxMenu').classList.add('hidden'); }

// ───────── окно (i): плавающие немодальные карточки мода ─────────
// Карточки — не модалки: перетаскиваются за заголовок, ресайзятся за угол, сворачиваются
// в шапку, и можно держать НЕСКОЛЬКО открытыми (сравнить два мода). Дедуп по mid.
// «Связи» разворачиваются ВТОРОЙ таблицей ПОД основной (в том же окне, не попап): клик по
// связи показывает ПОЛНУЮ карточку связанного мода ниже, с такой же информацией.
const modCards = new Map();     // mid -> элемент .mod-card
let cardZ = 500;                // текущий верхний z-index (растёт при фокусе карточки)

function bringCardFront(card) { card.style.zIndex = String(++cardZ); }

// геометрия (позиция+размер) запоминается между открытиями (localStorage)
function loadCardGeom() {
  try { return JSON.parse(localStorage.getItem('modCardGeom') || 'null'); } catch (e) { return null; }
}
function saveCardGeom(card) {
  try {
    localStorage.setItem('modCardGeom', JSON.stringify({
      left: parseInt(card.style.left) || 0, top: parseInt(card.style.top) || 0,
      width: card.offsetWidth, height: card.offsetHeight,
    }));
  } catch (e) {}
}

function makeCardDraggable(card, handle) {
  handle.addEventListener('mousedown', (e) => {
    if (e.target.closest('.mc-close, .mc-collapse, .mc-title-txt')) return;   // кнопки и САМ текст имени — не тащим (текст можно выделить/скопировать); пустая область шапки тащит
    bringCardFront(card);
    const r = card.getBoundingClientRect();
    const dx = e.clientX - r.left, dy = e.clientY - r.top;
    card.classList.add('dragging');
    const move = (ev) => {
      let x = ev.clientX - dx, y = ev.clientY - dy;
      x = Math.max(2, Math.min(x, window.innerWidth - 60));
      y = Math.max(2, Math.min(y, window.innerHeight - 30));
      card.style.left = x + 'px'; card.style.top = y + 'px';
    };
    const up = () => { card.classList.remove('dragging'); saveCardGeom(card); document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
    e.preventDefault();
  });
}

function closeModCard(mid) {
  const c = modCards.get(mid);
  if (c) { if (c._ro) c._ro.disconnect(); c.remove(); modCards.delete(mid); }
}

async function openModInfo(mid, variant) {
  if (!mid) return;
  let card = modCards.get(mid);
  if (!card) {
    card = document.createElement('div');
    card.className = 'mod-card';
    // геометрия из памяти + каскад-смещение, если уже открыты другие карточки
    const g = loadCardGeom();
    const off = modCards.size * 26;
    const clamp = (v, max) => Math.max(2, Math.min(v, max));
    if (g) {
      card.style.left = clamp((g.left || 40) + off, window.innerWidth - 80) + 'px';
      card.style.top = clamp((g.top || 60) + off, window.innerHeight - 40) + 'px';
      if (g.width) card.style.width = g.width + 'px';
      if (g.height) card.style.height = g.height + 'px';
    } else {
      card.style.left = clamp(Math.max(20, window.innerWidth / 2 - 300) + off, window.innerWidth - 80) + 'px';
      card.style.top = (70 + off) + 'px';
    }
    card.innerHTML = `<div class="mc-head"><button class="mc-collapse" title="Свернуть в заголовок">▾</button><span class="mc-title"><span class="mc-title-txt">Загрузка…</span></span><button class="mc-close" title="Закрыть (Esc)">✕</button></div>
      <div class="mc-body"><div class="sub">Загрузка…</div></div>`;
    document.body.appendChild(card);
    modCards.set(mid, card);
    makeCardDraggable(card, card.querySelector('.mc-head'));
    card.querySelector('.mc-close').onclick = () => closeModCard(mid);
    const collBtn = card.querySelector('.mc-collapse');
    collBtn.onclick = () => {
      const c = card.classList.toggle('collapsed');
      collBtn.textContent = c ? '▸' : '▾';
      collBtn.title = c ? 'Развернуть' : 'Свернуть в заголовок';
    };
    card.addEventListener('mousedown', () => bringCardFront(card));
    // ресайз за угол (CSS resize:both) → запоминаем размер
    if (window.ResizeObserver) {
      card._ro = new ResizeObserver(() => { clearTimeout(card._rot); card._rot = setTimeout(() => saveCardGeom(card), 250); });
      card._ro.observe(card);
    }
  }
  bringCardFront(card);
  let r;
  try { r = await api().get_mod_info(mid, variant || null); } catch (e) { r = null; }
  if (!modCards.has(mid)) return;                    // карточку закрыли, пока грузилось
  if (!r || !r.ok) { card.querySelector('.mc-body').innerHTML = '<div class="sub">Нет информации (ModuleInfo не найден).</div>'; return; }
  renderModCard(card, mid, r.info);
}

// полные строки карточки (вариант/авторы/кратко/описание/раздел/расположение) — общие
// для основной карточки и для развёрнутой ниже связанной (одинаковая полнота информации)
function cardInfoRows(i, mid) {
  const para = (t) => esc(t).replace(/\n/g, '<br>');
  const rows = [];
  if (i.variants && i.variants.length > 1) {
    const tabs = i.variants.map((v) => {
      const on = v.key === i.variant_key ? ' on' : '';
      const tag = (v.camps || []).filter((c) => c !== 'shared').map((c) => CAMP_BADGE[c] || c).join(',');
      const txt = variantLabel(v, i.variants, true);
      return `<button class="var-opt info-var${on}" data-key="${esc(v.key)}"
        title="Показать описание варианта «${esc(txt)}»${tag ? ' [' + esc(tag) + ']' : ''}">${esc(txt)}${tag ? ' <span class="sub">' + esc(tag) + '</span>' : ''}</button>`;
    }).join('');
    rows.push(`<div class="i-row"><div class="i-k">Вариант</div><div class="i-v"><span class="var-switch">${tabs}</span></div></div>`);
  }
  if (i.authors) rows.push(`<div class="i-row"><div class="i-k">Авторы</div><div class="i-v">${para(i.authors)}</div></div>`);
  if (i.small) rows.push(`<div class="i-row"><div class="i-k">Кратко</div><div class="i-v">${para(i.small)}</div></div>`);
  if (i.full) rows.push(`<div class="i-row"><div class="i-k">Описание</div><div class="i-v">${i.full_html || para(i.full)}</div></div>`);
  if (i.section) rows.push(`<div class="i-row"><div class="i-k">Раздел</div><div class="i-v">${esc(i.section)}</div></div>`);
  // ваши теги + заметка (редактируются прямо из карточки)
  const node = nodeByMid(mid);
  if (node) {
    const tags = node.tags || [];
    const tagHtml = tags.length
      ? tags.map((t) => `<span class="utag" data-mid="${esc(mid)}" data-tag="${esc(t)}" title="Клик — редактировать теги">${esc(t)}</span>`).join(' ')
      : '<span class="sub">нет</span>';
    rows.push(`<div class="i-row"><div class="i-k">Ваши теги</div><div class="i-v">${tagHtml}
      <button class="mini card-tags" data-mid="${esc(mid)}" title="Изменить теги мода">✎ теги</button></div></div>`);
    const noteHtml = node.note ? para(node.note) : '<span class="sub">нет</span>';
    rows.push(`<div class="i-row"><div class="i-k">Заметка</div><div class="i-v">${noteHtml}
      <button class="mini card-note" data-mid="${esc(mid)}" title="Изменить заметку">✎ заметка</button></div></div>`);
  }
  rows.push(`<div class="i-row"><div class="i-k">Расположение</div><div class="i-v"><code>${esc(i.location || '')}</code></div></div>`);
  const multi = i.variants && i.variants.length > 1;
  if (!i.installed) rows.push(`<div class="note" style="margin-top:8px">${multi
    ? 'Показан вариант из каталога — на диске может стоять другой.'
    : 'Мод ещё не установлен — показаны данные из каталога (без описания).'}</div>`);
  return rows.join('');
}
function renderModCard(card, mid, i) {
  card._info = i;                                   // для перерисовки после правки тегов/заметки
  card.querySelector('.mc-title-txt').textContent = i.name || mid.split('/').pop();
  const body = card.querySelector('.mc-body');
  body.innerHTML = cardInfoRows(i, mid);
  body.querySelectorAll('.info-var').forEach((el) =>
    el.onclick = () => { if (!el.classList.contains('on')) openModInfo(mid, el.dataset.key); });
  body.querySelectorAll('.card-tags, .utag').forEach((el) =>
    el.onclick = (e) => { e.stopPropagation(); editTagsFor(el.dataset.mid || mid); });
  body.querySelector('.card-note') && (body.querySelector('.card-note').onclick =
    (e) => { e.stopPropagation(); editNoteFor(mid); });
}

// ───────── утилиты ─────────
// доступность: селектор фокусируемых элементов внутри модалки
const FOCUS_SEL = 'a[href],button:not([disabled]),input:not([disabled]):not([type=hidden]),'
  + 'select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
function focusables(root) {
  return [...root.querySelectorAll(FOCUS_SEL)]
    .filter((el) => el.offsetWidth || el.offsetHeight || el.getClientRects().length);
}
// ───────── обучающий тур ─────────
const HELP_URL_FALLBACK =
  'https://github.com/ArtYudin89/sr-mods-launcher/blob/master/README.md';
// Шаги тура: sel — CSS-селектор подсвечиваемого элемента (null = карточка по центру).
const TOUR_STEPS = [
  { sel: null, title: 'Добро пожаловать!',
    text: 'Это SR Mods Launcher — он скачивает, ставит, обновляет и организует моды для Space Rangers HD. Тур покажет всё по порядку: настройку игры, профили и сборки, добавление модов, список с тегами/заметками и запуск. Пройти можно за пару минут, а повторить — кнопкой 🎓 в любой момент.' },
  { sel: '#gamePath', title: 'Папка игры',
    text: 'Сначала укажите, где установлена игра (там лежит Rangers.exe). Моды ставятся в подпапку Mods. Клик по адресу — выбрать папку; лаунчер умеет и сам найти типовые пути Steam.' },
  { sel: '#profileSel', title: 'Профиль модов',
    text: 'Профиль — это сохранённый набор модов (база + список). Держите несколько под разные партии и переключайтесь здесь. Кнопка ＋ рядом открывает окно управления профилями — его посмотрим на следующем шаге.' },
  { sel: '#profileOverlay .modal', open: 'profile', title: 'Окно профилей',
    text: 'Здесь: список ваших профилей (клик — переключиться), «Пресеты» для быстрого старта — добавить в профиль СРАЗУ всю сборку (Redux / Universe / Original), создание нового профиля, а также 📥 загрузка присланного профиля и 📂 папка с файлами профилей. Профиль переносимый — можно отдать другому игроку.' },
  { sel: '.base-pick', title: 'Базовая сборка',
    text: 'Второй ряд шапки: базовая сборка, относительно которой проверяются обновления. «Авто» определит её по файлам в Mods (кнопка 🎯). Рядом — «Проверить обновления». Важно: сейвы привязаны к базе, посреди партии базу менять нельзя.' },
  { sel: '#addOverlay .modal', open: 'add', title: 'Добавление модов',
    text: 'Три способа: «Поиск» по названию во всём каталоге; «Сборка → Пак → Мод» — выбор по цепочке (можно добавить и ЦЕЛУЮ сборку разом); «По ссылке» — форк-мод по URL дескриптора. Выбранное попадает в очередь профиля, а ставится потом кнопкой «Установить все».' },
  { sel: '#railToggle', title: 'Фильтры и вид списка',
    text: 'Левая панель: как группировать список (по папкам или по разделам ModuleInfo), поиск по имени/описанию, фильтры по состоянию, «только скрытые», «без тега» и чипы ваших тегов и меток сборок. Панель сворачивается кнопкой ▤.' },
  { sel: '#treeBody', title: 'Список модов',
    text: 'Все моды профиля и установленные на диске. Клик по строке — ВЫДЕЛИТЬ её (активная, подсветка — ею управляются «Связи» и карточка ⓘ). Галочка «В профиле» включает/выключает мод в наборе, «В игре» — подключён ли он в самой игре. У модов с несколькими версиями справа переключатель сборки (REDUX / UNI / имя пака) — можно выбрать, из какой сборки взять мод. В строке также дата мода (её можно выделить и скопировать).' },
  { sel: '#treeBody', title: 'Теги, заметки, скрытие',
    text: 'Правый клик по моду: 🙈 скрыть из списка, 🏷 свои теги (по ним потом фильтровать — чипы кликабельны), 📝 личная заметка (значок 📝 у кнопки ⓘ, текст — при наведении). Всё это видно и в карточке ⓘ (подвижное окно, можно открыть несколько). Если выделено несколько модов — скрытие и теги применяются ко всем сразу.' },
  { sel: '#treeBody', title: 'Массовый выбор',
    text: 'Слева от строк — чекбоксы для массовых действий: отметьте несколько (Shift — диапазон, Ctrl — по одной), и правый клик применит действие ко всему выбору. «✖ Снять выбор» в тулбаре (или Esc) сбрасывает отметки. Выделение (подсветка) и выбор (галочки) — разные вещи: первое для просмотра, второе для пакетных операций.' },
  { sel: '#relWrap', title: 'Связи мода',
    text: 'Сворачиваемый список под основным. Выделите мод — здесь теми же строками появятся моды, которые он ТРЕБУЕТ, для которых он НУЖЕН и с которыми КОНФЛИКТУЕТ (в т.ч. односторонние конфликты — кто объявил конфликт с ним). Клик по связи — перейти к тому моду.' },
  { sel: '#installBtn', title: 'Установить и обновить',
    text: '«Установить все» качает и ставит моды профиля. «Обновить все» (после «Проверить обновления») ставит новые версии, СОХРАНЯЯ ваши правки в файлах мода. Бесконфликтные обновления по умолчанию идут в тихом режиме, окно с решениями всплывает только когда нужен ваш выбор. Редкие действия — в «⋯ Ещё».' },
  { sel: '#compatBtn', title: 'Проверка совместимости',
    text: 'Покажет, всё ли в наборе согласовано: есть ли ровно одна база, на месте ли фиксы, нет ли конфликтов и незакрытых зависимостей, не сменилась ли база (предупреждение о сейвах). Ничего не меняет — только показывает, решение за вами.' },
  { sel: '.side-tabs', title: 'Игра и Журнал',
    text: 'Правая панель. Вкладка «Игра» — синхронизация профиля с тем, что реально подключено в игре (стрелками туда/обратно). Вкладка «Журнал» — что делает лаунчер по шагам, там же «Подробный лог» для диагностики.' },
  { sel: '#settingsOverlay .modal', open: 'settings', title: 'Настройки',
    text: 'Здесь: папка игры и 🔄 проверка новой версии лаунчера; «тихий режим» обновлений; «полное описание в списке» и «показывать скрытые моды». В блоке «Дополнительно» — база профиля, порядок проверки обновлений по нескольким сборкам, репозиторий, GitHub-токен (нужен только для приватного) и дополнительные репозитории-форки. Обычно тут менять ничего не нужно.' },
  { sel: '#launchBtn', title: 'Играть',
    text: 'Запускает Space Rangers HD с текущими модами (перед запуском лаунчер выставит правильный порядок подключения, чтобы игра не ругалась). Это всё основное — можно начинать!' },
  { sel: '#helpBtn', title: 'Справка и повтор',
    text: 'Этот тур всегда можно запустить снова кнопкой 🎓, а краткую справку со ссылкой на полную инструкцию на GitHub — кнопкой ❔ рядом.' },
];
let tourIdx = 0;
// окна, которые тур открывает на своём шаге (чтобы показать их вживую), с функциями
// открытия; закрываются автоматически при переходе на другой шаг / завершении тура.
const TOUR_MODALS = {
  profile: { overlay: 'profileOverlay', open: openProfiles },
  add: { overlay: 'addOverlay', open: openAdd },
  settings: { overlay: 'settingsOverlay', open: openSettings },
};
let _tourModal = null;   // ключ окна, открытого туром (или null)
function tourSyncModal(step) {
  const want = (step && step.open) || null;
  if (_tourModal && _tourModal !== want) {   // закрыть открытое туром чужое окно
    try { hide(TOUR_MODALS[_tourModal].overlay); } catch (e) {}
    _tourModal = null;
  }
  if (want && _tourModal !== want) {         // открыть окно текущего шага
    try { TOUR_MODALS[want].open(); } catch (e) {}
    _tourModal = want;
  }
}

function maybeAutoTour() {
  if (STATE && !STATE.tutorial_done) setTimeout(() => startTour(), 500);
}
function startTour() {
  tourIdx = 0;
  $('tourOverlay').classList.remove('hidden');
  renderTourStep();
  setTimeout(() => { try { $('tourNext').focus(); } catch (e) {} }, 40);
}
function endTour() {
  tourSyncModal(null);                  // закрыть окно, если тур его открывал
  $('tourOverlay').classList.add('hidden');
  $('tourOverlay').style.background = '';
  if (STATE) STATE.tutorial_done = true;
  try { api().set_tutorial_done(true); } catch (e) {}
}
function tourGo(d) {
  const n = tourIdx + d;
  if (n < 0) return;
  if (n >= TOUR_STEPS.length) { endTour(); return; }
  tourIdx = n;
  renderTourStep();
}
function renderTourStep() {
  const step = TOUR_STEPS[tourIdx];
  tourSyncModal(step);                   // открыть/закрыть окно шага (настройки/профили/добавление)
  $('tourTipTitle').textContent = step.title;
  $('tourTipText').textContent = step.text;
  $('tourStep').textContent = `Шаг ${tourIdx + 1} из ${TOUR_STEPS.length}`;
  $('tourPrev').style.display = tourIdx === 0 ? 'none' : '';
  $('tourNext').textContent = (tourIdx === TOUR_STEPS.length - 1) ? 'Готово ✓' : 'Далее →';
  // окно открыто скрывает фокус на своём первом поле — вернём фокус на кнопку тура,
  // чтобы навигация Enter/Tab оставалась на туре, а не внутри окна
  if (_tourModal) setTimeout(() => { try { $('tourNext').focus(); } catch (e) {} }, 60);
  const hi = $('tourHi'), tip = $('tourTip'), ov = $('tourOverlay');
  const el = step.sel ? document.querySelector(step.sel) : null;
  const r = (el && (el.offsetWidth || el.offsetHeight)) ? el.getBoundingClientRect() : null;
  if (r) {
    // подсветка вокруг цели; затемнение фона делает box-shadow самой подсветки
    const pad = 6;
    hi.style.display = 'block';
    hi.style.left = (r.left - pad) + 'px';
    hi.style.top = (r.top - pad) + 'px';
    hi.style.width = (r.width + pad * 2) + 'px';
    hi.style.height = (r.height + pad * 2) + 'px';
    ov.style.background = '';
    positionTourTip(tip, r);
  } else {
    // без цели — карточка по центру, затемняем весь оверлей
    hi.style.display = 'none';
    ov.style.background = 'rgba(0,0,0,.62)';
    tip.style.transform = 'translate(-50%,-50%)';
    tip.style.left = '50%';
    tip.style.top = '50%';
  }
}
function positionTourTip(tip, r) {
  tip.style.transform = '';
  tip.style.left = '0px'; tip.style.top = '0px';        // показать для замера
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  const vw = window.innerWidth, vh = window.innerHeight, gap = 14;
  let top = r.bottom + gap;                             // по умолчанию — под целью
  if (top + th > vh - 8) top = r.top - gap - th;        // не влезло снизу → над целью
  top = Math.max(8, Math.min(top, vh - th - 8));
  let left = r.left + r.width / 2 - tw / 2;             // по центру цели
  left = Math.max(8, Math.min(left, vw - tw - 8));
  tip.style.left = left + 'px';
  tip.style.top = top + 'px';
}
window.addEventListener('resize', () => {
  if (!$('tourOverlay').classList.contains('hidden')) renderTourStep();
});

const _trap = [];   // стек открытых модалок: {el, box, prev, handler} — для focus-trap и возврата фокуса
function show(id) {
  const el = $(id);
  const prev = document.activeElement;               // куда вернуть фокус при закрытии
  el.classList.remove('hidden');
  // доступность: при открытии окна ставим фокус на первое поле, иначе на первую кнопку —
  // чтобы можно было сразу печатать / подтвердить Enter, а не кликать мышью
  const box = el.querySelector('.modal, .popover, .ctx-menu') || el;
  const target = box.querySelector('input:not([type=hidden]),select,textarea')
    || box.querySelector('button');
  if (target) setTimeout(() => { try { target.focus(); } catch (e) {} }, 30);
  // focus-trap: Tab/Shift+Tab не выпускают фокус за пределы окна (циклично)
  const handler = (e) => {
    if (e.key !== 'Tab') return;
    const f = focusables(box);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    const inside = box.contains(document.activeElement);
    if (e.shiftKey && (document.activeElement === first || !inside)) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (document.activeElement === last || !inside)) { e.preventDefault(); first.focus(); }
  };
  el.addEventListener('keydown', handler);
  _trap.push({ el, box, prev, handler });
}
function hide(id) {
  const el = $(id);
  el.classList.add('hidden');
  // снять focus-trap этой модалки и вернуть фокус туда, где он был до открытия
  const i = _trap.map((t) => t.el).lastIndexOf(el);
  if (i >= 0) {
    const t = _trap.splice(i, 1)[0];
    el.removeEventListener('keydown', t.handler);
    try { if (t.prev && document.contains(t.prev)) t.prev.focus(); } catch (e) {}
  }
}
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
  const done = (fn) => { hide('confirmOverlay'); $('confirmOverlay').onkeydown = null; if (fn) fn(); };
  ok.onclick = () => done(onOk);
  cancel.onclick = () => done(onCancel);
  // Enter подтверждает (кроме многострочных полей), Esc отменяет — без мыши
  $('confirmOverlay').onkeydown = (e) => {
    if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA' && !opts.okHidden) { e.preventDefault(); done(onOk); }
    else if (e.key === 'Escape') { e.preventDefault(); done(onCancel); }
  };
}
// модальный ввод строки/текста (замена window.prompt, которого нет в pywebview).
// Возвращает Promise<string> (значение) или Promise<null> при отмене.
function promptModal(title, hint, initial, multiline, opts) {
  opts = opts || {};
  return new Promise((resolve) => {
    $('promptTitle').textContent = title;
    $('promptHint').textContent = hint || '';
    $('promptHint').style.display = hint ? '' : 'none';
    const inp = $('promptInput'), area = $('promptArea');
    const field = multiline ? area : inp;
    const other = multiline ? inp : area;
    other.style.display = 'none';
    field.style.display = '';
    field.value = initial || '';
    // чипы существующих значений (теги): клик добавляет значение в поле через запятую
    const chipsBox = $('promptChips');
    if (chipsBox) {
      const chips = opts.chips || [];
      if (chips.length) {
        chipsBox.style.display = '';
        chipsBox.innerHTML = '<span class="sub">Существующие теги (клик — добавить):</span><br>' +
          chips.map((t) => `<button type="button" class="chip-pick" data-tag="${esc(t)}">${esc(t)}</button>`).join('');
        chipsBox.querySelectorAll('.chip-pick').forEach((b) => b.onclick = () => {
          const parts = field.value.split(',').map((s) => s.trim()).filter(Boolean);
          const t = b.dataset.tag;
          if (!parts.some((p) => p.toLowerCase() === t.toLowerCase())) parts.push(t);
          field.value = parts.join(', ');
          try { field.focus(); } catch (e) {}
        });
      } else { chipsBox.style.display = 'none'; chipsBox.innerHTML = ''; }
    }
    show('promptOverlay');
    setTimeout(() => { try { field.focus(); field.select && field.select(); } catch (e) {} }, 40);
    const done = (val) => { hide('promptOverlay'); $('promptOverlay').onkeydown = null; resolve(val); };
    $('promptOk').onclick = () => done(field.value);
    $('promptCancel').onclick = () => done(null);
    $('promptOverlay').onkeydown = (e) => {
      // однострочное — Enter сохраняет; многострочное (заметка) — Ctrl/Cmd+Enter (обычный
      // Enter переносит строку); Esc отменяет
      if (e.key === 'Enter' && (!multiline || e.ctrlKey || e.metaKey)) { e.preventDefault(); done(field.value); }
      else if (e.key === 'Escape') { e.preventDefault(); done(null); }
    };
  });
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
