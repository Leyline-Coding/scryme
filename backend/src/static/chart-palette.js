/* Categorical chart colours derived from the active theme (#409).
 *
 * Charts used to carry two hard-coded ramps, tuned for one dark and one light theme. A user can
 * pick any of nine palettes, two modes and an arbitrary accent, so the slice colours are computed
 * instead. The first slice takes the accent's own hue; every later slice is chosen from a wheel of
 * candidate hues and lightness levels around it, as the one that is easiest to tell apart from the
 * slice before it (and, for the last slice, from the first one it wraps round to).
 *
 * "Easy to tell apart" is measured, not guessed: the distance between two colours in OKLab, both
 * as seen with full colour vision and as simulated for protanopia and deuteranopia (Machado,
 * Oliveira & Fernandes 2009). Candidates stay inside a lightness band that reads on the mode's
 * surface and above the chroma at which a hue turns grey.
 */
(function (root) {
  "use strict";

  var HUES = 12; // candidate hues, evenly spaced from the accent's
  var LEVELS = { dark: [0.66, 0.575, 0.49], light: [0.76, 0.68, 0.6, 0.52, 0.44] };
  var CHROMA = 0.15; // target; reduced per colour until it fits sRGB
  var CHROMA_FLOOR = 0.105; // below this a hue reads as grey
  var FALLBACK_HUE = 265; // for a grey accent, which has no hue of its own
  var NORMAL_MIN = 15, CVD_MIN = 8; // OKLab distance x100 two neighbours must keep
  var SEARCH_BUDGET = 4000; // candidate placements tried before settling for the best so far
  var CVD = [
    [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
    [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.01182, 0.04294, 0.968881]],
  ];

  function parseHex(hex) {
    var m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(hex || "").trim());
    if (!m) return null;
    var h = m[1];
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    return [0, 2, 4].map(function (i) { return Number.parseInt(h.slice(i, i + 2), 16) / 255; });
  }

  function toLinear(c) { return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }
  function fromLinear(c) { return c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055; }

  function linearToOklab(lin) {
    var r = lin[0], g = lin[1], b = lin[2];
    var l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
    var m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
    var s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
    var L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
    var A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
    var B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
    return [L, A, B];
  }

  function rgbToOklch(rgb) {
    var lab = linearToOklab(rgb.map(toLinear));
    var hue = (Math.atan2(lab[2], lab[1]) * 180) / Math.PI;
    return [lab[0], Math.hypot(lab[1], lab[2]), (hue + 360) % 360];
  }

  // OKLCH -> linear sRGB, unclamped (so the caller can tell whether it is in gamut).
  function oklchToLinear(L, C, hue) {
    var rad = (hue * Math.PI) / 180;
    var A = C * Math.cos(rad), B = C * Math.sin(rad);
    var l = Math.pow(L + 0.3963377774 * A + 0.2158037573 * B, 3);
    var m = Math.pow(L - 0.1055613458 * A - 0.0638541728 * B, 3);
    var s = Math.pow(L - 0.0894841775 * A - 1.291485548 * B, 3);
    return [
      4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
      -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
      -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
    ];
  }

  function inGamut(lin) {
    return lin.every(function (c) { return c >= -0.0005 && c <= 1.0005; });
  }

  // The most chroma (up to the target) this lightness and hue can carry inside sRGB.
  function maxChroma(L, hue) {
    var lo = 0, hi = CHROMA;
    if (inGamut(oklchToLinear(L, hi, hue))) return hi;
    for (var i = 0; i < 14; i++) {
      var mid = (lo + hi) / 2;
      if (inGamut(oklchToLinear(L, mid, hue))) lo = mid; else hi = mid;
    }
    return lo;
  }

  function toHex(lin) {
    return "#" + lin.map(function (c) {
      var v = Math.round(fromLinear(Math.min(1, Math.max(0, c))) * 255);
      return (v < 16 ? "0" : "") + v.toString(16);
    }).join("");
  }

  // A candidate colour, with the three OKLab renderings (normal, protan, deutan) used to compare it.
  function candidate(L, hueIndex, hue) {
    var chroma = maxChroma(L, hue);
    if (chroma < CHROMA_FLOOR) return null; // e.g. a dark yellow: it would read as brown-grey
    var hex = toHex(oklchToLinear(L, chroma, hue));
    var lin = parseHex(hex).map(toLinear); // compare what is actually drawn, after rounding
    var views = [linearToOklab(lin)].concat(CVD.map(function (m) {
      return linearToOklab(m.map(function (row) {
        return Math.min(1, Math.max(0, row[0] * lin[0] + row[1] * lin[1] + row[2] * lin[2]));
      }));
    }));
    return { hex: hex, hue: hueIndex, views: views };
  }

  // How well two colours separate: 1 or more means they clear both thresholds.
  function separation(a, b) {
    var worst = Infinity;
    for (var v = 0; v < 3; v++) {
      var d = 100 * Math.hypot(a.views[v][0] - b.views[v][0], a.views[v][1] - b.views[v][1],
                               a.views[v][2] - b.views[v][2]);
      worst = Math.min(worst, d / (v === 0 ? NORMAL_MIN : CVD_MIN));
    }
    return worst;
  }

  /** `count` slice colours for an accent and mode. Past 12 slices the sequence repeats. */
  function palette(accent, mode, count) {
    mode = mode === "light" ? "light" : "dark";
    var rgb = parseHex(accent);
    var base = rgb ? rgbToOklch(rgb) : null;
    var hue = base && base[1] >= 0.03 ? base[2] : FALLBACK_HUE;
    var n = Math.min(count == null ? 8 : count, HUES);
    if (n <= 0) return [];

    var pool = [];
    for (var h = 0; h < HUES; h++) {
      LEVELS[mode].forEach(function (L) {
        var c = candidate(L, h, (hue + (h * 360) / HUES) % 360);
        if (c) pool.push(c);
      });
    }
    // The first slice is the accent's hue, at the level nearest the accent's own lightness.
    var first = pool.filter(function (c) { return c.hue === 0; });
    var accentL = base ? base[0] : 0.6;
    first.sort(function (a, b) {
      return Math.abs(a.views[0][0] - accentL) - Math.abs(b.views[0][0] - accentL);
    });
    var chosen = [first[0] || pool[0]];
    var used = {}; used[chosen[0].hue] = true;
    var best = null, bestScore = -1, budget = SEARCH_BUDGET;

    // Depth-first: each slice takes the unused hue that best separates from the previous slice;
    // back up if the last slice cannot also separate from the first.
    (function place(i, weakest) {
      if (i === n) {
        if (weakest > bestScore) { bestScore = weakest; best = chosen.slice(); }
        return weakest >= 1;
      }
      var options = [];
      pool.forEach(function (c) {
        if (used[c.hue]) return;
        var score = separation(c, chosen[i - 1]);
        if (i === n - 1 && n > 2) score = Math.min(score, separation(c, chosen[0]));
        options.push([score, c]);
      });
      options.sort(function (a, b) { return b[0] - a[0]; });
      for (var k = 0; k < options.length && budget > 0; k++) {
        budget--;
        chosen[i] = options[k][1]; used[chosen[i].hue] = true;
        var done = place(i + 1, Math.min(weakest, options[k][0]));
        used[chosen[i].hue] = false; chosen.length = i;
        if (done) return true;
        if (options[k][0] < 1) break; // the rest separate even worse
      }
      return false;
    })(1, Infinity);

    var out = (best || chosen).map(function (c) { return c.hex; });
    for (var i = out.length; count != null && i < count; i++) out.push(out[i % n]);
    return out;
  }

  /** Black or white, whichever reads better on `hex` (for a label drawn on a slice). */
  function inkOn(hex) {
    var rgb = parseHex(hex) || [0, 0, 0];
    var lum = 0.2126 * toLinear(rgb[0]) + 0.7152 * toLinear(rgb[1]) + 0.0722 * toLinear(rgb[2]);
    return (lum + 0.05) / 0.05 >= 1.05 / (lum + 0.05) ? "#0b0b0b" : "#ffffff";
  }

  /** Read the live theme off the document: its accent, mode and chart surface. */
  function fromTheme(count) {
    var doc = root.document.documentElement;
    var style = root.getComputedStyle(doc);
    var mode = (doc.getAttribute("data-theme") || "").indexOf("-light") >= 0 ? "light" : "dark";
    return {
      mode: mode,
      colors: palette(style.getPropertyValue("--c-accent"), mode, count),
      surface: style.getPropertyValue("--c-surface").trim() || (mode === "light" ? "#ffffff" : "#111111"),
    };
  }

  var api = { palette: palette, inkOn: inkOn, fromTheme: fromTheme };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.scrymeChartPalette = api;
})(typeof window !== "undefined" ? window : globalThis);
