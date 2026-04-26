// Shared escapeHtml / capitalize helpers used by filters.js, list.js,
// map.js, detail.js. Loads before all module scripts so each IIFE can
// reference these as bare globals (resolves via window.*).

window.escapeHtml = function (s) {
  return (s == null ? '' : String(s)).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
};

window.capitalize = function (s) {
  if (!s) return '';
  return s[0].toUpperCase() + s.slice(1).toLowerCase();
};
