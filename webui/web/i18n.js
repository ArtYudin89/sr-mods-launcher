'use strict';

// Interface text only. Mod descriptions, names, notes and identifiers retain
// their original language; they are never scanned or rewritten in the DOM.
window.LauncherI18n = (() => {
  let language = 'ru';
  const catalog = window.LAUNCHER_EN || {};
  const staticTexts = new Map();
  const staticAttributes = [];
  const patterns = [];
  const escapeRegExp = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  for (const [source, translated] of Object.entries(catalog)) {
    if (!/\{\d+\}/.test(source)) continue;
    const slots = [];
    const regex = source.split(/(\{\d+\})/).map(part => {
      if (/^\{\d+\}$/.test(part)) {
        slots.push(+part.slice(1, -1));
        return '([\\s\\S]*?)';
      }
      return escapeRegExp(part);
    }).join('');
    patterns.push({source, prefix: source.split(/\{\d+\}/)[0], regex: new RegExp('^' + regex + '$'), translated, slots});
  }
  patterns.sort((a, b) => b.source.replace(/\{\d+\}/g, '').length - a.source.replace(/\{\d+\}/g, '').length);

  function t(source, ...values) {
    if (Array.isArray(source) && Object.prototype.hasOwnProperty.call(source, 'raw')) {
      source = source.map((part, index) => part + (index < values.length ? '{' + index + '}' : '')).join('');
    }
    const text = language === 'en' ? (catalog[source] || source) : source;
    return values.length ? String(text).replace(/\{(\d+)\}/g, (whole, index) =>
      +index < values.length ? String(values[+index]) : whole) : text;
  }

  function message(value) {
    if (typeof value !== 'string' || language !== 'en') return value;
    if (catalog[value]) return catalog[value];
    for (const pattern of patterns) {
      if (!value.startsWith(pattern.prefix)) continue;
      const match = pattern.regex.exec(value);
      if (!match) continue;
      const values = {};
      pattern.slots.forEach((slot, index) => { values[slot] = match[index + 1]; });
      // Backend count nouns are formatted separately. Translate only a noun slot
      // following a numeric slot, keeping names, paths and other values intact.
      for (const pair of pattern.source.matchAll(/\{(\d+)\} \{(\d+)\}/g)) {
        const count = values[pair[1]], noun = values[pair[2]];
        if (!/^\d+$/.test(count)) continue;
        if (/^(мод|мода|модов)$/.test(noun)) values[pair[2]] = +count === 1 ? 'mod' : 'mods';
        if (/^(файл|файла|файлов)$/.test(noun)) values[pair[2]] = +count === 1 ? 'file' : 'files';
      }
      return pattern.translated.replace(/\{(\d+)\}/g, (whole, slot) => values[slot] ?? whole);
    }
    return value;
  }

  function captureStatic(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (node.parentElement.closest('script,style,[data-i18n-skip]')) continue;
      const text = node.nodeValue.trim();
      if (Object.prototype.hasOwnProperty.call(catalog, text)) staticTexts.set(node, {original: node.nodeValue, text});
    }
    root.querySelectorAll('[title],[placeholder],[aria-label]').forEach(element => {
      if (element.closest('[data-i18n-skip]')) return;
      for (const name of ['title', 'placeholder', 'aria-label']) {
        const source = element.getAttribute(name);
        if (source && Object.prototype.hasOwnProperty.call(catalog, source)) staticAttributes.push({element, name, source});
      }
    });
  }

  function applyStatic() {
    for (const [node, entry] of staticTexts) {
      if (node.isConnected) node.nodeValue = entry.original.replace(entry.text, t(entry.text));
    }
    for (const {element, name, source} of staticAttributes) {
      if (element.isConnected) element.setAttribute(name, t(source));
    }
    document.documentElement.lang = language;
  }

  function setLanguage(value) { language = value === 'en' ? 'en' : 'ru'; }
  function attribute(element, name) {
    const entry = staticAttributes.find(item => item.element === element && item.name === name);
    return t(entry ? entry.source : (element.getAttribute(name) || element.dataset.help || ''));
  }
  return {t, message, captureStatic, applyStatic, setLanguage, attribute, get language() { return language; }};
})();
