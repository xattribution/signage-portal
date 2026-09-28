"""Administrator shell contracts; browser behavior is checked by the DOM harness."""
from collections import Counter
from html.parser import HTMLParser


class Shell(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_admin_has_one_header_and_no_sidebar(admin):
    response = admin.get('/')
    assert response.status_code == 200
    shell = Shell(response.text)
    assert sum(tag == 'header' and attrs.get('id') == 'app-header' for tag, attrs in shell.tags) == 1
    assert all(tag != 'aside' for tag, _ in shell.tags)
    counts = Counter(attrs['id'] for _, attrs in shell.tags if 'id' in attrs)
    assert all(n == 1 for n in counts.values())
    for name in ['tabs', 'main', 'theme-toggle', 'signout', 'refresh-now', 'account-menu', 'workspace-date']:
        assert counts[name] == 1


def test_navigation_uses_one_accessible_disclosure(admin):
    shell = Shell(admin.get('/').text)
    elements = {attrs['id']: (tag, attrs) for tag, attrs in shell.tags if 'id' in attrs}
    tag, nav = elements['tabs']
    assert tag == 'nav' and nav['aria-label'] == 'Workspace'
    tag, toggle = elements['nav-toggle']
    assert tag == 'button'
    assert toggle['aria-expanded'] == 'false'
    assert toggle['aria-controls'] == 'tabs'
    assert 'hidden' in elements['tab-users'][1]
    assert elements['main'][1]['tabindex'] == '-1'
    # All behavior remains in the existing same-origin external script.
    assert not any(key.startswith('on') for _, attrs in shell.tags for key in attrs)


def test_display_help_distinguishes_playback_from_controller_control(admin):
    html = admin.get('/').text
    assert 'PLAYBACK DESTINATIONS' in html
    assert 'This portal controls playback, not the UniFi console.' in html
    assert 'Device adoption, power, volume and firmware remain in UniFi Connect.' in html


def test_refresh_control_is_not_a_global_connection_badge(admin):
    html = admin.get('/').text
    elements = {attrs['id']: (tag, attrs) for tag, attrs in Shell(html).tags if 'id' in attrs}
    assert 'connection-state' not in elements
    assert 'connection-led' not in elements
    assert elements['refresh-now'][1]['aria-label'] == 'Refresh workspace'
    assert elements['refresh-now'][1]['aria-busy'] == 'false'
    assert 'Connected' not in html


def test_workspace_failure_notice_is_hidden_initially_and_retryable(admin):
    shell = Shell(admin.get('/').text)
    elements = {attrs['id']: (tag, attrs) for tag, attrs in shell.tags if 'id' in attrs}
    assert 'hidden' in elements['connection-banner'][1]
    assert elements['retry-workspace'][0] == 'button'
    assert elements['retry-workspace'][1]['type'] == 'button'
    assert elements['workspace-notice-title'][0] == 'strong'
    assert any(attrs.get('role') == 'alert' and attrs.get('aria-atomic') == 'true' for _, attrs in shell.tags)
