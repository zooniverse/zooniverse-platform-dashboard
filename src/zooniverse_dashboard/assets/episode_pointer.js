/* Display a clickable cursor directly over anomaly diamonds in Plotly charts.
 *
 * Plotly's transparent drag rectangles sit above the actual SVG markers and
 * determine the visible cursor. Moreover, hoverData may identify the daily
 * line rather than the anomaly diamond when both occupy the same coordinates.
 * We therefore test pointer proximity to the marker's rendered SVG geometry.
 *
 * Delegated pointer events survive dcc.Graph redraws and Dash page changes.
 * No server callbacks, plotting changes, timers or network requests are needed.
 */
(() => {
    "use strict";

    const EPISODE_ID = /^(users|classifications|talks)-\d{4}-\d{2}-\d{2}-(spike|drop)$/;
    const ACTIVE_CLASS = "episode-marker-hover";
    const HIT_PADDING = 3;
    let activePlot = null;

    function setActivePlot(nextPlot) {
        if (activePlot === nextPlot) return;
        if (activePlot) activePlot.classList.remove(ACTIVE_CLASS);
        if (nextPlot) nextPlot.classList.add(ACTIVE_CLASS);
        activePlot = nextPlot;
    }

    function inside(clientX, clientY, rect, padding = 0) {
        return clientX >= rect.left - padding && clientX <= rect.right + padding
            && clientY >= rect.top - padding && clientY <= rect.bottom + padding;
    }

    function overEpisodeDiamond(plot, event) {
        const dragRect = plot.querySelector(".draglayer rect.nsewdrag[data-subplot='xy']");
        if (!dragRect || !inside(event.clientX, event.clientY, dragRect.getBoundingClientRect())) {
            return false;
        }

        const data = plot.data;
        if (!Array.isArray(data)) return false;
        const traces = plot.querySelectorAll(".scatterlayer .trace");
        for (let i = 0; i < data.length; i += 1) {
            const metadata = data[i] && data[i].customdata;
            if (!Array.isArray(metadata) || !metadata.length) continue;
            if (!Array.isArray(metadata[0]) || !EPISODE_ID.test(metadata[0][0])) continue;

            // Both daily-count traces come before these marker-only traces.
            const markers = traces[i] && traces[i].querySelectorAll("path.point");
            if (!markers) continue;
            for (let j = 0; j < markers.length; j += 1) {
                if (!Array.isArray(metadata[j]) || !EPISODE_ID.test(metadata[j][0])) continue;
                if (inside(event.clientX, event.clientY,
                    markers[j].getBoundingClientRect(), HIT_PADDING)) return true;
            }
        }
        return false;
    }

    document.addEventListener("pointermove", (event) => {
        const chart = event.target.closest && event.target.closest(".history-graph");
        const plot = chart && chart.querySelector(".js-plotly-plot");
        setActivePlot(event.buttons === 0 && plot && overEpisodeDiamond(plot, event)
            ? plot : null);
    }, true);

    document.addEventListener("pointerdown", () => setActivePlot(null), true);
    window.addEventListener("blur", () => setActivePlot(null));
})();
