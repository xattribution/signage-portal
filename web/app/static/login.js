import { $, applyTheme } from '/static/js/ui.js';
import { api } from '/static/js/api.js';
applyTheme();
api('GET', '/api/site').then(data => { $('#site-name').textContent = data.name; document.title = `Sign in · ${data.name}`; }).catch(() => {});
$('#show-password').addEventListener('click', () => {
  const show = $('#p').type === 'password'; $('#p').type = show ? 'text' : 'password';
  $('#show-password').textContent = show ? 'Hide' : 'Show'; $('#show-password').setAttribute('aria-pressed', String(show));
});
$('#f').addEventListener('submit', async event => {
  event.preventDefault();
  const button = $('#submit'); if (button.disabled) return;
  $('#e').textContent = ''; button.disabled = true; button.textContent = 'Signing in…';
  try { await api('POST', '/auth/login', { username: $('#u').value.trim(), password: $('#p').value }); location.assign('/'); }
  catch (error) { $('#e').textContent = error.message; $('#p').focus(); }
  finally { button.disabled = false; button.textContent = 'Sign in →'; }
});
