/* Keep the Y axis fitted to the data visible after X-axis navigation.
 * A 2D box zoom deliberately keeps the user's selected Y range; panning,
 * range-slider changes and X-only zooms refit the Y range. This uses Plotly's
 * client-side relayout event, with no Dash server round trip or re-render.
 */
(() => {
    "use strict";

    function timestamp(value) {
        if (typeof value === "number") return value;
        if (value instanceof Date) return value.getTime();
        return Date.parse(value);
    }

    function verticalRange(plot) {
        const axis = plot._fullLayout && plot._fullLayout.xaxis;
        if (!axis || !Array.isArray(axis.range)) return null;
        const left = timestamp(axis.range[0]);
        const right = timestamp(axis.range[1]);
        if (!Number.isFinite(left) || !Number.isFinite(right)) return null;
        const minimum = Math.min(left, right);
        const maximum = Math.max(left, right);
        let peak = 0;
        let found = false;
        for (const trace of plot.data || []) {
            if (trace.visible === false || trace.visible === "legendonly") continue;
            if (trace.type !== "scatter" && trace.type !== "bar") continue;
            const xs = trace.x || [];
            const ys = trace.y || [];
            for (let i = 0; i < xs.length && i < ys.length; i += 1) {
                const center = timestamp(xs[i]);
                const value = Number(ys[i]);
                const width = trace.type === "bar" ? Number(
                    Array.isArray(trace.width) ? trace.width[i] : trace.width || 0
                ) : 0;
                const half = Number.isFinite(width) ? width / 2 : 0;
                if (Number.isFinite(center) && Number.isFinite(value)
                    && center + half >= minimum && center - half <= maximum) {
                    found = true;
                    peak = Math.max(peak, value);
                }
            }
        }
        return found ? [0, Math.max(1, peak) * 1.15] : null;
    }

    function xChanged(update) {
        return Object.keys(update || {}).some(key =>
            key === "xaxis.range" || key.startsWith("xaxis.range[")
            || key === "xaxis.autorange"
        );
    }

    function yChanged(update) {
        return Object.keys(update || {}).some(key =>
            key === "yaxis.range" || key.startsWith("yaxis.range[")
        );
    }

    function attach(plot) {
        if (plot.__zooniverseAutoscaleAttached || typeof plot.on !== "function") return;
        plot.__zooniverseAutoscaleAttached = true;
        let updating = false;
        plot.on("plotly_relayout", update => {
            if (updating || !xChanged(update)) return;
            // Respect deliberate vertical selections made via a 2D box zoom.
            if (yChanged(update) && plot._fullLayout.dragmode !== "pan"
                && !update["xaxis.autorange"]) return;
            const next = verticalRange(plot);
            if (!next || !window.Plotly || typeof window.Plotly.relayout !== "function") return;
            const previous = plot._fullLayout.yaxis.range || [];
            if (Math.abs((previous[0] || 0) - next[0]) < 0.001
                && Math.abs((previous[1] || 0) - next[1]) < 0.001) return;
            updating = true;
            Promise.resolve(window.Plotly.relayout(plot, {"yaxis.range": next}))
                .finally(() => { updating = false; });
        });
    }

    const seen = new WeakSet();
    let scheduled = false;
    function scan() {
        scheduled = false;
        for (const plot of document.querySelectorAll(".history-graph .js-plotly-plot")) {
            if (!seen.has(plot) && typeof plot.on === "function") {
                attach(plot);
                seen.add(plot);
            }
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

    // Pure helper exposed for browser tests; no global application state.
    window.ZooniverseChartAutoscale = {verticalRange, xChanged, yChanged};
})();
