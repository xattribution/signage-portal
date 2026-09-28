"""Check the delivered navigation against the offline workspace preview.

This verifies DOM/layout/interaction in Chromium, not a UniFi, NAS, TLS or CSP
integration. The separate regression and overlay suites exercise the real API.
"""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--preview', type=Path, required=True)
parser.add_argument('--output', type=Path, default=Path('navigation-evidence'))
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
results = {'scope': __doc__.strip(), 'checks': {}, 'page_errors': []}

with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
    page = browser.new_page(viewport={'width':1440,'height':1050}, color_scheme='light')
    page.set_default_timeout(6000)
    page.on('pageerror', lambda e: results['page_errors'].append(str(e)))
    # Native file/network navigation is blocked here; do not alter browser policy.
    page.set_content(args.preview.read_text(), wait_until='domcontentloaded')
    page.wait_for_selector('.stream')
    assert page.locator('body > header.topbar').count() == 1
    assert page.locator('.sidebar,.breadcrumb,.mobile-account').count() == 0
    assert page.locator('.workspace').bounding_box()['x'] == 0
    assert page.locator('#tabs button[aria-current="page"]').count() == 1
    assert page.locator('#tabs button svg').count() == 0
    results['checks']['single_header_no_sidebar_no_duplicate_utilities'] = True
    page.screenshot(path=str(args.output/'streams-desktop.png'), full_page=True)

    def navigate(view):
        if page.locator('#nav-toggle').is_visible() and not page.locator('#tabs').is_visible():
            page.locator('#nav-toggle').click()
        page.locator(f'#tabs button[data-view="{view}"]').click()
        page.wait_for_selector(f'#view-{view}.active')
        page.wait_for_timeout(100)
        assert page.locator('#tabs button[aria-current="page"]').count() == 1
        assert page.locator(f'#tabs button[data-view="{view}"]').get_attribute('aria-current') == 'page'

    for view in ['library','displays','activity','users','streams']:
        navigate(view)
    results['checks']['all_five_views_navigate'] = True
    navigate('displays')
    before = page.locator('.display-url a').first.get_attribute('href')
    control = page.locator('#assign-1')
    target = '2' if control.input_value() != '2' else '1'
    control.select_option(target)
    page.wait_for_timeout(300)
    assert page.locator('#assign-1').input_value() == target
    assert page.locator('.display-url a').first.get_attribute('href') == before
    assert 'not the UniFi console' in page.locator('#view-displays .note').inner_text()
    results['checks']['sample_assignment_keeps_display_url_and_shows_integration_boundary'] = True
    page.screenshot(path=str(args.output/'displays-desktop.png'), full_page=True)

    # Theme and account controls remain single instances at both sizes.
    page.locator('#theme-toggle').click()
    page.locator('#theme-toggle').click()
    assert page.locator('html').get_attribute('data-theme') == 'dark'
    navigate('streams')
    page.wait_for_timeout(250)
    page.screenshot(path=str(args.output/'streams-dark.png'), full_page=True)
    page.locator('#theme-toggle').click()
    assert page.locator('html').get_attribute('data-theme') is None
    results['checks']['theme_cycle'] = True

    for width in [1920,1440,1280,1180,1024,901,900,768,600,480,390,360,320]:
        page.set_viewport_size({'width':width,'height':900})
        for view in ['streams','library','displays','activity','users']:
            navigate(view)
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), f'{width}px {view}: document overflow'
            assert page.locator('#app-header').bounding_box()['height'] <= 68, f'{width}px: header wrapped'
            assert page.locator('#theme-toggle').is_visible()
            assert page.locator('#refresh-now').is_visible()
            assert page.locator('#account-menu summary').is_visible()
        assert page.locator('#nav-toggle').is_visible() == (width <= 900)
    results['checks']['responsive_matrix'] = {'widths':[1920,1440,1280,1180,1024,901,900,768,600,480,390,360,320], 'views':5, 'overflow':False}

    page.set_viewport_size({'width':390,'height':844})
    navigate('streams')
    page.evaluate('window.scrollTo(0,0)')
    page.screenshot(path=str(args.output/'streams-mobile.png'), full_page=True)
    page.locator('#nav-toggle').click()
    assert page.locator('#nav-toggle').get_attribute('aria-expanded') == 'true'
    assert page.evaluate('document.activeElement.matches("#tabs button[aria-current=page]")')
    page.screenshot(path=str(args.output/'navigation-mobile.png'))
    page.keyboard.press('Escape')
    assert page.locator('#nav-toggle').get_attribute('aria-expanded') == 'false'
    assert page.evaluate('document.activeElement.id === "nav-toggle"')
    page.locator('#nav-toggle').click()
    page.locator('#tabs button[data-view="library"]').click()
    assert not page.locator('#tabs').is_visible()
    assert page.evaluate('document.activeElement.id === "main"')
    assert page.locator('#view-label').inner_text() == 'Library'
    results['checks']['mobile_keyboard_focus_escape_and_selection'] = True

    page.locator('#nav-toggle').click()
    page.locator('#tabs button[data-view="users"]').focus()
    page.keyboard.press('Tab')
    assert not page.locator('#tabs').is_visible()
    page.locator('#account-menu summary').click()
    assert page.locator('#signout').is_visible()
    page.keyboard.press('Escape')
    assert not page.locator('#signout').is_visible()
    assert page.evaluate('document.activeElement.matches("#account-menu summary")')
    page.locator('#account-menu summary').click()
    page.locator('#theme-toggle').click()
    assert not page.locator('#signout').is_visible()
    results['checks']['account_escape_outside_click_and_no_navigation_focus_trap'] = True

    # Resizing never leaves focus in a now-hidden navigation element.
    page.set_viewport_size({'width':1024,'height':900})
    page.locator('#tabs button[data-view="streams"]').focus()
    page.set_viewport_size({'width':768,'height':900})
    page.wait_for_timeout(60)
    assert page.evaluate('document.activeElement.id === "nav-toggle"')
    page.set_viewport_size({'width':1024,'height':900})
    page.wait_for_timeout(60)
    assert page.evaluate('document.activeElement.matches("#tabs button[aria-current=page]")')
    results['checks']['focus_recovers_across_breakpoint'] = True

    for width in [1440,901,900,390,320]:
        page.set_viewport_size({'width':width,'height':900})
        page.evaluate('document.querySelector("#site-name").textContent="Office signage with an exceptionally long workspace title";document.querySelector("#me").textContent="long.account.name@example.invalid"')
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth'), f'Long labels overflow at {width}'
    results['checks']['long_workspace_and_account_names_fit'] = True

    page.set_viewport_size({'width':1440,'height':1050})
    navigate('streams')
    page.locator('#refresh-now').click()
    page.wait_for_timeout(250)
    page.locator('#btn-new-stream').click()
    page.get_by_label('Name',exact=True).fill('Navigation test channel')
    page.get_by_role('button',name='Save',exact=True).click()
    page.wait_for_selector('dialog',state='detached')
    page.wait_for_timeout(350)
    assert page.get_by_role('button',name='Rename Navigation test channel',exact=True).is_visible()
    page.locator('#demo-reset').click()
    page.wait_for_timeout(300)
    page.get_by_role('button',name='Banners & motion',exact=True).first.click()
    page.wait_for_selector('.presentation-dialog')
    page.get_by_label('Message',exact=True).fill('TOP BAR TEST')
    page.get_by_role('button',name='Apply to stream',exact=True).click()
    page.wait_for_selector('dialog',state='detached')
    page.get_by_role('button',name='Banners & motion',exact=True).first.click()
    page.wait_for_selector('.presentation-dialog')
    assert page.get_by_label('Message',exact=True).input_value() == 'TOP BAR TEST'
    page.get_by_role('button',name='Cancel',exact=True).click()
    results['checks']['stream_creation_and_banner_editor_in_new_shell'] = True
    page.locator('.stream:not(.is-ss) .tools a').first.click()
    page.wait_for_selector('.demo-stage')
    assert page.locator('.demo-stage img').count() >= 1
    page.get_by_role('button',name='Close',exact=True).click()
    results['checks']['offline_playback_illustration_opens'] = True

    # Recovery uses the original request path; no new polling implementation.
    page.evaluate('window.savedFetch=window.fetch;window.fetch=async()=>new Response(JSON.stringify({detail:"test outage"}),{status:503})')
    page.locator('#refresh-now').click()
    page.wait_for_selector('#connection-banner:not([hidden])')
    assert page.locator('#workspace-notice-title').inner_text() == 'Workspace updates unavailable'
    assert 'Last successful refresh:' in page.locator('#workspace-notice-detail').inner_text()
    assert 'does not report display playback health' in page.locator('#workspace-notice-detail').inner_text()
    assert page.locator('#connection-state,#connection-led').count() == 0
    page.screenshot(path=str(args.output/'workspace-warning.png'), full_page=True)
    page.evaluate('window.fetch=window.savedFetch')
    page.locator('#retry-workspace').click()
    page.wait_for_selector('#connection-banner',state='hidden')
    assert page.evaluate('document.activeElement.id === "refresh-now"')
    assert page.locator('#refresh-now').get_attribute('aria-busy') == 'false'
    results['checks']['workspace_failure_visible_retry_recovers_focus'] = True

    page.evaluate('window.dispatchEvent(new Event("offline"))')
    assert page.locator('#workspace-notice-title').inner_text() == 'Browser is offline'
    assert page.locator('#connection-banner').is_visible()
    page.evaluate('window.dispatchEvent(new Event("online"))')
    page.wait_for_selector('#connection-banner',state='hidden')
    results['checks']['browser_offline_and_online_recovery'] = True

    stream_count = page.locator('.stream').count()
    page.evaluate('window.fetch=async()=>new Response("<html>proxy error</html>",{status:200,headers:{"Content-Type":"text/html"}})')
    page.locator('#refresh-now').click()
    page.wait_for_selector('#connection-banner:not([hidden])')
    assert page.locator('.stream').count() == stream_count
    page.evaluate('window.fetch=window.savedFetch')
    page.locator('#retry-workspace').click()
    page.wait_for_selector('#connection-banner',state='hidden')
    results['checks']['malformed_200_response_keeps_last_valid_workspace'] = True

    page.evaluate('window.fetch=async()=>new Response(null,{status:304})')
    page.locator('#refresh-now').click()
    page.wait_for_timeout(100)
    assert not page.locator('#connection-banner').is_visible()
    assert page.locator('.stream').count() == stream_count
    page.evaluate('window.fetch=window.savedFetch')
    results['checks']['not_modified_response_preserves_state_without_false_warning'] = True
    assert not results['page_errors'], results['page_errors']
    browser.close()

(args.output/'navigation-results.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results,indent=2))
