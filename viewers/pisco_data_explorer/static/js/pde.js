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

function drawFigure(figure) {
    lastFigure = figure;
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
