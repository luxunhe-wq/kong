"""Refresh recovery regression checks with a temporary server and account.

MONILITE_TEST_BROWSER=/path/to/chromium python3 tests/browser_refresh.py
"""
import os
import time

from playwright.sync_api import expect, sync_playwright

from browser_smoke import test_server


def hold_metrics(page):
    page.wait_for_function('() => !state.fetching')
    page.evaluate("""() => {
      clearTimeout(state.timer);
      window.nativeFetch=window.fetch;
      window.heldMetrics=[];
      window.fetch=(url, options) => String(url).startsWith('/api/metrics?')
        ? new Promise(resolve => heldMetrics.push({resolve, signal:options.signal}))
        : nativeFetch(url, options);
      void fetchData();
    }""")
    page.wait_for_function('() => heldMetrics.length === 1 && state.fetching')


def restore_metrics(page):
    page.evaluate("""() => {
      window.fetch=nativeFetch;
      cancelMetricsRequest();
      scheduleRefresh();void fetchData();
    }""")
    page.wait_for_function('() => !state.fetching && !state.failed')


def main():
    with test_server() as (base, _), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, executable_path=os.environ.get('MONILITE_TEST_BROWSER', os.environ.get('KONG_TEST_BROWSER')),
            args=['--no-sandbox'])
        context = browser.new_context()
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base)
        page.locator('#auth-username').fill('refresh_owner')
        page.locator('#auth-password').fill('refresh-owner-password')
        page.locator('#auth-confirm').fill('refresh-owner-password')
        page.locator('#auth-form button[type="submit"]').click()
        expect(page.locator('.metric-card')).to_have_count(4)
        page.evaluate('window.refreshSentinel = true')

        # A slow but healthy connection must not be aborted by the previous 8-second limit.
        page.wait_for_function('() => !state.fetching')
        page.evaluate("""() => {
          clearTimeout(state.timer);
          window.nativeFetch=window.fetch;
          window.fetch=(url, options) => String(url).startsWith('/api/metrics?')
            ? new Promise(resolve => setTimeout(resolve, 9000)).then(() => nativeFetch(url, options))
            : nativeFetch(url, options);
          window.slowResult=fetchData();
        }""")
        assert page.evaluate('window.slowResult') is True
        assert page.evaluate('!state.failed')
        page.evaluate('window.fetch=nativeFetch;scheduleRefresh()')
        print('A healthy response delayed by 9 seconds renders successfully.', flush=True)

        # A rendering failure must not be reported as a network failure.
        page.evaluate("""() => {
          clearTimeout(state.timer);
          window.nativeUpdatePage=updatePage;
          updatePage=()=>{throw new TypeError('simulated rendering failure');};
        }""")
        assert page.evaluate('fetchData()') is False
        expect(page.locator('#connection-error')).to_contain_text('监控数据处理失败')
        page.evaluate('updatePage=nativeUpdatePage;scheduleRefresh()')
        assert page.evaluate('fetchData()') is True

        # A suspended request may never settle, even after abort. The timer must still retry.
        hold_metrics(page)
        page.evaluate('scheduleRefresh()')
        page.wait_for_function('() => heldMetrics.length === 2', timeout=40000)
        assert page.evaluate('heldMetrics[0].signal.aborted')
        assert page.evaluate('state.fetching && !heldMetrics[1].signal.aborted')
        # A late response from the abandoned request must neither overwrite data nor unlock its successor.
        previous = page.evaluate('state.data.timestamp')
        page.evaluate("""() => {
          const obsolete={...state.data,timestamp:state.data.timestamp-300};
          heldMetrics[0].resolve(new Response(JSON.stringify(obsolete), {status:200}));
        }""")
        page.wait_for_timeout(100)
        assert page.evaluate('state.data.timestamp') == previous
        assert page.evaluate('state.fetching && state.metricsController.signal === heldMetrics[1].signal')
        restore_metrics(page)
        print('Hung requests retry; late responses cannot overwrite data or clear the new request.', flush=True)

        for event in ['visibilitychange', 'focus', 'pageshow', 'online', 'resume']:
            hold_metrics(page)
            page.evaluate("""event => {
              state.metricsStartedAt-=125000;
              clearTimeout(state.timer);state.timer=null;
              window.fetch=nativeFetch;
              const target=['visibilitychange','resume'].includes(event)?document:window;
              target.dispatchEvent(new Event(event));
            }""", event)
            page.wait_for_function('() => !state.fetching && state.timer !== null && !state.failed')
            assert page.evaluate('heldMetrics[0].signal.aborted')
        # BFCache restore replaces a pending request even if its wall-time deadline has not elapsed.
        hold_metrics(page)
        page.evaluate("""() => {
          window.fetch=nativeFetch;
          window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}));
        }""")
        page.wait_for_function('() => !state.fetching && !state.failed')
        assert page.evaluate('heldMetrics[0].signal.aborted')
        print('Foreground, focus, page restore, network recovery and resume restart refresh.', flush=True)

        # User pause takes precedence over every recovery event.
        page.evaluate("""() => {
          preferences.autoRefresh=false;scheduleRefresh();
          window.pausedCalls=0;window.fetch=(...args)=>{pausedCalls++;return nativeFetch(...args);};
          for(const event of ['visibilitychange','resume'])document.dispatchEvent(new Event(event));
          for(const event of ['focus','pageshow','online'])window.dispatchEvent(new Event(event));
        }""")
        page.wait_for_timeout(2200)
        assert page.evaluate('pausedCalls === 0 && state.timer === null')
        page.evaluate('window.fetch=nativeFetch;preferences.autoRefresh=true;scheduleRefresh()')

        context.set_offline(True)
        page.wait_for_function('() => state.failed')
        expect(page.locator('#connection-error')).to_be_visible()
        context.set_offline(False)
        page.wait_for_function('() => !state.failed && !state.fetching', timeout=10000)
        expect(page.locator('#connection-error')).to_be_hidden()
        print('Pause is preserved; temporary network loss recovers automatically.', flush=True)

        # Exercise an actual Chromium page freeze for longer than the reported two minutes.
        session = context.new_cdp_session(page)
        previous = page.evaluate('state.data.timestamp')
        session.send('Page.setWebLifecycleState', {'state':'frozen'})
        print('Freezing the browser page for 125 seconds...', flush=True)
        deadline = time.monotonic() + 125
        while time.monotonic() < deadline:
            time.sleep(min(1, deadline-time.monotonic()))
        session.send('Page.setWebLifecycleState', {'state':'active'})
        page.bring_to_front()
        page.wait_for_function('timestamp => state.data.timestamp > timestamp', arg=previous, timeout=10000)
        latest = page.evaluate('state.data.timestamp')
        page.wait_for_function('timestamp => state.data.timestamp > timestamp', arg=latest, timeout=10000)
        assert page.evaluate('window.refreshSentinel === true')
        expect(page.locator('#connection-error')).to_be_hidden()
        assert not errors, errors
        browser.close()
        print('Refresh checks passed, including 125 seconds of real browser suspension without reloading.', flush=True)


if __name__ == '__main__':
    main()
