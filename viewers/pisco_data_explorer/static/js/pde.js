// PIScO Data Explorer — the Dash app's callbacks as plain event handlers.
// The DataFrame stays on the server; a load returns a cache key, and every
// control change asks /plot for the figure of the current selection.

const BASE = "/viewer/pisco-data-explorer";
const TABLE_ROWS = 200;

// Same specs as pde.PRESETS on the server.
const PRESETS = {
    coarse: [0, 200, 1000, 4000, 10000],
    medium: "0:50:50, 50:200:50, 200:1000:200, 1000:4000:500, 4000:10000:1000",
    fine: "0:100:10, 100:1000:50, 1000:5000:100",
    veryfine: "0:100:5, 100:500:25, 500:2000:50",
    "1dbar": "0:1000:1",
    "10dbar": "0:5000:10",
};

let loaded = null;      // the /load summary: cache_key, categories, columns, …
let plotTimer = null;
let plotSeq = 0;        // drops answers to requests a newer one has replaced

const $ = id => document.getElementById(id);

function setStatus(id, text, isError = false) {
    const el = $(id);
    el.textContent = text || "";
    el.classList.toggle("pde-error", isError);
}

async function getJSON(path, params = {}) {
    const query = new URLSearchParams(
        Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== ""));
    const response = await fetch(`${BASE}${path}?${query}`);
    let body;
    try {
        body = await response.json();
    } catch {
        throw new Error(`${response.status} ${response.statusText}`);
    }
    if (!response.ok) throw new Error(body.error || body.description || response.statusText);
    return body;
}

function fillSelect(select, values, { placeholder = null, selected = [] } = {}) {
    select.innerHTML = "";
    if (placeholder !== null) select.add(new Option(placeholder, ""));
    for (const v of values) {
        const [value, label] = Array.isArray(v) ? v : [v, v];
        const option = new Option(label, value);
        option.selected = selected.includes(value);
        select.add(option);
    }
}

function selectedValues(select) {
    return Array.from(select.selectedOptions).map(o => o.value).filter(Boolean);
}

// ─── Depth bins ───────────────────────────────────────────────────────────────

function segmentsToEdges(text) {
    const edges = new Set();
    for (let seg of text.split(",")) {
        seg = seg.trim();
        if (!seg) continue;
        const parts = seg.split(":").map(Number);
        if (parts.length !== 3 || parts.some(Number.isNaN)) {
            throw new Error(`Bad segment '${seg}' — expected start:stop:step`);
        }
        const [start, stop, step] = parts;
        if (step <= 0 || start >= stop) throw new Error(`Invalid range in '${seg}'`);
        for (let i = 0; start + i * step <= stop + 1e-9; i++) {
            edges.add(Math.round((start + i * step) * 1e6) / 1e6);
        }
    }
    return [...edges].sort((a, b) => a - b);
}

function binMode() {
    return document.querySelector("input[name=bin-mode]:checked").value;
}

function currentEdges() {
    const mode = binMode();
    if (mode === "preset") {
        const spec = PRESETS[$("bin-preset").value];
        return Array.isArray(spec) ? spec : segmentsToEdges(spec);
    }
    if (mode === "manual") {
        const values = $("bin-manual").value.split(",").map(s => s.trim()).filter(Boolean).map(Number);
        if (values.some(Number.isNaN)) throw new Error("Manual edges must be numbers");
        return [...new Set(values)].sort((a, b) => a - b);
    }
    return segmentsToEdges($("bin-segments").value);
}

function updateBinPreview() {
    const mode = binMode();
    $("bin-preset-div").style.display = mode === "preset" ? "" : "none";
    $("bin-segments-div").style.display = mode === "segments" ? "" : "none";
    $("bin-manual-div").style.display = mode === "manual" ? "" : "none";
    try {
        const edges = currentEdges();
        if (edges.length < 2) throw new Error("Need at least two edges");
        setStatus("bin-preview", `${edges.length - 1} bins, ${edges[0]}–${edges[edges.length - 1]} dbar`);
        return edges;
    } catch (e) {
        setStatus("bin-preview", e.message, true);
        return null;
    }
}

// ─── Loading ──────────────────────────────────────────────────────────────────

async function loadHubExports() {
    try {
        const body = await getJSON("/hub_exports");
        const options = body.exports.map(e => [e.path, `${e.workflow}${e.created ? "  · " + e.created.replace("T", " ") : ""}`]);
        fillSelect($("hub-export-select"), options, {
            placeholder: options.length ? "Choose a profile…" : "No Hub exports found",
            selected: PDE_INITIAL.path ? [PDE_INITIAL.path] : [],
        });
        setStatus("hub-status", `${options.length} export(s)`);
    } catch (e) {
        fillSelect($("hub-export-select"), [], { placeholder: "—" });
        setStatus("hub-status", `Could not list exports: ${e.message}`, true);
    }
}

async function scan() {
    setStatus("root-status", "Scanning…");
    try {
        const body = await getJSON("/scan", { root: $("root-path").value, cache_root: $("cache-root").value });
        fillSelect($("cruise-select"), body.cruises, {
            placeholder: body.cruises.length ? "Choose a cruise…" : "No cruises",
            selected: body.auto ? [body.auto] : [],
        });
        setStatus("root-status", body.root_status);
        setStatus("cache-status", body.cache_status);
        if (body.auto) await listProfiles();
    } catch (e) {
        setStatus("root-status", e.message, true);
    }
}

async function listProfiles() {
    const cruise = $("cruise-select").value;
    fillSelect($("profile-select"), []);
    if (!cruise) return;
    try {
        const body = await getJSON("/profiles", {
            root: $("root-path").value, cache_root: $("cache-root").value, cruise,
        });
        const server = new Set(body.server);
        fillSelect($("profile-select"),
            body.merged.map(p => [p, server.has(p) ? p : `${p} (cache only)`]));
        setStatus("cruise-status", body.status);
    } catch (e) {
        setStatus("cruise-status", e.message, true);
    }
}

async function load(params) {
    setStatus("load-status", "Loading… (a large profile takes a while)");
    try {
        const body = await getJSON("/load", {
            ...params, root: $("root-path").value, cache_root: $("cache-root").value,
        });
        applyLoaded(body);
        optics.show(params.path || null);
    } catch (e) {
        setStatus("load-status", e.message, true);
    }
}

// ─── Optics ───────────────────────────────────────────────────────────────────
// What one frame samples: the field of view the run's parameters hold,
// with the depth of field. Runs from before the depth preview found it
// get it here: fitted on the run's own frames, checked against one of
// them, saved to the run (and, if wanted, to the cruise's other runs of
// the instrument that have none).

const optics = (() => {
    let path = null, run = null, found = null, size = null, imageUrl = null;

    const num = id => { const v = parseFloat($(id).value); return isNaN(v) ? null : v; };

    // Pixels of the circle inside the frame, as fov.area_in_frame counts them.
    function areaInFrame(cx, cy, r, w, h) {
        let area = 0;
        for (let x = 0; x < w; x++) {
            const dx = x + 0.5 - cx;
            if (Math.abs(dx) >= r) continue;
            const half = Math.sqrt(r * r - dx * dx);
            area += Math.min(cy + half, h) - Math.max(cy - half, 0);
        }
        return Math.max(area, 0);
    }

    function place(circle, d, cx, cy) {
        if (!(d > 0) || !size) { circle.style.display = "none"; return; }
        circle.style.display = "";
        circle.setAttribute("cx", cx ?? size[0] / 2);
        circle.setAttribute("cy", cy ?? size[1] / 2);
        circle.setAttribute("r", d / 2);
    }

    function draw() {
        const svg = $("optics-svg");
        if (!size) return;
        svg.setAttribute("viewBox", `0 0 ${size[0]} ${size[1]}`);
        place(svg.querySelector(".found"), found?.fov_diameter_px, found?.fov_center_x_px, found?.fov_center_y_px);
        place(svg.querySelector(".kept"), num("opt-d"), num("opt-cx"), num("opt-cy"));
        const d = num("opt-d"), dof = num("opt-dof");
        if (!(d > 0) || !(dof > 0)) { setStatus("optics-volume", ""); return; }
        const [w, h] = size;
        const area = areaInFrame(num("opt-cx") ?? w / 2, num("opt-cy") ?? h / 2, d / 2, w, h);
        const mm2 = area * (run.pixel_size_um / 1000) ** 2;
        setStatus("optics-volume", `${(area / (w * h) * 100).toFixed(0)} % of the ${w}×${h} frame at ` +
            `${run.pixel_size_um} µm/px = ${(mm2 / 100).toFixed(1)} cm² × ${dof} mm = ` +
            `${(mm2 * dof / 1e6).toFixed(3)} L per frame`);
    }

    function fill(values) {
        $("opt-d").value = values.fov_diameter_px ?? "";
        $("opt-cx").value = values.fov_center_x_px ?? "";
        $("opt-cy").value = values.fov_center_y_px ?? "";
        if (values.depth_of_field_mm != null) $("opt-dof").value = values.depth_of_field_mm;
        draw();
    }

    function summary() {
        const o = run.optics;
        $("optics-summary").textContent = o
            ? `· ${run.workflow}: ⌀ ${o.fov_diameter_px} px, ${o.depth_of_field_mm} mm deep, ${o.litres_per_frame.toFixed(3)} L per frame`
            : `· ${run.workflow}: no field of view yet, so no concentrations`;
        const all = $("optics-save-all");
        all.hidden = !run.siblings;
        all.textContent = `Save to this run and ${run.siblings} more ${run.instrument} run(s) of ${run.cruise}`;
    }

    async function detect() {
        found = null; draw();
        $("optics-use").disabled = true;
        setStatus("optics-note", "Fitting the field of view on frames spread over the run…");
        try {
            found = await getJSON("/optics/detect", { path });
        } catch (e) {
            setStatus("optics-note", e.message, true);
            return;
        }
        $("optics-use").disabled = false;
        let text = `Found ⌀ ${found.fov_diameter_px} px, centre (${found.fov_center_x_px}, ${found.fov_center_y_px}) ` +
                   `on ${found.frames_used} frames, edge fitted to ±${found.fit_rms_px} px`;
        if (found.inside_frame < 0.999) text += `; ${(found.inside_frame * 100).toFixed(0)} % of the disc lies inside the frame`;
        if (found.warning) text += `. ⚠️ ${found.warning}`;
        setStatus("optics-note", text + ".", !!found.warning);
        if (!run.optics) fill({ ...found, depth_of_field_mm: num("opt-dof") });
        draw();
    }

    async function save(scope) {
        setStatus("optics-status", "Saving…");
        try {
            const response = await fetch(`${BASE}/optics`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    path, scope,
                    fov_diameter_px: $("opt-d").value, fov_center_x_px: $("opt-cx").value,
                    fov_center_y_px: $("opt-cy").value, depth_of_field_mm: $("opt-dof").value,
                }),
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(body.error || body.description || response.statusText);
            setStatus("optics-status", `Saved to ${body.saved.join(", ")}.`);
        } catch (e) {
            setStatus("optics-status", e.message, true);
            return;
        }
        await load({ path });
    }

    async function show(newPath) {
        const box = $("optics");
        if (!newPath) { box.hidden = true; path = null; return; }
        try {
            run = await getJSON("/optics", { path: newPath });
        } catch {
            box.hidden = true;              // not the output of a Hub workflow
            path = null;
            return;
        }
        const samePath = newPath === path;
        path = newPath;
        box.hidden = false;
        summary();
        if (samePath) { draw(); return; }
        found = null;
        size = run.width && run.height ? [run.width, run.height] : null;
        setStatus("optics-status", "");
        setStatus("optics-note", run.dataset ? "" : "The run's profile_dataset.h5 is gone: enter the field of view by hand.", !run.dataset);
        $("opt-dof").value = run.params.depth_of_field_mm ?? run.default_depth_mm;
        fill(run.params);
        $("optics-detect").disabled = !run.dataset;
        if (run.dataset) {
            const r = await fetch(`${BASE}/optics/frame?path=${encodeURIComponent(path)}`);
            if (r.ok) {
                if (imageUrl) URL.revokeObjectURL(imageUrl);
                imageUrl = URL.createObjectURL(await r.blob());
                $("optics-img").src = imageUrl;
                $("optics-link").href = imageUrl;
            }
        }
        box.open = !run.optics;
        if (!run.optics && run.dataset) detect();
    }

    document.addEventListener("DOMContentLoaded", () => {
        for (const id of ["opt-d", "opt-cx", "opt-cy", "opt-dof"]) $(id).addEventListener("input", draw);
        $("optics-detect").addEventListener("click", detect);
        $("optics-use").addEventListener("click", () => found && fill(found));
        $("optics-save").addEventListener("click", () => save("run"));
        $("optics-save-all").addEventListener("click", () => save("cruise"));
    });

    return { show };
})();

function applyLoaded(meta) {
    loaded = meta;
    setStatus("load-status", meta.status);

    const keepX = $("x-axis").value || "concentration_per_L";
    const keepY = $("y-axis").value || "depth_bin_midpoint";
    fillSelect($("x-axis"), meta.columns, { selected: [keepX] });
    fillSelect($("y-axis"), meta.columns, { selected: [keepY] });

    const keepTaxa = selectedValues($("taxon-filter"));
    fillSelect($("taxon-filter"), meta.categories, { selected: keepTaxa });

    schedulePlot();
}

// ─── Plotting ─────────────────────────────────────────────────────────────────

function plotOptions() {
    const options = [];
    if ($("opt-lines").checked) options.push("lines");
    if ($("opt-logx").checked) options.push("log_x");
    if ($("opt-logy").checked) options.push("log_y");
    return options.join(",");
}

function plotType() {
    return document.querySelector("input[name=plot-type]:checked").value;
}

// The Hub's light/dark switch sets data-theme; follow it in the figure.
function themed(layout) {
    const css = getComputedStyle(document.documentElement);
    const text = css.getPropertyValue("--text").trim() || "#333";
    const dark = document.documentElement.getAttribute("data-theme") === "dark";
    const grid = dark ? "rgba(255,255,255,0.12)" : "rgba(0,0,0,0.08)";
    const axis = a => ({ gridcolor: grid, zerolinecolor: grid, ...a });
    return {
        ...layout,
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
        font: { color: text },
        xaxis: axis(layout.xaxis || {}),
        yaxis: axis(layout.yaxis || {}),
        margin: { l: 70, r: 20, t: layout.title ? 50 : 20, b: 60 },
        autosize: true,
    };
}

let lastFigure = null;
let lastTable = null;

// "<profile(s)>_<plot>", for the files the page saves.
function fileBase() {
    const what = plotType() === "esd" ? "esd_spectrum" : "depth_profile";
    const profiles = (loaded?.profiles || []).join("_") || "pisco";
    return `${profiles}_${what}`.replace(/[^\w.+-]+/g, "_").slice(0, 120);
}

function drawFigure(figure) {
    lastFigure = figure;
    $("export-open").disabled = !figure.data?.length;
    Plotly.react("main-plot", figure.data, themed(figure.layout),
        { responsive: true, displaylogo: false });
}

function schedulePlot() {
    clearTimeout(plotTimer);
    plotTimer = setTimeout(plot, 250);
}

async function plot() {
    if (!loaded) return;
    const edges = updateBinPreview();
    if (!edges) return;

    const seq = ++plotSeq;
    setStatus("pde-status-line", "Plotting…");
    try {
        const body = await getJSON("/plot", {
            cache_key: loaded.cache_key,
            edges: JSON.stringify(edges),
            plot_type: plotType(),
            x_axis: $("x-axis").value,
            y_axis: $("y-axis").value,
            color: $("color-select").value,
            options: plotOptions(),
            taxon: selectedValues($("taxon-filter")).join(","),
            validated: $("validated-only").checked ? "1" : "",
            esd_min: $("esd-min").value,
            esd_max: $("esd-max").value,
            esd_nbins: $("esd-nbins").value,
            esd_yaxis: $("esd-yaxis").value,
        });
        if (seq !== plotSeq) return;
        drawFigure(body.figure);
        renderTable(body.table);
        lastTable = body.table;
        $("table-download").disabled = !body.table.data.length;
        $("table-count").textContent = body.table.data.length
            ? `${body.table.data.length.toLocaleString()} row(s)` : "";
        setStatus("pde-status-line", body.status || `${body.points.toLocaleString()} point(s)`);
    } catch (e) {
        if (seq !== plotSeq) return;
        setStatus("pde-status-line", e.message, true);
    }
}

function formatCell(value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "number" && !Number.isInteger(value)) {
        return Math.abs(value) >= 1e-3 && Math.abs(value) < 1e6 ? value.toFixed(4) : value.toExponential(3);
    }
    return String(value);
}

function renderTable(table) {
    const wrap = $("data-table-wrap");
    wrap.innerHTML = "";
    if (!table.data.length) return;

    const t = document.createElement("table");
    const head = t.createTHead().insertRow();
    for (const col of table.columns) {
        const th = document.createElement("th");
        th.textContent = col.name;
        head.appendChild(th);
    }
    const tbody = t.createTBody();
    for (const row of table.data.slice(0, TABLE_ROWS)) {
        const tr = tbody.insertRow();
        for (const col of table.columns) tr.insertCell().textContent = formatCell(row[col.id]);
    }
    wrap.appendChild(t);

    if (table.data.length > TABLE_ROWS) {
        const note = document.createElement("div");
        note.className = "pde-table-note";
        note.textContent = `First ${TABLE_ROWS} of ${table.data.length.toLocaleString()} rows`;
        wrap.appendChild(note);
    }
}

// All rows of the table the last plot came with, as CSV or TSV.
function downloadTable() {
    if (!lastTable?.data?.length) return;
    const tsv = $("table-format").value === "tsv";
    const sep = tsv ? "\t" : ",";
    const cell = v => {
        if (v === null || v === undefined || (typeof v === "number" && !Number.isFinite(v))) return "";
        const text = String(v);
        if (tsv) return text.replace(/[\t\r\n]+/g, " ");
        return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    };
    const cols = lastTable.columns;
    const lines = [cols.map(c => cell(c.name)).join(sep),
                   ...lastTable.data.map(row => cols.map(c => cell(row[c.id])).join(sep))];
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([lines.join("\n") + "\n"],
        { type: tsv ? "text/tab-separated-values" : "text/csv" }));
    link.download = `${fileBase()}_table.${tsv ? "tsv" : "csv"}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

// ─── Figure export ────────────────────────────────────────────────────────────
// The figure on screen, redrawn in a print style (black on white, whatever
// the Hub's theme) with the labels, legend, size and format the dialog
// holds, and saved by Plotly's own image renderer. The preview is the
// export at its real layout size, scaled down to fit, so what it shows
// is what the file will look like.
//
// Sizes in cm or inches are laid out at 72 units per inch, so a font
// size is in points and the DPI only sets the raster's resolution.

const exporter = (() => {
    const STYLE_KEY = "pde-export-style";
    const PRESETS = {
        screen: { unit: "px", width: 1200, height: 800, res: 2, fontsize: 14, linewidth: 2, markersize: 6 },
        slide:  { unit: "px", width: 1600, height: 900, res: 2, fontsize: 20, linewidth: 3, markersize: 8 },
        single: { unit: "cm", width: 8.5, height: 7, res: 600, fontsize: 8, linewidth: 1, markersize: 4 },
        double: { unit: "cm", width: 17.5, height: 11, res: 600, fontsize: 9, linewidth: 1.2, markersize: 4.5 },
        a4:     { unit: "cm", width: 25, height: 17, res: 300, fontsize: 11, linewidth: 1.5, markersize: 5 },
    };
    const PER_INCH = { px: null, cm: 72 / 2.54, in: 72 };
    const LEGEND = {
        right:  { x: 1.02, xanchor: "left", y: 1, yanchor: "top" },
        bottom: { orientation: "h", x: 0.5, xanchor: "center", y: -0.12, yanchor: "top" },
        tr: { x: 0.99, xanchor: "right", y: 0.99, yanchor: "top" },
        tl: { x: 0.01, xanchor: "left", y: 0.99, yanchor: "top" },
        br: { x: 0.99, xanchor: "right", y: 0.01, yanchor: "bottom" },
        bl: { x: 0.01, xanchor: "left", y: 0.01, yanchor: "bottom" },
    };

    let source = null;      // the figure the dialog's labels were taken from
    let traceRows = [];     // [{show, name}] per trace of `source`
    let timer = null;

    const num = id => { const v = parseFloat($(id).value); return isNaN(v) ? null : v; };
    const titleText = t => (typeof t === "string" ? t : t?.text) || "";
    const styleFields = () => document.querySelectorAll("#export-dialog [data-style]");

    // ── settings that outlive a figure: size, font, format… ──
    function saveStyle() {
        const style = { preset: $("exp-preset").value };
        for (const el of styleFields()) style[el.id] = el.type === "checkbox" ? el.checked : el.value;
        try { localStorage.setItem(STYLE_KEY, JSON.stringify(style)); } catch { /* private window */ }
    }

    function loadStyle() {
        let style = null;
        try { style = JSON.parse(localStorage.getItem(STYLE_KEY)); } catch { /* none */ }
        if (!style) return;
        for (const el of styleFields()) {
            if (!(el.id in style)) continue;
            if (el.type === "checkbox") el.checked = !!style[el.id]; else el.value = style[el.id];
        }
        $("exp-preset").value = style.preset || "";
        wantedPreset = style.preset || "";
        unitLabels();
    }

    function unitLabels() {
        const px = $("exp-unit").value === "px";
        $("exp-res-label").textContent = px ? "Scale (×)" : "Resolution (dpi)";
        $("exp-fontunit").textContent = px ? "px" : "pt";
        $("exp-unit").dataset.prev = $("exp-unit").value;
    }

    // Layout size in Plotly's units and the factor to the raster's pixels.
    function size() {
        const unit = $("exp-unit").value;
        const k = PER_INCH[unit] ?? 1;
        const res = num("exp-res") || (unit === "px" ? 1 : 300);
        return {
            w: Math.max(50, Math.round((num("exp-width") || 0) * k)),
            h: Math.max(50, Math.round((num("exp-height") || 0) * k)),
            scale: unit === "px" ? res : res / 72,
        };
    }

    // Switching the unit keeps the figure as it is: same layout, same pixels.
    function changeUnit(from, to) {
        const kFrom = PER_INCH[from] ?? 1, kTo = PER_INCH[to] ?? 1;
        const round = v => Math.round(v * 100) / 100;
        for (const id of ["exp-width", "exp-height"]) {
            const v = num(id);
            if (v !== null) $(id).value = round(v * kFrom / kTo);
        }
        const res = num("exp-res");
        if (res !== null && (from === "px") !== (to === "px")) {
            $("exp-res").value = to === "px" ? round(res / 72) : Math.round(res * 72);
        }
        unitLabels();
    }

    function applyPreset(name) {
        const p = PRESETS[name];
        if (!p) return;
        $("exp-unit").value = p.unit;
        for (const key of ["width", "height", "res", "fontsize", "linewidth", "markersize"]) {
            $(`exp-${key}`).value = p[key];
        }
        unitLabels();
    }

    // ── saved presets, kept on the Hub ──
    // A preset holds the style fields as they are, and the labels with the
    // original title they replace: an axis label goes back only onto the
    // same axis, a series name onto the series of that name (an ESD depth
    // bin's "(n=…)" ignored, since the count differs per profile).
    const SAVED = "saved:";
    let saved = [];              // [{name, settings, saved_by, saved}]
    let wantedPreset = "";       // the stored choice, until the list arrives

    const profileName = () => (loaded?.profiles || []).join(", ");
    const seriesKey = name => String(name ?? "").replace(/\s*\(n=[\d,]+\)\s*$/, "").trim();
    const savedPreset = () => {
        const value = $("exp-preset").value;
        return value.startsWith(SAVED) ? saved.find(p => p.name === value.slice(SAVED.length)) : null;
    };

    function capture() {
        const style = {};
        for (const el of styleFields()) style[el.id] = el.type === "checkbox" ? el.checked : el.value;
        const src = source?.layout || {};
        const axis = (key, label, min, max) => ({
            from: titleText(src[key]?.title), to: $(label).value, min: $(min).value, max: $(max).value });
        const series = {};
        (source?.data || []).forEach((trace, i) => {
            const row = traceRows[i], original = trace.name ?? "";
            if (row.name.value !== original || !row.show.checked) {
                series[seriesKey(original)] = {
                    name: row.name.value !== original ? row.name.value : null, show: row.show.checked };
            }
        });
        let title = $("exp-title").value;
        const profile = profileName();
        if (profile && title.includes(profile)) title = title.replaceAll(profile, "{profile}");
        return {
            style, title, show_legend: $("exp-showlegend").checked,
            legend_title: { from: titleText(src.legend?.title), to: $("exp-legend-title").value },
            x: axis("xaxis", "exp-xlabel", "exp-xmin", "exp-xmax"),
            y: axis("yaxis", "exp-ylabel", "exp-ymin", "exp-ymax"),
            series,
        };
    }

    function applyStyle(style = {}) {
        for (const el of styleFields()) {
            if (!(el.id in style)) continue;
            if (el.type === "checkbox") el.checked = !!style[el.id]; else el.value = style[el.id];
        }
        unitLabels();
    }

    // Labels onto the figure in the dialog, starting from the figure's own.
    // Returns what did not fit, for the note under the preset.
    function applyLabels(settings) {
        if (!source) return [];
        takeLabels(source);
        const src = source.layout || {};
        const kept = [];
        $("exp-title").value = settings.title ?? "";
        if (settings.show_legend !== undefined) $("exp-showlegend").checked = settings.show_legend;
        const legend = settings.legend_title;
        if (legend && legend.from === titleText(src.legend?.title)) $("exp-legend-title").value = legend.to;
        for (const [key, axisKey, label, min, max] of [["x", "xaxis", "exp-xlabel", "exp-xmin", "exp-xmax"],
                                                       ["y", "yaxis", "exp-ylabel", "exp-ymin", "exp-ymax"]]) {
            const axis = settings[key];
            if (!axis) continue;
            if (axis.from !== titleText(src[axisKey]?.title)) {
                kept.push(`${key.toUpperCase()} label and range not applied: the preset was made for “${axis.from}”.`);
                continue;
            }
            $(label).value = axis.to;
            $(min).value = axis.min ?? "";
            $(max).value = axis.max ?? "";
        }
        source.data.forEach((trace, i) => {
            const entry = settings.series?.[seriesKey(trace.name)];
            if (!entry) return;
            if (entry.name !== null && entry.name !== undefined) traceRows[i].name.value = entry.name;
            traceRows[i].show.checked = entry.show !== false;
        });
        return kept;
    }

    function presetNote(preset, extra = []) {
        const parts = [];
        if (preset) {
            const when = (preset.saved || "").slice(0, 10);
            parts.push(`Saved${preset.saved_by ? ` by ${preset.saved_by}` : ""}${when ? ` on ${when}` : ""}.`);
        }
        setStatus("exp-preset-note", [...parts, ...extra].join(" "), extra.length > 0);
        $("exp-preset-delete").hidden = !preset;
    }

    async function loadPresets() {
        try {
            saved = (await getJSON("/export_presets")).presets || [];
        } catch (e) {
            setStatus("exp-preset-note", `Saved presets unavailable: ${e.message}`, true);
            return;
        }
        const group = $("exp-saved-group");
        const current = wantedPreset || $("exp-preset").value;
        group.replaceChildren(...saved.map(p => new Option(p.name, SAVED + p.name)));
        group.hidden = !saved.length;
        $("exp-preset").value = [...$("exp-preset").options].some(o => o.value === current) ? current : "";
        wantedPreset = "";
        presetNote(savedPreset());
    }

    function choosePreset() {
        const value = $("exp-preset").value;
        const preset = savedPreset();
        if (preset) {
            applyStyle(preset.settings.style);
            presetNote(preset, applyLabels(preset.settings));
            $("exp-preset-name").value = preset.name;
        } else {
            applyPreset(value);
            presetNote(null);
        }
        saveStyle();
        schedule();
    }

    async function savePreset() {
        const name = $("exp-preset-name").value.trim();
        if (!name) { setStatus("exp-preset-note", "Give the preset a name first.", true); return; }
        try {
            const response = await fetch(`${BASE}/export_presets`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name, settings: capture() }),
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(body.error || response.statusText);
            wantedPreset = SAVED + body.name;     // its option exists once the list is back
            await loadPresets();
            saveStyle();
            setStatus("exp-preset-note", `${body.replaced ? "Updated" : "Saved"} “${body.name}”.`);
            $("exp-preset-delete").hidden = false;
        } catch (e) {
            setStatus("exp-preset-note", `Could not save: ${e.message}`, true);
        }
    }

    async function deletePreset() {
        const preset = savedPreset();
        if (!preset || !confirm(`Delete the preset “${preset.name}” for everyone on this Hub?`)) return;
        try {
            const response = await fetch(`${BASE}/export_presets/${encodeURIComponent(preset.name)}`, { method: "DELETE" });
            if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || response.statusText);
        } catch (e) {
            setStatus("exp-preset-note", `Could not delete: ${e.message}`, true);
            return;
        }
        $("exp-preset").value = "";
        await loadPresets();
        saveStyle();
        setStatus("exp-preset-note", `Deleted “${preset.name}”.`);
    }

    // ── labels and legend entries, taken from the figure on screen ──
    function takeLabels(figure) {
        source = figure;
        const layout = figure.layout || {};
        $("exp-title").value = titleText(layout.title);
        $("exp-xlabel").value = titleText(layout.xaxis?.title);
        $("exp-ylabel").value = titleText(layout.yaxis?.title);
        $("exp-legend-title").value = titleText(layout.legend?.title);
        $("exp-showlegend").checked = layout.showlegend ?? figure.data.length > 1;
        for (const id of ["exp-xmin", "exp-xmax", "exp-ymin", "exp-ymax"]) $(id).value = "";

        const list = $("exp-traces");
        list.innerHTML = "";
        traceRows = figure.data.map((trace, i) => {
            const row = document.createElement("div");
            row.className = "pde-export-trace";
            const show = document.createElement("input");
            show.type = "checkbox";
            show.checked = trace.visible !== false;
            show.title = "Include this series";
            const swatch = document.createElement("span");
            swatch.className = "pde-export-swatch";
            swatch.style.background = trace.line?.color || trace.marker?.color || "#888";
            const name = document.createElement("input");
            name.type = "text";
            name.value = trace.name ?? `Series ${i + 1}`;
            row.append(show, swatch, name);
            list.appendChild(row);
            return { show, name };
        });
        list.hidden = !traceRows.length;

        $("exp-filename").value = fileBase();
    }

    // [lo, hi] in the axis's own units, or null to let Plotly choose.
    function axisRange(axis, minId, maxId, label, problems) {
        const a = num(minId), b = num(maxId);
        if (a === null && b === null) return null;
        if (a === null || b === null || a === b) {
            problems.push(`${label} range needs two different values`);
            return null;
        }
        let range = [Math.min(a, b), Math.max(a, b)];
        if (axis.type === "log") {
            if (range[0] <= 0) { problems.push(`${label} is logarithmic: the range must be above 0`); return null; }
            range = range.map(Math.log10);
        }
        if (axis.autorange === "reversed") range.reverse();
        return range;
    }

    function build() {
        const problems = [];
        const { w, h, scale } = size();
        const format = $("exp-format").value;
        const fontSize = num("exp-fontsize") || 12;
        const font = { family: $("exp-font").value, size: fontSize, color: "#000" };
        const transparent = $("exp-bg").value === "transparent" && format !== "jpeg";
        const background = transparent ? "rgba(0,0,0,0)" : "#fff";
        const lineWidth = num("exp-linewidth"), markerSize = num("exp-markersize");
        const grid = $("exp-grid").checked, frame = $("exp-frame").checked;
        $("exp-logdecades").disabled = ![source.layout?.xaxis?.type, source.layout?.yaxis?.type].includes("log");

        const data = source.data.map((trace, i) => {
            const row = traceRows[i];
            const out = { ...trace, name: row.name.value, visible: row.show.checked };
            if (lineWidth !== null) out.line = { ...trace.line, width: lineWidth };
            if (markerSize !== null) out.marker = { ...trace.marker, size: markerSize };
            return out;
        });

        const axis = (src, title, range) => {
            const out = {
                ...src,
                title: { text: title, font, standoff: Math.round(fontSize * 0.6) },
                automargin: true,
                color: "#000",
                tickfont: font,
                showgrid: grid, gridcolor: "#dcdcdc", gridwidth: Math.max(0.5, fontSize / 20),
                zeroline: false,
                showline: true, linecolor: "#000", linewidth: Math.max(0.75, fontSize / 14),
                mirror: frame ? "ticks" : false,
                ticks: "outside", ticklen: Math.round(fontSize * 0.4), tickcolor: "#000",
                tickwidth: Math.max(0.75, fontSize / 14),
                exponentformat: $("exp-numfmt").value,
                minorloglabels: $("exp-logdecades").checked ? "none" : "small digits",
            };
            if (range) { out.range = range; out.autorange = false; }
            return out;
        };
        const src = source.layout || {};
        const xRange = axisRange(src.xaxis || {}, "exp-xmin", "exp-xmax", "X", problems);
        const yRange = axisRange(src.yaxis || {}, "exp-ymin", "exp-ymax", "Y", problems);

        const title = $("exp-title").value.trim().replaceAll("{profile}", profileName());
        const legendTitle = $("exp-legend-title").value.trim();
        const pos = $("exp-legend-pos").value;
        const inside = !["right", "bottom"].includes(pos);
        const pad = Math.round(fontSize * 0.8);
        const layout = {
            width: w, height: h,
            font,
            paper_bgcolor: background,
            plot_bgcolor: background,
            title: title ? { text: title, font: { ...font, size: Math.round(fontSize * 1.25) },
                             x: 0.5, xanchor: "center" } : undefined,
            xaxis: axis(src.xaxis || {}, $("exp-xlabel").value, xRange),
            yaxis: axis(src.yaxis || {}, $("exp-ylabel").value, yRange),
            showlegend: $("exp-showlegend").checked,
            legend: {
                ...LEGEND[pos],
                title: legendTitle ? { text: legendTitle, font } : undefined,
                font,
                bgcolor: inside ? (transparent ? "rgba(255,255,255,0)" : "rgba(255,255,255,0.85)") : "rgba(0,0,0,0)",
                bordercolor: inside ? "#000" : "rgba(0,0,0,0)",
                borderwidth: inside ? 0.75 : 0,
            },
            margin: { l: pad, r: pad, b: pad, t: title ? Math.round(fontSize * 3) : pad },
        };
        return { data, layout, w, h, scale, format, problems };
    }

    const MAX_PIXELS = 80e6;   // beyond this the browser's canvas gives up

    // Plotly places a legend below the plot by plot fraction and does not
    // look at the x-axis title, so the two can overlap: measure on the
    // rendered figure and move the legend down until they don't.
    async function clearBottomLegend(target, fig) {
        if ($("exp-legend-pos").value !== "bottom" || !fig.layout.showlegend) return;
        const gap = (num("exp-fontsize") || 12) * 0.5;
        for (let pass = 0; pass < 3; pass++) {
            const title = target.querySelector(".g-xtitle"), box = target.querySelector(".legend");
            if (!title || !box) return;
            const unit = target.getBoundingClientRect().width / fig.w;   // screen px per layout px
            const overlap = (title.getBoundingClientRect().bottom - box.getBoundingClientRect().top) / unit + gap;
            if (overlap <= 0.5) return;
            fig.layout = { ...fig.layout,
                           legend: { ...fig.layout.legend, y: fig.layout.legend.y - overlap / target._fullLayout._size.h } };
            await Plotly.react(target, fig.data, fig.layout, { staticPlot: true, displayModeBar: false });
        }
    }

    // Renders the preview and returns the figure as rendered, ready to save.
    async function preview() {
        if (!source) return null;
        const fig = build();
        const box = $("exp-preview-box");
        const target = $("exp-preview");
        await Plotly.react(target, fig.data, fig.layout, { staticPlot: true, displayModeBar: false });
        await clearBottomLegend(target, fig);
        const area = box.parentElement;
        const fit = Math.min(1, (area.clientWidth - 48) / fig.w, (area.clientHeight - 72) / fig.h);
        target.style.transform = `scale(${fit})`;
        box.style.width = `${fig.w * fit}px`;
        box.style.height = `${fig.h * fit}px`;
        box.classList.toggle("transparent", fig.layout.paper_bgcolor !== "#fff");

        const out = fig.format === "svg"
            ? `SVG, ${fig.w} × ${fig.h} (vector)`
            : `${fig.format.toUpperCase()}, ${Math.round(fig.w * fig.scale)} × ${Math.round(fig.h * fig.scale)} px`;
        if (fig.format !== "svg" && fig.w * fig.h * fig.scale ** 2 > MAX_PIXELS) {
            fig.problems.push("too many pixels for the browser to render: lower the size or resolution");
            fig.tooLarge = true;
        }
        const note = fig.problems.length ? ` · ⚠️ ${fig.problems.join("; ")}` : "";
        setStatus("exp-info", `${out}${fit < 1 ? ` · preview at ${Math.round(fit * 100)} %` : ""}${note}`,
                  !!fig.problems.length);
        return fig;
    }

    function schedule() {
        clearTimeout(timer);
        timer = setTimeout(preview, 150);
    }

    function open() {
        if (!lastFigure?.data?.length) return;
        // Keep the labels typed for this figure; a new figure starts from its own.
        if (source !== lastFigure) {
            takeLabels(lastFigure);
            const preset = savedPreset();
            if (preset) presetNote(preset, applyLabels(preset.settings));
        }
        $("export-dialog").showModal();
        preview();
    }

    async function download() {
        clearTimeout(timer);
        const fig = await preview();
        if (!fig || fig.tooLarge) return;
        const name = ($("exp-filename").value.trim() || "pisco_figure").replace(/\.(png|svg|jpe?g|webp)$/i, "");
        setStatus("exp-info", "Rendering…");
        try {
            await Plotly.downloadImage({ data: fig.data, layout: fig.layout }, {
                format: fig.format, width: fig.w, height: fig.h,
                scale: fig.format === "svg" ? 1 : fig.scale, filename: name,
            });
            setStatus("exp-info", `Saved ${name}.${fig.format === "jpeg" ? "jpeg" : fig.format}`);
        } catch (e) {
            setStatus("exp-info", `Could not render: ${e.message || e}`, true);
        }
    }

    document.addEventListener("DOMContentLoaded", () => {
        const dialog = $("export-dialog");
        loadStyle();

        $("export-open").addEventListener("click", open);
        for (const el of dialog.querySelectorAll("[data-close]")) {
            el.addEventListener("click", () => dialog.close());
        }
        // A click on the backdrop lands on the dialog element itself.
        dialog.addEventListener("click", e => { if (e.target === dialog) dialog.close(); });
        $("exp-download").addEventListener("click", download);
        $("exp-reset").addEventListener("click", () => { takeLabels(lastFigure || source); schedule(); });

        $("exp-preset").addEventListener("change", choosePreset);
        $("exp-preset-save").addEventListener("click", savePreset);
        $("exp-preset-name").addEventListener("keydown", e => { if (e.key === "Enter") savePreset(); });
        $("exp-preset-delete").addEventListener("click", deletePreset);
        loadPresets();
        unitLabels();
        $("exp-unit").addEventListener("change", () => {
            changeUnit($("exp-unit").dataset.prev, $("exp-unit").value);
            saveStyle();
        });

        dialog.addEventListener("input", e => {
            if (e.target.matches("[data-style]")) {
                if (["exp-width", "exp-height", "exp-unit", "exp-res", "exp-fontsize",
                     "exp-linewidth", "exp-markersize"].includes(e.target.id) && $("exp-preset").value) {
                    $("exp-preset").value = "";
                    presetNote(null);
                }
                saveStyle();
            }
            schedule();
        });
        dialog.addEventListener("change", schedule);
        window.addEventListener("resize", () => { if (dialog.open) schedule(); });
    });

    return { open };
})();

// ─── Wiring ───────────────────────────────────────────────────────────────────

function togglePlotControls() {
    const esd = plotType() === "esd";
    $("depth-controls").style.display = esd ? "none" : "";
    $("esd-controls").style.display = esd ? "" : "none";
}

document.addEventListener("DOMContentLoaded", () => {
    $("scan-btn").addEventListener("click", scan);
    $("cruise-select").addEventListener("change", listProfiles);
    $("load-btn").addEventListener("click", () => {
        const profiles = selectedValues($("profile-select"));
        if (!profiles.length) return setStatus("load-status", "Select at least one profile", true);
        load({ cruise: $("cruise-select").value, profiles: profiles.join(",") });
    });
    $("load-path-btn").addEventListener("click", () => {
        const path = $("tsv-path").value.trim();
        if (path) load({ path });
    });
    $("table-download").addEventListener("click", downloadTable);
    $("load-hub-btn").addEventListener("click", () => {
        const path = $("hub-export-select").value;
        if (path) load({ path });
    });

    for (const el of document.querySelectorAll("input[name=bin-mode], input[name=plot-type]")) {
        el.addEventListener("change", () => { togglePlotControls(); schedulePlot(); });
    }
    for (const id of ["bin-preset", "bin-segments", "bin-manual"]) {
        $(id).addEventListener("input", () => { updateBinPreview(); schedulePlot(); });
    }
    for (const id of ["x-axis", "y-axis", "color-select", "esd-min", "esd-max", "esd-yaxis",
                      "opt-lines", "opt-logx", "opt-logy", "taxon-filter", "validated-only"]) {
        $(id).addEventListener("change", schedulePlot);
    }
    $("esd-nbins").addEventListener("input", () => {
        $("esd-nbins-val").textContent = $("esd-nbins").value;
        schedulePlot();
    });

    // Re-theme the figure when the Hub's light/dark toggle flips.
    new MutationObserver(() => { if (lastFigure) drawFigure(lastFigure); })
        .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    togglePlotControls();
    updateBinPreview();
    loadHubExports();

    if (PDE_INITIAL.cruise) $("tree-browser").open = true;
    if (PDE_INITIAL.root) scan();
    if (PDE_INITIAL.loaded) applyLoaded(PDE_INITIAL.loaded);
    if (PDE_INITIAL.path) optics.show(PDE_INITIAL.path);
});
