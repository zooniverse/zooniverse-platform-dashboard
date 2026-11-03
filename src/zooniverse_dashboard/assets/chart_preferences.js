/* Global chart preferences managed through Dash callbacks.
 * The app-level session dcc.Store is the only persisted preference state.
 * RadioItems are transient: restoring a page derives their values from the
 * Store (or that page's defaults), not from a prior component instance.
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
                ? desiredInterval : page.default_interval;
            const plot = page.plot_types.includes(desiredPlot)
                ? desiredPlot : page.default_plot_type;
            // Projects cannot display Day/Week. Treat Month as a visual
            // fallback, *not* an explicit replacement of the saved preference.
            const fallback = page.source === "projects"
                && (desiredInterval === "daily" || desiredInterval === "weekly");
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
