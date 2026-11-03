/* Global activity chart preferences managed through Dash callbacks.
 * The app's dcc.Store persists across Pages navigation in this browser tab.
 * Dash, rather than DOM mutation observers, owns the RadioItems values.
 */
window.dash_clientside = Object.assign({}, window.dash_clientside, {
    zooniverse_chart: {
        restore: function (page, saved, currentInterval, currentPlot) {
            const noop = window.dash_clientside.no_update;
            if (!page || !Array.isArray(page.intervals)
                || !Array.isArray(page.plot_types)) {
                return [noop, noop, noop];
            }
            const desiredInterval = saved && saved.interval;
            const desiredPlot = saved && saved.plot_type;
            const interval = page.intervals.includes(desiredInterval)
                ? desiredInterval
                : page.intervals.includes(currentInterval)
                    ? currentInterval : page.default_interval;
            const plot = page.plot_types.includes(desiredPlot)
                ? desiredPlot
                : page.plot_types.includes(currentPlot)
                    ? currentPlot : page.default_plot_type;
            // Projects cannot display Day/Week. Treat Month as a visual
            // fallback, *not* an explicit replacement of the saved preference.
            const fallback = Boolean(saved && desiredInterval
                && !page.intervals.includes(desiredInterval));
            return [
                interval === currentInterval ? noop : interval,
                plot === currentPlot ? noop : plot,
                {
                    source: page.source,
                    fallback: fallback,
                    initial_interval: interval,
                    initial_plot_type: plot,
                },
            ];
        },
        save: function (interval, plot, page, ready, saved) {
            const noop = window.dash_clientside.no_update;
            if (!page || !ready || ready.source !== page.source
                || !page.intervals.includes(interval)
                || !page.plot_types.includes(plot)) return noop;
            // Mounting a page is not a user choice. Without a previously
            // stored preference, ignore the initial control values.
            if (!saved && interval === ready.initial_interval
                && plot === ready.initial_plot_type) return noop;
            const next = {
                interval: interval,
                plot_type: plot,
            };
            // Restoring Month on Projects must not erase Day/Week from the
            // activity pages. A subsequent explicit selection of Year does.
            if (ready.fallback && interval === page.default_interval
                && saved && typeof saved.interval === "string"
                && !page.intervals.includes(saved.interval)) {
                next.interval = saved.interval;
            }
            if (saved && saved.interval === next.interval
                && saved.plot_type === next.plot_type) return noop;
            return next;
        },
    },
});
