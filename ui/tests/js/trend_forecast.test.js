'use strict';
/**
 * Unit tests for the pure forecast math behind the dashboard's
 * "Waste Trend & Forecast" chart. Run with:
 *
 *   node --test ui/tests/js
 *
 * (also wrapped by ui/tests/test_trend_forecast_js.py so it runs with the
 * regular Python suite whenever node is installed).
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { computeForecast, PARAMS } = require(
    path.join(__dirname, '..', '..', 'static', 'trend_forecast.js')
);

// Plain unweighted least-squares slope, for comparison in the
// structural-break test.
function unweightedSlope(raw) {
    const n = raw.length;
    let sx = 0, sy = 0, sxy = 0, sxx = 0;
    for (let i = 0; i < n; i++) {
        sx += i; sy += raw[i]; sxy += i * raw[i]; sxx += i * i;
    }
    return (n * sxy - sx * sy) / (n * sxx - sx * sx);
}

test('recovers the exact slope of a clean linear series', () => {
    const raw = Array.from({ length: 30 }, (_, i) => 100 + 2 * i);
    const fc = computeForecast(raw, 'day');
    // Weights change nothing on a perfect line: every subset agrees on it
    assert.ok(Math.abs(fc.slope - 2) < 1e-9, `slope ${fc.slope} should be 2`);
    assert.ok(Math.abs(fc.points[0] - (raw[29] + 2)) < 1e-9);
    assert.ok(Math.abs(fc.points[29] - (raw[29] + 2 * 30)) < 1e-9);
});

test('a recent structural break dominates a long flat history', () => {
    // 30 flat days, then a steep 10-day decline: the whole point of the
    // half-life weighting is that the forecast follows the recent break
    // instead of averaging it away.
    const raw = Array(30).fill(100).concat(
        Array.from({ length: 10 }, (_, i) => 100 - 9 * (i + 1))
    );
    const fc = computeForecast(raw, 'day');
    const naive = unweightedSlope(raw);
    assert.ok(fc.slope < 0, 'trend must point down');
    // The weighting makes the break at least 50% steeper than a plain
    // regression would see it (measured: -2.68 weighted vs -1.53 naive; the
    // half-life of 10 points still lets the flat history damp the slope, so
    // it never reaches the raw -9/day of the last segment).
    assert.ok(
        fc.slope < naive * 1.5,
        `weighted slope (${fc.slope.toFixed(2)}) must be at least 50% steeper ` +
        `than the unweighted one (${naive.toFixed(2)})`
    );
});

test('forecast is anchored on the last actual value, not the fitted intercept', () => {
    // Long flat history at 100, sudden last value of 50: the fitted line
    // still sits far above 50 at the window's edge, but the forecast must
    // start from where the data actually left off.
    const raw = Array(29).fill(100).concat([50]);
    const fc = computeForecast(raw, 'day');
    assert.ok(
        Math.abs(fc.points[0] - (50 + fc.slope)) < 1e-9,
        `first forecast point (${fc.points[0].toFixed(2)}) must equal last actual + slope`
    );
    assert.ok(fc.points[0] < 60, 'forecast must not jump back toward the old level');
});

test('forecast never goes below zero', () => {
    const raw = Array.from({ length: 14 }, (_, i) => 50 - 10 * i); // deep dive
    const fc = computeForecast(raw, 'day');
    assert.ok(fc.points.every((v) => v >= 0));
    assert.equal(fc.points[fc.points.length - 1], 0, 'a steep decline must clamp at 0');
});

test('horizon and minimum points depend on granularity', () => {
    const daily = computeForecast(Array(10).fill(5), 'day');
    assert.equal(daily.points.length, 30);
    const monthly = computeForecast(Array(10).fill(5), 'month');
    assert.equal(monthly.points.length, 6);
    assert.equal(PARAMS.day.minPoints, 7);
    assert.equal(PARAMS.month.minPoints, 4);
});

test('too little history yields no forecast, with the threshold exposed', () => {
    const daily = computeForecast([1, 2, 3, 4, 5, 6], 'day'); // 6 < 7
    assert.equal(daily.points, null);
    assert.equal(daily.slope, null);
    assert.equal(daily.minPoints, 7);

    const monthly = computeForecast([1, 2, 3], 'month'); // 3 < 4
    assert.equal(monthly.points, null);
    assert.equal(monthly.minPoints, 4);
});

test('unknown granularity falls back to daily parameters', () => {
    const fc = computeForecast(Array(10).fill(5), 'fortnight');
    assert.equal(fc.points.length, PARAMS.day.horizon);
});

test('a single dominant step yields a flat forecast, flagged as stepBreak', () => {
    // Six quiet days then a batch of resources lands: an event, not a
    // trend. Projecting its slope would claim +$10/day forever.
    const raw = [2, 2, 2, 2, 2, 2, 77];
    const fc = computeForecast(raw, 'day');
    assert.equal(fc.stepBreak, true);
    assert.equal(fc.slope, 0);
    assert.ok(fc.points.every((v) => v === 77), 'forecast must stay flat at the new level');
});

test('three stable points after the step resume a (flat) fit without the flag', () => {
    const raw = [2, 2, 2, 2, 77, 77, 77];
    const fc = computeForecast(raw, 'day');
    assert.equal(fc.stepBreak, false);
    assert.ok(Math.abs(fc.slope) < 1e-9);
    assert.ok(fc.points.every((v) => Math.abs(v - 77) < 1e-9));
});

test('a real post-step trend is fitted from post-step points only', () => {
    // The step must not leak into the slope: only the 77→80→83 segment
    // (a clean +3/day) drives the forecast.
    const raw = [2, 2, 2, 2, 77, 80, 83];
    const fc = computeForecast(raw, 'day');
    assert.equal(fc.stepBreak, false);
    assert.ok(Math.abs(fc.slope - 3) < 1e-9, `slope ${fc.slope} should be 3`);
    assert.ok(Math.abs(fc.points[0] - 86) < 1e-9);
});
