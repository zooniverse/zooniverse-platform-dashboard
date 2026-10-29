/* Keep the selected UTC X-axis window across intervals, chart types, and
 * pages. Plotly's grouped bars have a numeric axis (one slot per period),
 * so persist dates rather than Plotly axis coordinates. Only dates are kept
 * in this tab's sessionStorage; no counts, titles or credentials are stored.
 */
(() => {
    "use strict";

    const STORAGE_KEY = "zooniverse-history-viewport-v1";

    function dateMs(value) {
        if (typeof value === "number") return value;
        if (value instanceof Date) return value.getTime();
        return typeof value === "string" ? Date.parse(value) : NaN;
    }

    function bounds(meta) {
        if (!meta || !Array.isArray(meta.period_bounds) || meta.period_bounds.length < 2) {
            return null;
        }
        const dates = meta.period_bounds.map(dateMs);
        if (!dates.every(Number.isFinite) || !dates.every((value, i) =>
            i === 0 || value > dates[i - 1])) return null;
        return dates;
    }

    // A numeric bar slot [i - 0.5, i + 0.5] spans exactly one UTC
    // calendar period, even for leap years and partial snapshot periods.
    function slotToMs(position, dates) {
        if (!Number.isFinite(position) || !dates) return NaN;
        const n = dates.length - 1;
        const p = Math.max(-0.5, Math.min(n - 0.5, position));
        const index = Math.min(n - 1, Math.max(0, Math.floor(p + 0.5)));
        const fraction = Math.max(0, Math.min(1, p - index + 0.5));
        return dates[index] + fraction * (dates[index + 1] - dates[index]);
    }

    function msToSlot(ms, dates) {
        if (!Number.isFinite(ms) || !dates) return NaN;
        const n = dates.length - 1;
        if (ms <= dates[0]) return -0.5;
        if (ms >= dates[n]) return n - 0.5;
        // Binary search handles full-history daily/weekly views efficiently.
        let lo = 0;
        let hi = n;
        while (lo + 1 < hi) {
            const mid = Math.floor((lo + hi) / 2);
            if (dates[mid] <= ms) lo = mid;
            else hi = mid;
        }
        return lo - 0.5 + (ms - dates[lo]) / (dates[lo + 1] - dates[lo]);
    }

    function axisToDates(range, meta) {
        if (!Array.isArray(range) || range.length !== 2) return null;
        const dates = bounds(meta);
        if (meta && Array.isArray(meta.period_bounds) && !dates) return null;
        const converted = dates
            ? range.map(value => slotToMs(Number(value), dates))
            : range.map(dateMs);
        if (!converted.every(Number.isFinite)) return null;
        const first = dateMs(meta.first);
        const last = dateMs(meta.last_exclusive);
        if (!Number.isFinite(first) || !Number.isFinite(last) || last <= first) return null;
        const left = Math.max(first, Math.min(...converted));
        const right = Math.min(last, Math.max(...converted));
        return right > left ? [left, right] : null;
    }

    function datesToAxis(range, meta) {
        if (!Array.isArray(range) || range.length !== 2
            || !range.every(Number.isFinite) || range[0] >= range[1]) return null;
        const first = dateMs(meta.first);
        const last = dateMs(meta.last_exclusive);
        if (!Number.isFinite(first) || !Number.isFinite(last)) return null;
        const left = Math.max(first, range[0]);
        const right = Math.min(last, range[1]);
        if (right <= left) return null; // No overlap: leave this source at its default view.
        const dates = bounds(meta);
        if (meta.period_bounds && !dates) return null;
        return dates
            ? [msToSlot(left, dates), msToSlot(right, dates)]
            : [new Date(left).toISOString(), new Date(right).toISOString()];
    }

    function readRange() {
        try {
            const value = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY));
            if (Array.isArray(value) && value.length === 2
                && value.every(Number.isFinite) && value[0] < value[1]) return value;
        } catch (_) { /* Browsers may disallow sessionStorage. */ }
        return null;
    }

    function saveRange(range) {
        try { window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(range)); }
        catch (_) { /* No persistence when storage is unavailable. */ }
    }

    function clearRange() {
        try { window.sessionStorage.removeItem(STORAGE_KEY); }
        catch (_) { /* Same fallback as saveRange. */ }
    }

    function xChanged(update) {
        return Object.keys(update || {}).some(key =>
            key === "xaxis.range" || key.startsWith("xaxis.range[")
            || key === "xaxis.autorange"
        );
    }

    function chartMeta(plot) {
        const meta = plot.layout && plot.layout.meta;
        return meta && meta.history_zoom;
    }

    function sync(plot, state) {
        const meta = chartMeta(plot);
        if (!meta || typeof meta.revision !== "string"
            || meta.revision === state.revision) return;
        const previous = state.meta;
        state.revision = meta.revision;
        state.meta = meta;
        // The visible-window dropdown is an explicit new selection. Do not
        // let an old manual zoom unexpectedly override its preset.
        if (previous && previous.source === meta.source && previous.window !== meta.window) {
            clearRange();
            return;
        }
        // Anomaly deep links take precedence over an old shared zoom. Their
        // server-selected window becomes the new shared window, so subsequent
        // grouping changes and navigation stay on the selected episode.
        if (meta.focused) {
            const axis = plot._fullLayout && plot._fullLayout.xaxis;
            const focusedRange = axis && axisToDates(axis.range, meta);
            if (focusedRange) saveRange(focusedRange);
            return;
        }
        const saved = readRange();
        const next = saved && datesToAxis(saved, meta);
        if (!next || !window.Plotly || typeof window.Plotly.relayout !== "function") return;
        const current = plot._fullLayout && plot._fullLayout.xaxis
            && plot._fullLayout.xaxis.range;
        if (current && next.every((value, i) => {
            const a = meta.period_bounds ? Number(value) : dateMs(value);
            const b = meta.period_bounds ? Number(current[i]) : dateMs(current[i]);
            return Number.isFinite(b) && Math.abs(a - b) < (meta.period_bounds ? 1e-8 : 1000);
        })) return;
        state.applying = true;
        try {
            Promise.resolve(window.Plotly.relayout(plot, {"xaxis.range": next}))
                .finally(() => { state.applying = false; });
        } catch (_) {
            state.applying = false;
        }
    }

    function attach(plot) {
        if (plot.__zooniverseZoomAttached || typeof plot.on !== "function") return;
        plot.__zooniverseZoomAttached = true;
        const state = {meta: null, revision: null, applying: false};
        plot.on("plotly_relayout", update => {
            if (state.applying || !xChanged(update)) return;
            const meta = chartMeta(plot);
            // Ignore transient Plotly updates while Dash is replacing a figure.
            if (!meta || meta.revision !== state.revision) return;
            if (update["xaxis.autorange"]) {
                clearRange();
                return;
            }
            const axis = plot._fullLayout && plot._fullLayout.xaxis;
            const range = axis && axisToDates(axis.range, meta);
            if (range) saveRange(range);
        });
        plot.on("plotly_afterplot", () => sync(plot, state));
        sync(plot, state);
    }

    let scheduled = false;
    function scan() {
        scheduled = false;
        for (const plot of document.querySelectorAll(".history-graph .js-plotly-plot")) {
            attach(plot);
        }
    }
    function schedule() {
        if (scheduled) return;
        scheduled = true;
        window.requestAnimationFrame(scan);
    }
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", schedule, {once: true});
    } else {
        schedule();
    }
    new MutationObserver(schedule).observe(document.documentElement, {
        childList: true, subtree: true
    });

    // Pure conversion helpers are exported for Node-based regression tests.
    window.ZooniverseChartZoom = {axisToDates, datesToAxis, slotToMs, msToSlot, attach};
})();
