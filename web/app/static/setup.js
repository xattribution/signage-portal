import { $, applyTheme } from '/static/js/ui.js';
import { api } from '/static/js/api.js';
applyTheme();

api('GET', '/api/setup').then(status => {
  if (!status.needed) { location.replace('/'); return; }
  $('#site-name').textContent = status.site_name;
  $('#name').value = status.site_name;
  $('#code-field').hidden = !status.code_required;
  if (!status.lan) $('#e').textContent = 'Setup only works from a computer on the same network as the server.';
  $('#p').focus();
}).catch(error => { $('#e').textContent = error.message; });

$('#show-password').addEventListener('click', () => {
  const show = $('#p').type === 'password';
  for (const id of ['#p', '#p2']) $(id).type = show ? 'text' : 'password';
  $('#show-password').textContent = show ? 'Hide' : 'Show';
  $('#show-password').setAttribute('aria-pressed', String(show));
});

// Hover/focus tooltips for the (i) icons, same behavior as the workspace.
const tip = $('#tip');
function showTip(event) {
  const el = event.target.closest?.('[data-tip]');
  if (!el) { tip.style.display = 'none'; return; }
  tip.textContent = el.dataset.tip; tip.style.display = 'block';
  const rect = el.getBoundingClientRect();
  tip.style.left = Math.max(8, Math.min(innerWidth - tip.offsetWidth - 8, rect.left)) + 'px';
  tip.style.top = Math.min(innerHeight - tip.offsetHeight - 8, rect.bottom + 8) + 'px';
}
for (const icon of document.querySelectorAll('.i')) { icon.tabIndex = 0; icon.setAttribute('aria-label', icon.dataset.tip); }
document.addEventListener('mouseover', showTip); document.addEventListener('focusin', showTip);
document.addEventListener('focusout', () => { tip.style.display = 'none'; });

$('#f').addEventListener('submit', async event => {
  event.preventDefault();
  const button = $('#submit');
  if (button.disabled) return;
  $('#e').textContent = '';
  const name = $('#name').value.trim(), username = $('#u').value.trim(), password = $('#p').value;
  if (!name) { $('#e').textContent = 'Enter a workspace name.'; $('#name').focus(); return; }
  if (!/^[A-Za-z0-9._@-]{2,64}$/.test(username)) { $('#e').textContent = 'Use 2 to 64 letters, numbers, dots, dashes, underscores or @ for the username.'; $('#u').focus(); return; }
  if (password.length < 12) { $('#e').textContent = 'Use at least 12 characters for the password.'; $('#p').focus(); return; }
  if (password !== $('#p2').value) { $('#e').textContent = 'The two passwords do not match.'; $('#p2').focus(); return; }
  button.disabled = true; button.textContent = 'Setting up…';
  try {
    await api('POST', '/api/setup', { site_name: name, username, password, setup_code: $('#code').value.trim() });
    location.assign('/');
  } catch (error) {
    $('#e').textContent = error.message;
    if (error.status === 409) setTimeout(() => location.assign('/login'), 1500);
  } finally { button.disabled = false; button.textContent = 'Finish setup →'; }
});
