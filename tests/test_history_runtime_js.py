"""Exercise the shared browser lifecycle for Plotly history-chart features."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_shared_observer_registers_both_features_once_per_plot():
    """One observer attaches both handlers, including after Dash page navigation."""
    assets = Path(__file__).resolve().parents[1] / "src/zooniverse_dashboard/assets"
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const frames = [];
const callbacks = {};
const plots = [];
let observers = 0;
let observer;
let queries = 0;
const window = {
    requestAnimationFrame: callback => frames.push(callback),
    sessionStorage: {getItem: () => null, setItem: () => {}, removeItem: () => {}},
};
const document = {
    readyState: 'loading',
    documentElement: {},
    addEventListener: (event, callback) => { callbacks[event] = callback; },
    querySelectorAll: selector => {
        assert.equal(selector, '.history-graph .js-plotly-plot');
        queries += 1;
        return plots;
    },
};
class MutationObserver {
    constructor(callback) { this.callback = callback; observer = this; observers += 1; }
    observe() { this.observed = true; }
}
function plot(name) {
    const listeners = new Map();
    return {
        name,
        layout: {meta: {history_zoom: {
            source: name, window: '90', revision: name + '-r1',
            first: '2024-01-01', last_exclusive: '2025-01-01',
            period_bounds: null,
        }}},
        _fullLayout: {xaxis: {range: ['2024-01-01', '2025-01-01']}},
        on: (event, handler) => listeners.set(event, [...(listeners.get(event) || []), handler]),
        listenerCount: event => (listeners.get(event) || []).length,
    };
}
const context = {window, document, MutationObserver, Date, Promise};
for (const file of process.argv.slice(1)) {
    vm.runInNewContext(fs.readFileSync(file, 'utf8'), context);
}
assert.equal(observers, 1);
assert.equal(frames.length, 0);
assert.equal(typeof callbacks.DOMContentLoaded, 'function');
plots.push(plot('users'));
document.readyState = 'complete';
callbacks.DOMContentLoaded();
assert.equal(frames.length, 1);
frames.shift()();
assert.equal(queries, 1);
assert.equal(plots[0].listenerCount('plotly_relayout'), 2);
assert.equal(plots[0].listenerCount('plotly_afterplot'), 1);
// Multiple rapid DOM changes coalesce into a single scan, with no duplicates.
observer.callback();
observer.callback();
assert.equal(frames.length, 1);
frames.shift()();
assert.equal(plots[0].listenerCount('plotly_relayout'), 2);
assert.equal(plots[0].listenerCount('plotly_afterplot'), 1);
// A new plot (Dash page navigation) gets both feature listeners.
plots.push(plot('talks'));
observer.callback();
frames.shift()();
assert.equal(observers, 1);
assert.equal(plots[1].listenerCount('plotly_relayout'), 2);
assert.equal(plots[1].listenerCount('plotly_afterplot'), 1);
"""
    subprocess.run(
        [
            "node",
            "-e",
            script,
            str(assets / "00_history_runtime.js"),
            str(assets / "history_autoscale.js"),
            str(assets / "history_zoom.js"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
