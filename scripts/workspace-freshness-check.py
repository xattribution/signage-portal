"""Offline DOM regression checks for workspace freshness; no real network is used."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--preview', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
html = args.preview.read_text(encoding='utf-8')
# Shorten only the request deadline inside this disposable test document.
# The shipped application retains its 15-second deadline.
html = html.replace('timeout = 15000', 'timeout = 200')
injection = '''
window.freshnessCase = 'unavailable';
const freshnessFetch = window.fetch;
window.fetch = async (url, options={}) => {
  if (url === '/api/overview') {
    if (window.freshnessCase === 'unavailable') return new Response('{}', {status:503});
    if (window.freshnessCase === 'not-modified') return new Response(null, {status:304});
    if (window.freshnessCase === 'timeout') return new Promise((resolve,reject)=>{
      options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true});
    });
  }
  return freshnessFetch(url,options);
};
'''
assert '\ninit();' in html
html = html.replace('\ninit();', '\n' + injection + '\ninit();')
results = {'scope': __doc__, 'checks': {}, 'page_errors': []}
with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
    page = browser.new_page(viewport={'width':390,'height':844})
    page.on('pageerror', lambda e: results['page_errors'].append(str(e)))
    page.set_content(html, wait_until='domcontentloaded')
    page.wait_for_selector('#connection-banner:not([hidden])')
    assert 'has not loaded yet' in page.locator('#workspace-notice-detail').inner_text()
    assert page.locator('.stream').count() == 0
    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
    results['checks']['first_load_failure_visible_and_mobile_fits'] = True
    page.evaluate("window.freshnessCase='normal'")
    page.locator('#retry-workspace').click()
    page.wait_for_selector('.stream')
    assert not page.locator('#connection-banner').is_visible()
    results['checks']['first_load_retry_recovers'] = True

    page.evaluate("window.freshnessCase='unavailable'")
    page.locator('#refresh-now').click()
    page.wait_for_selector('#connection-banner:not([hidden])')
    page.evaluate("window.freshnessCase='normal'")
    # Exercise the real retry scheduler. No user click or online event triggers recovery.
    page.wait_for_selector('#connection-banner', state='hidden', timeout=12000)
    results['checks']['automatic_backoff_retry_recovers_without_user_action'] = True

    count = page.locator('.stream').count()
    page.evaluate("window.freshnessCase='timeout'")
    page.locator('#refresh-now').click()
    page.wait_for_selector('#connection-banner:not([hidden])')
    assert page.locator('.stream').count() == count
    assert page.locator('#refresh-now').get_attribute('aria-busy') == 'false'
    results['checks']['abort_deadline_keeps_previous_snapshot'] = True
    page.evaluate("window.freshnessCase='normal'")
    page.locator('#retry-workspace').click()
    page.wait_for_selector('#connection-banner', state='hidden')

    fresh = browser.new_page()
    fresh.on('pageerror', lambda e: results['page_errors'].append(str(e)))
    fresh.set_content(html.replace("window.freshnessCase = 'unavailable';", "window.freshnessCase = 'not-modified';"))
    fresh.wait_for_selector('#connection-banner:not([hidden])')
    assert fresh.locator('.stream').count() == 0
    assert 'has not loaded yet' in fresh.locator('#workspace-notice-detail').inner_text()
    results['checks']['not_modified_cannot_initialize_missing_snapshot'] = True
    assert not results['page_errors'], results['page_errors']
    browser.close()
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(results, indent=2), encoding='utf-8')
print(json.dumps(results, indent=2))
