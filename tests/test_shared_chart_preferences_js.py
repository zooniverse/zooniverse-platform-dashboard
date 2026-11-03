"""Exercise Dash-managed chart preferences without relying on DOM timing."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_cross_page_chart_preferences_with_projects_fallback():
    """Test hydration, user changes, and unsupported Projects intervals."""
    path = (
        Path(__file__).resolve().parents[1] / "src/zooniverse_dashboard/assets/chart_preferences.js"
    )
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const noop = Symbol('no_update');
const window = {dash_clientside: {no_update: noop}};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {window});
const {restore, save} = window.dash_clientside.zooniverse_chart;
const activity = source => ({
    source, intervals: ['daily','weekly','monthly','yearly'],
    plot_types: ['line','bars'], default_interval: 'daily', default_plot_type: 'line',
});
const projects = {
    source: 'projects', intervals: ['monthly','yearly'],
    plot_types: ['line','bars'], default_interval: 'monthly', default_plot_type: 'bars',
};
const user = activity('users');
const fresh = restore(user, null, 'daily', 'line');
assert.equal(fresh[0], noop);
assert.equal(fresh[1], noop);
assert.equal(fresh[2].source, 'users');
assert.equal(save('daily','line',user,null,null), noop); // pre-hydration
assert.equal(save('daily','line',user,fresh[2],null),noop); // page mount is not a choice
const weekBars = save('weekly','bars',user,fresh[2],null);
assert.equal(weekBars.interval, 'weekly');
assert.equal(weekBars.plot_type, 'bars');

// A new page first renders its own defaults; hydration must restore the
// saved value, without a DOM observer or Plotly having mounted.
const classifications = activity('classifications');
const hydrated = restore(classifications,weekBars,'daily','line');
assert.equal(hydrated[0], 'weekly');
assert.equal(hydrated[1], 'bars');
assert.equal(hydrated[2].source, 'classifications');
assert.equal(save('daily','line',classifications,fresh[2],weekBars), noop);
assert.equal(save('weekly','bars',classifications,hydrated[2],weekBars),noop);

// Projects displays Month as a fallback without overwriting saved Week.
const proj = restore(projects,weekBars,'monthly','bars');
assert.equal(proj[0],noop);
assert.equal(proj[1],noop);
assert.equal(proj[2].fallback,true);
assert.equal(save('monthly','bars',projects,restore(projects,null,'monthly','bars')[2],null),noop);
assert.equal(save('monthly','bars',projects,proj[2],weekBars),noop);
assert.equal(save('monthly','line',projects,proj[2],weekBars).interval,'weekly');
const yearBars = save('yearly','bars',projects,proj[2],weekBars);
assert.equal(yearBars.interval,'yearly');
const talks = activity('talks');
assert.equal(restore(talks,yearBars,'daily','line')[0],'yearly');
assert.equal(restore(talks,yearBars,'daily','line')[1],'bars');

// Reopening Projects from a stored supported value must use that value.
const projYear = restore(projects,yearBars,'monthly','line');
assert.equal(projYear[0],'yearly');
assert.equal(projYear[1],'bars');
assert.equal(projYear[2].fallback,false);
assert.equal(save('monthly','line',projects,projYear[2],yearBars).interval,'monthly');
// The store (not values inherited from previously mounted components) is
// authoritative: missing or invalid stored choices restore page defaults.
const invalid = restore(user,{interval:'invalid',plot_type:'invalid'},'weekly','bars');
assert.equal(invalid[0],'daily');
assert.equal(invalid[1],'line');
const absent = restore(projects,null,'yearly','line');
assert.equal(absent[0],'monthly');
assert.equal(absent[1],'bars');
// Restoration also works when the components already have the desired values.
const same = restore(talks,weekBars,'weekly','bars');
assert.equal(same[0],noop);
assert.equal(same[1],noop);
assert.equal(save('daily','line',classifications,null,weekBars),noop);
// Invalid stored values are never preserved as a Projects fallback.
const invalidProjects = restore(projects,{interval:'invalid',plot_type:'invalid'},'monthly','bars');
assert.equal(invalidProjects[2].fallback,false);
const repaired = save('monthly','line',projects,invalidProjects[2],
    {interval:'invalid',plot_type:'invalid'});
assert.equal(repaired.interval,'monthly');
assert.equal(repaired.plot_type,'line');
"""
    subprocess.run(
        ["node", "-e", script, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
