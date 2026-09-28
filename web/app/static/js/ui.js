/** Small, dependency-free DOM and dialog primitives. No HTML-string rendering. */
export const $ = (selector, root = document) => root.querySelector(selector);
let uid = 0;
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'html' || key === 'innerHTML') throw new TypeError('HTML strings are not supported');
    if (key === 'class') el.className = value;
    else if (key === 'style') {
      for (const [k, v] of Object.entries(value)) {
        if (k.startsWith('--')) el.style.setProperty(k, v); else el.style[k] = v;
      }
    } else if (key.startsWith('on') && typeof value === 'function') {
      el.addEventListener(key.slice(2), async event => {
        if (el.dataset.busy) return;
        try {
          const result = value(event);
          if (result && typeof result.then === 'function') {
            el.dataset.busy = 'true';
            const wasDisabled = el.disabled;
            if ('disabled' in el) el.disabled = true;
            try { await result; }
            finally { delete el.dataset.busy; if ('disabled' in el) el.disabled = wasDisabled; }
          }
        } catch (error) { toast(error.message || 'Something went wrong.', true); }
      });
    } else el.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child !== null && child !== undefined && child !== false) el.append(child instanceof Node ? child : String(child));
  }
  if (tag === 'button' && !el.hasAttribute('type')) el.type = 'button';
  if (el.classList.contains('i')) { el.tabIndex = 0; el.setAttribute('aria-label', el.dataset.tip || 'Help'); }
  return el;
}
export function toast(message, error = false) {
  const root = $('#toasts');
  if (!root) return;
  const node = h('div', { class: 'toast' + (error ? ' err' : ''), role: error ? 'alert' : 'status' }, message);
  root.append(node);
  while (root.children.length > 4) root.firstChild.remove();
  setTimeout(() => node.remove(), error ? 9000 : 4500);
}
export function modal({ title, body, actions = [], wide = false, onClose }) {
  const previous = document.activeElement;
  const id = 'dialog-title-' + (++uid);
  const footer = h('div', { class: 'foot' });
  const dialog = h('dialog', { class: 'modal' + (wide ? ' wide' : ''), 'aria-labelledby': id },
    h('header', { class: 'dialog-head' }, h('h2', { id }, title)), h('div', { class: 'body' }, body), footer);
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true; dialog.close(); dialog.remove();
    if (previous && previous.isConnected) previous.focus({ preventScroll: true });
    if (onClose) onClose();
  };
  for (const build of actions) footer.append(build(close));
  // Associate labels in the legacy view templates with their inputs.
  dialog.querySelectorAll('.field').forEach(field => {
    const label = field.querySelector(':scope > label');
    const input = field.querySelector('input:not([type=checkbox]),select,textarea');
    if (label && input) { input.id ||= 'field-' + (++uid); label.htmlFor = input.id; }
  });
  dialog.querySelectorAll('.err-text').forEach(el => el.setAttribute('role', 'alert'));
  dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
  dialog.addEventListener('click', event => {
    const rect = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) close();
  });
  $('#modal-root').append(dialog);
  dialog.showModal();
  return { close, box: dialog };
}
export function icon(name) {
  const paths = {
    streams: 'M3 4h7v7H3z M14 4h7v7h-7z M3 15h7v5H3z M14 15h7v5h-7z',
    library: 'M4 5h16v15H4z M8 2h12 M4 15l5-5 4 4 3-3 4 5',
    displays: 'M3 4h18v12H3z M8 21h8 M12 16v5',
    activity: 'M3 12h4l3-8 4 16 3-8h4',
    users: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M9 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M17 4a4 4 0 0 1 0 7 M22 21v-2a4 4 0 0 0-3-3.87',
    theme: 'M12 3v2 M12 19v2 M3 12h2 M19 12h2 M5.6 5.6l1.4 1.4 M17 17l1.4 1.4 M5.6 18.4 7 17 M17 7l1.4-1.4 M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8',
    refresh: 'M20 7v5h-5 M4 17v-5h5 M6.1 7a7 7 0 0 1 11.55-2L20 8 M4 16l2.35 3A7 7 0 0 0 17.9 17',
    arrow: 'M5 12h14 M13 6l6 6-6 6',
    upload: 'M12 16V3 M7 8l5-5 5 5 M4 16v5h16v-5',
  };
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  for (const [k, v] of Object.entries({ viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.6', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', class: 'icon' })) svg.setAttribute(k, v);
  const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', paths[name] || paths.streams); svg.append(path); return svg;
}
export function applyTheme() {
  let choice = 'system'; try { choice = localStorage.getItem('sp_theme') || 'system'; } catch (_) {}
  if (choice === 'system') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = choice;
  const button = $('#theme-toggle');
  if (button) button.setAttribute('aria-label', 'Theme: ' + choice + '. Change theme');
  return choice;
}
