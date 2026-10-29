"""Browser tick-density and visible-Y autoscale smoke tests (if Node is available)."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_indexed_calendar_axes_keep_readable_ticks_and_y_autoscale():
    asset = (
        Path(__file__).resolve().parents[1] / "src/zooniverse_dashboard/assets/history_autoscale.js"
    )
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const window = { requestAnimationFrame: () => {} };
const document = {
    readyState: 'loading',
    addEventListener: () => {},
    documentElement: {},
};
class MutationObserver { observe() {} }
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
    window, document, MutationObserver, Date, Promise,
});
const {periodTickUpdate, verticalRange} = window.ZooniverseChartAutoscale;
const labels = Array.from({length: 60}, (_, i) => `Period ${i}`);
const plot = {
    layout: { meta: {period_labels: labels}, xaxis: {tickvals: [], ticktext: []} },
    _fullLayout: { xaxis: {range: [-0.5, 11.5], _length: 425} },
    data: [{type: 'bar', x: [4, 5, 6], y: [20, 90, 40], width: [0.82, 0.82, 0.82]},
           {type: 'scatter', x: [5.25], y: [95]}],
};
const initial = periodTickUpdate(plot);
assert.deepEqual(Array.from(initial['xaxis.tickvals']), [0, 3, 6, 9]);
assert.deepEqual(Array.from(initial['xaxis.ticktext']),
    ['Period 0', 'Period 3', 'Period 6', 'Period 9']);
plot._fullLayout.xaxis.range = [4.5, 6.5];
assert.deepEqual(Array.from(periodTickUpdate(plot)['xaxis.tickvals']), [5, 6]);
assert.deepEqual(Array.from(verticalRange(plot)), [0, 95 * 1.15]);
plot._fullLayout.xaxis.range = [5.1, 5.3];
assert.deepEqual(Array.from(periodTickUpdate(plot)['xaxis.tickvals']), [5]);
assert.deepEqual(Array.from(verticalRange(plot)), [0, 95 * 1.15]);
const noMeta = {layout: {}, _fullLayout: plot._fullLayout};
assert.equal(periodTickUpdate(noMeta), null);
"""
    subprocess.run(["node", "-e", script, str(asset)], check=True, capture_output=True, text=True)
