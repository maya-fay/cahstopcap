// Escape text before putting it into HTML built with innerHTML / template
// strings. Anything that came from the database or a customer (names,
// addresses, hat names, error messages) must go through this.
function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
