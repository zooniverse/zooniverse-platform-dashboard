/* Shared Plotly attachment lifecycle for dashboard history charts.
 * Dash replaces graphs during navigation. One DOM observer and scan attaches
 * both independent chart features to each graph, with no remote requests.
 * Dash loads this 00_ asset before history_autoscale.js and history_zoom.js.
 */
(() => {
    "use strict";

    const attachments = [];
    let scheduled = false;
    let observing = false;

    function xChanged(update) {
        return Object.keys(update || {}).some(key =>
            key === "xaxis.range" || key.startsWith("xaxis.range[")
            || key === "xaxis.autorange"
        );
    }

    function scan() {
        scheduled = false;
        for (const plot of document.querySelectorAll(".history-graph .js-plotly-plot")) {
            for (const attach of attachments) attach(plot);
        }
    }

    function schedule() {
        if (scheduled) return;
        scheduled = true;
        window.requestAnimationFrame(scan);
    }

    function register(attach) {
        attachments.push(attach);
        if (!observing) {
            observing = true;
            if (document.readyState === "loading") {
                document.addEventListener("DOMContentLoaded", schedule, {once: true});
            }
            new MutationObserver(schedule).observe(document.documentElement, {
                childList: true, subtree: true
            });
        }
        // Covers late registration; each feature's attach() is idempotent.
        if (document.readyState !== "loading") schedule();
    }

    window.ZooniverseChartRuntime = {register, xChanged};
})();
