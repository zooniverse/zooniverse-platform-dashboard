"""Node integration tests for session-scoped UTC chart viewport persistence."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_zoom_persists_across_grouped_bars_pages_and_reset():
    asset = Path(__file__).resolve().parents[1] / "src/zooniverse_dashboard/assets/history_zoom.js"
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const values = new Map();
const storage = {
    getItem: key => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
};
const applied = [];
const window = {sessionStorage: storage, requestAnimationFrame: () => {}};
const document = {readyState: 'loading', addEventListener: () => {}, documentElement: {}};
class MutationObserver { observe() {} }
window.Plotly = {
    relayout: (plot, update) => {
        const x = Array.from(update['xaxis.range']);
        applied.push([plot.name, x]);
        plot._fullLayout.xaxis.range = x;
        plot.emit('plotly_relayout', {'xaxis.range': x});
        plot.emit('plotly_afterplot');
        return Promise.resolve();
    },
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
    window, document, MutationObserver, Date, Promise,
});
const z = window.ZooniverseChartZoom;
const ms = value => Date.parse(value);
const between = (a, b) => Math.abs(a - b) < 1e-7;
const daily = (source, revision, dateWindow='90', first='2024-01-01',
               last='2024-12-31', focused=false) => ({
    source, window: dateWindow, revision, interval: 'daily', plot_type: 'line',
    first, last_exclusive: last, focused, period_bounds: null,
});
const indexed = (source, revision, interval, boundaries, dateWindow='90') => ({
    source, window: dateWindow, revision, interval, plot_type: 'bars',
    first: '2024-01-01', last_exclusive: '2025-01-01', focused: false,
    period_bounds: boundaries,
});
function plot(name, meta, initial) {
    const events = {};
    return {
        name,
        layout: {meta: {history_zoom: meta}},
        _fullLayout: {xaxis: {range: initial}},
        on: (key, cb) => {events[key] = cb},
        emit: (key, arg) => {if (events[key]) events[key](arg)},
    };
}
(async () => {
const first = plot('users', daily('users', 'users-daily'), ['2024-01-01', '2024-12-31']);
z.attach(first);
assert.equal(applied.length, 0);
// Two-axis box zoom stores ONLY the horizontal date range.
first._fullLayout.xaxis.range = ['2024-02-10', '2024-02-22'];
first.emit('plotly_relayout', {'xaxis.range[0]': '2024-02-10',
                              'xaxis.range[1]': '2024-02-22',
                              'yaxis.range[0]': 100, 'yaxis.range[1]': 250});
assert.deepEqual(JSON.parse(values.get('zooniverse-history-viewport-v1')),
    [ms('2024-02-10'), ms('2024-02-22')]);
const months = ['2024-01-01', '2024-02-01', '2024-03-01', '2024-04-01'];
first.layout.meta.history_zoom = indexed('users', 'users-months', 'monthly', months);
first._fullLayout.xaxis.range = [-0.5, 2.5];
first.emit('plotly_afterplot');
await Promise.resolve();
assert.equal(applied.length, 1);
const mapped = applied[0][1];
assert(between(mapped[0], 0.5 + 9 / 29));
assert(between(mapped[1], 0.5 + 21 / 29));
assert.deepEqual(Array.from(z.axisToDates(mapped, first.layout.meta.history_zoom)),
    [ms('2024-02-10'), ms('2024-02-22')]);
// Plotly relayout for restoring a graph must not rewrite the original dates.
assert.deepEqual(JSON.parse(values.get('zooniverse-history-viewport-v1')),
    [ms('2024-02-10'), ms('2024-02-22')]);
// Navigating to a different source keeps exactly the same date window.
const talk = plot('talks', daily('talks', 'talks-daily'), ['2024-01-01', '2024-12-31']);
z.attach(talk);
await Promise.resolve();
assert.equal(applied.length, 2);
assert.deepEqual(applied[1], ['talks',
    ['2024-02-10T00:00:00.000Z', '2024-02-22T00:00:00.000Z']]);
// Changing to weekly bars preserves the window even across ISO year boundary.
const weeks = ['2024-01-01', '2024-01-08', '2024-01-15', '2024-01-22',
    '2024-01-29', '2024-02-05', '2024-02-12', '2024-02-19', '2024-02-26'];
talk.layout.meta.history_zoom = indexed('talks', 'talks-weekly', 'weekly', weeks);
talk._fullLayout.xaxis.range = [-0.5, 7.5];
talk.emit('plotly_afterplot');
await Promise.resolve();
assert.equal(applied.length, 3);
assert.deepEqual(Array.from(z.axisToDates(applied[2][1], talk.layout.meta.history_zoom)),
    [ms('2024-02-10'), ms('2024-02-22')]);
// A less-complete source clips its display without corrupting shared state.
const short = plot('classifications', daily('classifications', 'class-short', '90',
    '2024-02-15', '2024-03-01'), ['2024-02-15', '2024-03-01']);
z.attach(short);
await Promise.resolve();
assert.deepEqual(applied[3][1],
    ['2024-02-15T00:00:00.000Z', '2024-02-22T00:00:00.000Z']);
assert.deepEqual(JSON.parse(values.get('zooniverse-history-viewport-v1')),
    [ms('2024-02-10'), ms('2024-02-22')]);
// A source with no overlapping data is not forced to an invalid viewport.
const distant = plot('distant', daily('distant', 'distant-1', '90',
    '2025-01-01', '2025-02-01'), ['2025-01-01', '2025-02-01']);
z.attach(distant);
assert.equal(applied.length, 4);
// Preset dropdown deliberately clears the manual shared zoom.
talk.layout.meta.history_zoom = indexed('talks', 'talks-all', 'weekly', weeks, 'all');
talk.emit('plotly_afterplot');
assert.equal(values.has('zooniverse-history-viewport-v1'), false);
// New zoom followed by Plotly Reset axes clears shared state too.
first.layout.meta.history_zoom = daily('users', 'users-again');
first.emit('plotly_afterplot');
first._fullLayout.xaxis.range = ['2024-03-04', '2024-03-07'];
first.emit('plotly_relayout', {'xaxis.range': ['2024-03-04', '2024-03-07']});
assert.equal(values.has('zooniverse-history-viewport-v1'), true);
first.emit('plotly_relayout', {'xaxis.autorange': true});
assert.equal(values.has('zooniverse-history-viewport-v1'), false);
// Anomaly deep links are intentionally not overridden.
values.set('zooniverse-history-viewport-v1', JSON.stringify([ms('2024-02-10'), ms('2024-02-22')]));
const focus = plot('focus', daily('users', 'focus-episode', '90',
    '2024-01-01', '2025-01-01', true), ['2024-04-01', '2024-04-30']);
z.attach(focus);
assert.equal(applied.length, 4);
assert.deepEqual(JSON.parse(values.get('zooniverse-history-viewport-v1')),
    [ms('2024-04-01'), ms('2024-04-30')]);
// Yearly conversion uses year lengths, including 366-day leap years.
const year = indexed('users', 'year', 'yearly',
    ['2023-01-01', '2024-01-01', '2025-01-01']);
assert(between(z.msToSlot(ms('2024-07-02'), year.period_bounds.map(ms)),
    0.5 + 183 / 366));
// Direct date X axes survive sub-day pans, not just whole-day zooms.
const precise = [ms('2024-06-01T12:00:00Z'), ms('2024-06-04T06:00:00Z')];
assert.deepEqual(Array.from(z.axisToDates(z.datesToAxis(precise,
    daily('users', 'precision')), daily('users', 'precision'))), precise);
})().catch(err => { console.error(err); process.exitCode = 1; });
"""
    subprocess.run(["node", "-e", script, str(asset)], check=True, capture_output=True, text=True)
