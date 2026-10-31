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

    // Indexed calendar bars carry their labels in layout.meta, while the
    // x axis itself is numeric. Limit ticks to the available plot width, and
    // update them on pan/zoom so labels remain useful at every zoom level.
    function periodTickUpdate(plot) {
        const labels = plot.layout && plot.layout.meta && plot.layout.meta.period_labels;
        const axis = plot._fullLayout && plot._fullLayout.xaxis;
        if (!Array.isArray(labels) || !labels.length || !axis || !Array.isArray(axis.range)) {
            return null;
        }
        const left = Number(axis.range[0]);
        const right = Number(axis.range[1]);
        if (!Number.isFinite(left) || !Number.isFinite(right)) return null;
        const first = Math.max(0, Math.ceil(Math.min(left, right)));
        const last = Math.min(labels.length - 1, Math.floor(Math.max(left, right)));
        const width = Number(axis._length) || plot.clientWidth || 800;
        const count = Math.max(2, Math.floor(width / 85));
        const stride = Math.max(1, Math.ceil(Math.max(1, last - first + 1) / count));
        const indexes = [];
        if (first <= last) {
            for (let i = Math.ceil(first / stride) * stride; i <= last; i += stride) {
                indexes.push(i);
            }
        } else {
            // A zoom strictly inside one bar still needs a period label.
            indexes.push(Math.min(labels.length - 1, Math.max(0, Math.round((left + right) / 2))));
        }
        const values = indexes.map(i => labels[i]);
        const existingVals = plot.layout.xaxis && plot.layout.xaxis.tickvals || [];
        const existingText = plot.layout.xaxis && plot.layout.xaxis.ticktext || [];
        if (indexes.length === existingVals.length && indexes.every((x, i) =>
            x === existingVals[i] && values[i] === existingText[i])) return null;
        return {"xaxis.tickvals": indexes, "xaxis.ticktext": values};
    }

    const {register, xChanged} = window.ZooniverseChartRuntime;

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
            const changes = periodTickUpdate(plot) || {};
            // Respect deliberate vertical selections made via a 2D box zoom.
            if (!yChanged(update) || plot._fullLayout.dragmode === "pan"
                || update["xaxis.autorange"]) {
                const next = verticalRange(plot);
                const previous = plot._fullLayout.yaxis.range || [];
                if (next && (Math.abs((previous[0] || 0) - next[0]) >= 0.001
                    || Math.abs((previous[1] || 0) - next[1]) >= 0.001)) {
                    changes["yaxis.range"] = next;
                }
            }
            if (!Object.keys(changes).length || !window.Plotly
                || typeof window.Plotly.relayout !== "function") return;
            updating = true;
            Promise.resolve(window.Plotly.relayout(plot, changes))
                .finally(() => { updating = false; });
        });
        // The initial layout is already sparsely labelled server-side; adapt
        // it to the real plot width once the browser has rendered the chart.
        const ticks = periodTickUpdate(plot);
        if (ticks && window.Plotly && typeof window.Plotly.relayout === "function") {
            updating = true;
            Promise.resolve(window.Plotly.relayout(plot, ticks))
                .finally(() => { updating = false; });
        }
    }

    register(attach);

    // Pure helper exposed for browser tests; no global application state.
    window.ZooniverseChartAutoscale = {verticalRange, periodTickUpdate, xChanged, yChanged};
})();
