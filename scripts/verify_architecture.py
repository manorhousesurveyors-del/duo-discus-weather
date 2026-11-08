"""Browser regressions for first-pass forecasts and a completely still idle UI.

Run with run_architecture_checks.py. Weather is synthetic at the HTTP boundary;
production page, cache, normalization, map, and component code are exercised.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import time
from urllib.request import Request, urlopen

from playwright.async_api import async_playwright, expect
from streamlit.proto.BackMsg_pb2 import BackMsg

PAGES = {
    'Weather map': None,
    'Task planner': 'Task planning map',
    'Help Guide': 'Help Guide',
    'Wave watch': 'Wave Watch Map',
    'Live OGN Glider Radar': 'Live OGN Glider Radar',
    'Help & Briefing Bot': 'Help & Briefing Bot',
    'Data quality': 'Forecast quality',
}
FORBIDDEN = (
    'Forecast recovery continues in the background',
    'Weather recovery continues in the background',
    'Weather updating',
    'waiting for verified weather',
    'Awaiting live weather',
    'Weather provider cooling down',
    'background recovery is scheduled',
    'No flight assessment',
    'Open-Meteo unavailable',
)
METRICS = ('LONG MYND · NET CLIMB', 'POTENTIAL CLOUD BASE', 'USABLE MIXING TOP')
DATES = ('Sun 08 Nov', 'Mon 09 Nov', 'Tue 10 Nov', 'Wed 11 Nov', 'Thu 12 Nov')


async def main(args):
    out = Path('var/architecture-browser') / args.scenario
    out.mkdir(parents=True, exist_ok=True)
    result = {'scenario': args.scenario, 'pages': {}, 'idle': {}, 'errors': [], 'slow_pages': []}
    websocket_reruns = []
    frame_navigations = []

    def inspect_frame(payload):
        if not isinstance(payload, bytes):
            return
        message = BackMsg()
        message.ParseFromString(payload)
        if message.WhichOneof('type') == 'rerun_script':
            websocket_reruns.append(time.perf_counter())

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=os.environ.get('CHROMIUM_EXECUTABLE') or shutil.which('chromium'),
            headless=True, args=['--no-sandbox'])
        page = await browser.new_page(viewport={'width': 1650, 'height': 1450})
        page.on('pageerror', lambda error: result['errors'].append(str(error)))
        page.on('websocket', lambda socket: socket.on('framesent', inspect_frame))
        page.on('framenavigated', lambda frame: frame_navigations.append(frame.url))
        # Maps use bundled JavaScript. Blocking external tiles makes these tests
        # deterministic and guarantees fixtures never query real weather/OGN.
        await page.route('https://**/*', lambda route: route.abort())
        await page.goto(args.url)
        nav = page.locator('.st-key-active_page')
        await nav.wait_for(timeout=60000)
        frames = page.locator('iframe[title="map_component.duo_map"]')

        def frame():
            return frames.last.content_frame

        def map_root():
            return frame().locator('.leaflet-container')

        async def counter(label):
            value = await page.get_by_text(label, exact=False).inner_text()
            return float(value.rsplit(':', 1)[-1])

        async def provider_statistics():
            # The footer is deliberately static. A separate fixture-only
            # counter catches HTTP calls even without a Streamlit rerender.
            text = await page.get_by_text('Fixture statistics URL:', exact=False).inner_text()
            with urlopen(text.split(': ', 1)[1], timeout=2) as response:
                return json.load(response)

        async def live_request_count():
            return (await provider_statistics())['requests']

        async def settle():
            # Streamlit may receive the widget event just after click() returns.
            # Observe that transition before checking the idle script flag.
            await page.wait_for_timeout(150)
            await expect(page.get_by_test_id('stApp')).not_to_have_attribute(
                'data-test-script-state', 'running', timeout=60000)
            await page.get_by_text('Fixture full renders:', exact=False).or_(
                page.get_by_test_id('stException')).first.wait_for(timeout=60000)
            # Allow the component's single document-cache acknowledgement to
            # finish. It may rerun its fragment, but never the full application.
            await page.wait_for_timeout(150)
            errors = await page.get_by_test_id('stException').all_text_contents()
            assert not errors, errors
            content = await page.locator('body').inner_text()
            assert not [text for text in FORBIDDEN if text in content], content

        async def after_full_render(previous):
            await page.wait_for_function("""previous =>
                [...document.querySelectorAll('[data-testid="stCaptionContainer"]')].some(
                    el => /^Fixture full renders: /.test(el.innerText) &&
                    Number(el.innerText.split(':').at(-1)) > previous)
            """, arg=previous, timeout=60000)
            await settle()

        async def completed_map():
            await expect(frames).to_have_count(1)
            await map_root().wait_for(timeout=60000)
            await map_root().evaluate("""() => new Promise(resolve => {
                const ready = () => window._duoRenderTiming?.readyAt ? resolve() : requestAnimationFrame(ready);
                ready();
            })""")
            await settle()

        async def idle_check(name):
            """Catch reruns even if Streamlit reuses the iframe element itself."""
            await completed_map()
            await page.screenshot(path=str(out / (name + '.png')), full_page=True)
            await page.evaluate("""() => {
                window.__idleFrames = {added: 0, removed: 0};
                window.__idleObserver?.disconnect();
                const count = node => node.nodeType !== 1 ? 0 :
                    (node.matches('iframe[title="map_component.duo_map"]') ? 1 : 0) +
                    node.querySelectorAll('iframe[title="map_component.duo_map"]').length;
                window.__idleObserver = new MutationObserver(mutations => {
                    for (const mutation of mutations) {
                        for (const node of mutation.addedNodes) window.__idleFrames.added += count(node);
                        for (const node of mutation.removedNodes) window.__idleFrames.removed += count(node);
                    }
                });
                window.__idleObserver.observe(document.body, {childList: true, subtree: true});
            }""")
            identity = await map_root().evaluate("""() => {
                window.__idleMap = window.map_div;
                return {readyAt: window._duoRenderTiming.readyAt,
                        leaflet: window.map_div._leaflet_id};
            }""")
            before = {
                'full_renders': await counter('Fixture full renders:'),
                'weather_requests': await live_request_count(),
                'websocket_reruns': len(websocket_reruns),
                'frame_navigations': len(frame_navigations),
            }
            bounds = await map_root().bounding_box()
            started = time.perf_counter()
            await page.wait_for_timeout(10000)
            after = {
                'full_renders': await counter('Fixture full renders:'),
                'weather_requests': await live_request_count(),
                'websocket_reruns': len(websocket_reruns),
                'frame_navigations': len(frame_navigations),
            }
            mutations = await page.evaluate('window.__idleFrames')
            current = await map_root().evaluate("""() => ({
                readyAt: window._duoRenderTiming.readyAt,
                leaflet: window.map_div._leaflet_id,
                sameObject: window.map_div === window.__idleMap
            })""")
            assert before == after, (name, before, after)
            assert mutations == {'added': 0, 'removed': 0}, (name, mutations)
            assert current.pop('sameObject'), (name, current)
            assert current == identity, (name, identity, current)
            assert bounds == await map_root().bounding_box(), name + ': map jumped while idle'
            result['idle'][name] = {
                'seconds': round(time.perf_counter() - started, 2),
                'extra_full_renders': 0, 'extra_weather_requests': 0,
                'extra_websocket_reruns': 0, 'extra_frame_navigations': 0,
                'iframe_additions': 0, 'iframe_removals': 0,
            }
            print(json.dumps({'scenario': args.scenario, 'idle_check': name, **result['idle'][name]}), flush=True)
            await page.evaluate('window.__idleObserver.disconnect()')

        await settle()
        # First completed render, before ANY recovery wait or user interaction.
        assert await counter('Fixture full renders:') == 1
        model = page.get_by_role('combobox', name='Forecast model', exact=True)
        await expect(model).to_have_value('DWD ICON-EU · 7 km')
        first_metrics = {}
        for label in METRICS:
            card = page.get_by_test_id('stMetric').filter(has_text=label)
            first_metrics[label] = await card.get_by_test_id('stMetricValue').inner_text()
        assert all(re.search(r'\d', value) and '—' not in value for value in first_metrics.values()), first_metrics
        first_text = await page.locator('body').inner_text()
        if args.scenario == 'healthy':
            assert await page.get_by_text('8/8 local hours covered', exact=False).count() == 5
        elif args.scenario in ('429', 'timeout', 'deferred'):
            assert re.search(r'MET Norway', first_text, re.I), first_text
            assert re.search(r'assum|estimat\w*.*upper|upper.*estimat', first_text, re.I), first_text
        else:
            assert 'DEMONSTRATION' in first_text, first_text
            assert 'FULLY ACHIEVABLE' not in first_text
        for day in DATES:
            label = page.get_by_test_id('stMetricLabel').filter(has_text=day)
            value = await page.get_by_test_id('stMetric').filter(has=label).get_by_test_id('stMetricValue').inner_text()
            assert ('★' in value or re.search(r'\d', value)) and '—' not in value, (day, value)
            if args.scenario != 'healthy':
                card = await page.get_by_test_id('stColumn').filter(has=label).inner_text()
                assert ('Demo' if args.scenario == 'offline' else 'Estimated') in card, card
                assert '★' not in value, (day, value)
        result['first_pass_metrics'] = first_metrics
        result['first_pass_full_renders'] = 1
        result['first_pass_weather_requests'] = await counter('Fixture HTTP requests:')
        first_requests = await provider_statistics()
        meteo = [query for host, query in first_requests['calls'] if host == 'api.open-meteo.com']
        met = [query for host, query in first_requests['calls'] if host == 'api.met.no']
        attempts = [query for host, query in first_requests['transport_attempts'] if host == 'api.open-meteo.com']
        assert len(attempts) == 1, first_requests
        assert len(attempts[0]['latitude'].split(',')) == 9, first_requests
        assert len(meteo) == (0 if args.scenario == 'deferred' else 1), first_requests
        assert first_requests['deferred_raises'] == int(args.scenario == 'deferred'), first_requests
        assert len(met) == (0 if args.scenario == 'healthy' else 9), first_requests
        assert len({(item['lat'][0], item['lon'][0]) for item in met}) == len(met), first_requests
        assert first_requests['provider_span_ms'] < 2000, first_requests
        result['first_provider_span_ms'] = round(first_requests['provider_span_ms'], 2)
        await idle_check('clean_weather')

        await page.get_by_role('combobox', name='Map mode', exact=True).click()
        await page.get_by_role('option', name='Windy-style Wind Stream', exact=True).click()
        await completed_map()
        await frame().locator('[data-wind-view=arrows]').wait_for()
        assert not await frame().locator('.duo-wind-heat,.duo-wind-raster').count()
        await expect(frame().locator('[data-reference=bga]')).not_to_be_checked()
        domain = await map_root().evaluate('() => window.map_div._duoWind.bounds')
        assert (domain['latMin'], domain['latMax'], domain['lonMin'], domain['lonMax']) == (49.5, 61, -11, 2.5), domain
        coverage = await map_root().evaluate("""() =>
            [[50.5,-3.5],[52.5,-2.9],[54.5,-2],[57.2,-3.8],[53.3,-7.5]].map(
                ([lat,lon]) => window.map_div._duoWind.sampleAt(lat,lon))""")
        assert all(sample and all(isinstance(sample.get(key), (int, float)) for key in ('u', 'v')) for sample in coverage), coverage
        levels = await map_root().evaluate("""() => [0,1,2,3,4,5].map(
            level => window.map_div._duoWind.sampleAt(52.5,-2.9,undefined,level))""")
        assert all(sample and all(isinstance(sample.get(key), (int, float)) for key in ('u', 'v')) for sample in levels), levels
        stats = await map_root().evaluate('() => window.map_div._duoWind.renderStats')
        assert stats['style']['colour'] == '#111827', stats
        assert stats['style']['arrowWidth'] >= 2.2, stats
        assert 7 <= stats['style']['headLength'] <= 9, stats
        assert stats['style']['headWidth'] >= 5, stats
        assert stats['arrowCount'] > 0 and stats['animationRunning'], stats
        assert await live_request_count() == first_requests['requests']
        result['wind_initial'] = stats
        controls = []
        before_controls = await counter('Fixture full renders:')
        for mode in ('barbs', 'off', 'arrows'):
            duration = await map_root().evaluate("""(element, mode) => {
                const start = performance.now();
                document.querySelector(`[data-wind-view="${mode}"]`).click();
                return performance.now() - start;
            }""", mode)
            assert await map_root().evaluate('() => window.map_div._duoWind.display') == mode
            assert duration < 100, (mode, duration)
            if mode == 'barbs':
                stats = await map_root().evaluate('() => window.map_div._duoWind.renderStats')
                assert stats['barbCount'] > 0 and not stats['animationRunning'], stats
            controls.append(round(duration, 2))
        assert await counter('Fixture full renders:') == before_controls
        await frames.last.scroll_into_view_if_needed()
        box = await map_root().bounding_box()
        xy = await map_root().evaluate('() => {const p=window.map_div.latLngToContainerPoint([53.2,-4.5]);return [p.x,p.y];}')
        await page.mouse.click(box['x'] + xy[0], box['y'] + xy[1])
        await expect(frame().locator('.duo-callout').last).to_contain_text('Wind')
        callout_text = await frame().locator('.duo-callout').last.text_content()
        assert 'Unavailable' not in callout_text, callout_text
        cells = await frame().locator('.duo-callout').last.locator('td').all_text_contents()
        assert cells and not any(cell.strip() == '—' for cell in cells), cells
        if args.scenario in ('429', 'timeout', 'deferred'):
            assert 'MET Norway' in callout_text, callout_text
            assert re.search(r'assum|estimated', callout_text, re.I), callout_text
        elif args.scenario == 'offline':
            assert 'DEMONSTRATION' in callout_text, callout_text
            assert 'Open-Meteo' not in callout_text, callout_text
        await frame().locator('[data-level="3"]').click()
        result['wind_controls_ms'] = controls
        await idle_check('wind_weather')

        # Exercise every lazy page, with no Weather/Task headings bleeding across.
        for iteration in range(2):
            for name, heading in PAGES.items():
                started = time.perf_counter()
                await nav.get_by_text(name, exact=True).click()
                if heading:
                    await page.get_by_role('heading', name=heading, exact=True).wait_for(timeout=60000)
                await settle()
                for page_name, title in (('Task planner', 'Task planning map'), ('Wave watch', 'Wave Watch Map')):
                    await expect(page.get_by_role('heading', name=title, exact=True)).to_have_count(int(name == page_name))
                await expect(frames).to_have_count(0 if name in ('Help Guide', 'Help & Briefing Bot', 'Data quality') else 1)
                if name != 'Weather map':
                    assert not await page.get_by_text('Enable Point Forecast Probe', exact=True).count()
                if iteration:
                    ms = await counter('Fixture server render ms:')
                    result['pages'][name] = {'server_ms': ms, 'browser_ready_ms': round((time.perf_counter() - started) * 1000, 1)}
                    if ms >= 500:
                        result['slow_pages'].append({'page': name, 'server_ms': ms})
        await nav.get_by_text('Weather map', exact=True).click()
        await settle()
        # Day/hour changes slice the same successful 120-hour data bundle.
        before = await counter('Fixture HTTP requests:')
        await page.get_by_role('combobox', name='Forecast day', exact=True).click()
        await page.get_by_role('option', name=re.compile('Day 2 ·')).click()
        await settle()
        after = await counter('Fixture HTTP requests:')
        assert before == after, (before, after)
        result['day_change_request_count'] = after
        await page.get_by_text('Enable Point Forecast Probe', exact=True).click()
        await completed_map()
        await frame().locator('.duo-probe-prompt').wait_for()
        await map_root().evaluate('() => window.map_div._duoProbe.show({lat:52.518,lon:-2.88,name:"Long Mynd"})')
        await frame().locator('.duo-point-panel').wait_for()
        probe = await map_root().evaluate('() => ({ms:window.map_div._duoProbe.lastRenderMs,paint:window.map_div._duoProbe.lastPaintMs,rows:window.map_div._duoProbe.last.rows.length})')
        assert probe['ms'] < 100, probe
        assert probe['rows'] > 0, probe
        result['probe'] = probe
        if args.scenario != 'healthy':
            # Restore the provider, then use the actual user refresh control.
            # A cached failed response would keep these cards empty.
            statistics = await page.get_by_text('Fixture statistics URL:', exact=False).inner_text()
            with urlopen(Request(statistics.split(': ', 1)[1], data=b'healthy', method='POST'), timeout=2):
                pass
            before_refresh = await live_request_count()
            renders_before_refresh = await counter('Fixture full renders:')
            await page.get_by_role('button', name='Refresh weather', exact=True).click()
            await after_full_render(renders_before_refresh)
            assert await page.get_by_text('8/8 local hours covered', exact=False).count() == 5, await page.locator('body').inner_text()
            for label in METRICS:
                value = await page.get_by_test_id('stMetric').filter(has_text=label).get_by_test_id('stMetricValue').inner_text()
                assert re.search(r'\d', value) and '—' not in value, (label, value)
            assert await live_request_count() > before_refresh
            result['explicit_refresh_recovers'] = True
        await page.screenshot(path=str(out / 'weather-probe.png'), full_page=True)
        (out / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))
        assert not result['errors'], result['errors']
        assert not result['slow_pages'], result['slow_pages']
        await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', choices=['healthy', '429', 'timeout', 'offline', 'deferred'], default='healthy')
    parser.add_argument('--url', default='http://127.0.0.1:8508')
    asyncio.run(main(parser.parse_args()))
