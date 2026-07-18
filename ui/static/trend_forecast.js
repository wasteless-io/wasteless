/**
 * Pure forecast math for the dashboard's Waste Trend & Forecast chart.
 *
 * Extracted from the inline script of templates/dashboard.html so the
 * regression can be unit-tested with `node --test` (ui/tests/js/). The
 * template keeps everything DOM/Chart.js: this module only turns a series
 * of numbers into a forecast.
 *
 * Loaded both in the browser (window.trendForecast) and in Node
 * (module.exports), hence the UMD-style wrapper.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.trendForecast = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    // Honest minimum for a regression, forecast horizon and half-life per
    // granularity. Half-life in points, not days/months, since granularity
    // varies.
    var PARAMS = {
        day: { minPoints: 7, horizon: 30, halfLife: 10 },
        month: { minPoints: 4, horizon: 6, halfLife: 2 }
    };

    /**
     * Weighted linear-regression forecast over `raw` (oldest first).
     *
     * Recent points count more (exponential weights with the granularity's
     * half-life), so a real structural break (e.g. a batch of waste
     * dismissed) shows up in the forecast within a couple of points instead
     * of being diluted across the whole window by a long flat history
     * before it.
     *
     * The forecast is anchored on the last actual value, not the fitted
     * line's absolute position: the regression line minimizes error over
     * the whole window, it doesn't pass through the last point, so using
     * the intercept directly could jump away from where the data actually
     * left off. Only the slope (the trend's direction and rate) comes from
     * the regression. Waste can't go negative, so points clamp at 0.
     *
     * A single step dominating the window is an event, not a trend: a
     * batch of resources appearing (or being cleaned up) in one day is a
     * regime change, and fitting a line through it projects the one-off
     * as a permanent growth rate. When one jump exceeds half the window's
     * total range, the slope is estimated from the points AFTER the jump
     * only; fewer than 3 of those means no established trend yet, so the
     * forecast stays flat at the current level and `stepBreak` is true
     * (the chart surfaces it in the subtitle).
     *
     * Returns { minPoints, horizon, slope, stepBreak, points }:
     *  - points: array of `horizon` forecast values, or null when raw has
     *    fewer than minPoints entries (no honest trend to draw);
     *  - slope: per-step trend, or null alongside a null points;
     *  - stepBreak: true when a dominant step forced a flat forecast.
     */
    function fitSlope(seg, halfLife) {
        var n = seg.length;
        var sw = 0, swx = 0, swy = 0, swxy = 0, swxx = 0;
        for (var i = 0; i < n; i++) {
            var w = Math.pow(0.5, (n - 1 - i) / halfLife);
            sw += w; swx += w * i; swy += w * seg[i]; swxy += w * i * seg[i]; swxx += w * i * i;
        }
        return (sw * swxy - swx * swy) / (sw * swxx - swx * swx);
    }

    function computeForecast(raw, granularity) {
        var p = PARAMS[granularity] || PARAMS.day;
        var n = raw.length;
        if (n < p.minPoints) {
            return {
                minPoints: p.minPoints, horizon: p.horizon,
                slope: null, stepBreak: false, points: null
            };
        }

        var min = raw[0], max = raw[0], jump = 0, jumpAt = 0;
        for (var i = 1; i < n; i++) {
            if (raw[i] < min) min = raw[i];
            if (raw[i] > max) max = raw[i];
            var d = Math.abs(raw[i] - raw[i - 1]);
            if (d > jump) { jump = d; jumpAt = i; }
        }

        var slope;
        var stepBreak = false;
        if (max - min > 0 && jump > 0.5 * (max - min)) {
            var seg = raw.slice(jumpAt);
            if (seg.length >= 3) {
                slope = fitSlope(seg, p.halfLife);
            } else {
                slope = 0;
                stepBreak = true;
            }
        } else {
            slope = fitSlope(raw, p.halfLife);
        }

        var points = [];
        for (var k = 1; k <= p.horizon; k++) {
            points.push(Math.max(0, raw[n - 1] + slope * k));
        }
        return {
            minPoints: p.minPoints, horizon: p.horizon,
            slope: slope, stepBreak: stepBreak, points: points
        };
    }

    return { computeForecast: computeForecast, PARAMS: PARAMS };
});
