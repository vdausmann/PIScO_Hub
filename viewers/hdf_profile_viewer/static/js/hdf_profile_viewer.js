// --- Global View State Tracking Elements ---
function showLoader() {
	const globalLoader = document.getElementById('global-loader');
	if (globalLoader) globalLoader.style.display = 'flex'; 
}
function hideLoader() { 
	const globalLoader = document.getElementById('global-loader');
	if (globalLoader) globalLoader.style.display = 'none'; 
}

// --- File structure: browsed one group at a time --------------------------
// A PISCO3 cast holds tens of thousands of frame groups, so the tree is never
// sent whole: each group lists its children a page at a time when opened, and
// the frame list at the top can be narrowed by name on the server.

async function fetchChildren(group, offset, query) {
    const params = new URLSearchParams({ path: filePath, group, offset: String(offset) });
    if (query) params.set("q", query);
    const response = await fetch(`/viewer/hdf-profile-viewer/children?${params}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || response.statusText);
    return payload;
}

// Appends one page of `group`'s children to `ul`, followed by a "more" entry
// while there are any left. Returns the page so the caller can show totals.
async function appendChildren(ul, group, offset, query) {
    const page = await fetchChildren(group, offset, query);
    page.children.forEach(child => ul.appendChild(treeNode(child)));

    const shown = page.offset + page.children.length;
    if (shown < page.total) {
        const more = document.createElement("li");
        more.className = "tree-more";
        more.textContent = `show more (${shown} of ${page.total})`;
        more.addEventListener("click", async () => {
            more.textContent = "loading…";
            try {
                await appendChildren(ul, group, shown, query);
                more.remove();
            } catch (err) {
                console.error("Error listing group:", err);
                more.textContent = "could not load more";
            }
        });
        ul.appendChild(more);
    }
    return page;
}

function treeNode(node) {
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.className = "tree-label";
    label.textContent = node.name.split("/").pop() || "/";
    li.appendChild(label);

    if (node.type === "dataset") {
        label.style.cursor = "pointer";
        label.addEventListener("click", () => {
            showLoader();
            // Fire both layout components
            loadDatasetInfo(node.name);
            loadDatasetData(node.name);
        });
        return li;
    }

    // Groups open on click; their children are fetched the first time only.
    li.classList.add("tree-group");
    let ul = null;
    label.addEventListener("click", async () => {
        if (ul) {
            li.classList.toggle("open");
            ul.hidden = !li.classList.contains("open");
            return;
        }
        ul = document.createElement("ul");
        li.appendChild(ul);
        li.classList.add("open");
        try {
            await appendChildren(ul, node.name, 0, null);
        } catch (err) {
            console.error("Error listing group:", err);
            ul.innerHTML = "<li class=\"tree-more\">could not list this group</li>";
        }
    });
    return li;
}

async function loadTree(query) {
    const root = document.getElementById("tree-root");
    const count = document.getElementById("tree-count");
    root.innerHTML = "";
    if (count) count.textContent = "loading…";
    try {
        const page = await appendChildren(root, "/", 0, query || null);
        if (count) {
            count.textContent = query
                ? `${page.total} matching "${query}"`
                : `${page.total} entries`;
        }
    } catch (err) {
        console.error("Error listing file:", err);
        if (count) count.textContent = `could not read the file: ${err.message}`;
    }
}

function setupTreeFilter() {
    const input = document.getElementById("tree-filter");
    if (!input) return;
    let timer = null;
    input.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(() => loadTree(input.value.trim()), 250);
    });
}

async function loadDatasetInfo(datasetPath) {
    const response = await fetch(`/viewer/hdf-profile-viewer/dataset_info?file=${encodeURIComponent(filePath)}&dataset=${encodeURIComponent(datasetPath)}`);
    const info = await response.json();

    document.getElementById("dataset-info").innerHTML = `

        <p><b>Group Attributes:</b>${info.group_attrs}</p>
        <p><b>Dataset Attributes:</b>${info.dataset_attrs}</p>
        <p><b>Name:</b> ${info.name}</p>
        <p><b>Shape:</b> ${info.shape.join(" × ")}</p>
        <p><b>Type:</b> ${info.dtype}</p>
        <p><b>Elements:</b> ${info.size}</p>
    `;
}

// Replaces your old renderDataset function completely
async function loadDatasetData(datasetPath) {
    try {
        const response = await fetch(`/viewer/hdf-profile-viewer/dataset_data?file=${encodeURIComponent(filePath)}&dataset=${encodeURIComponent(datasetPath)}`);
        const payload = await response.json();

        if (payload.type === "frame") {
            renderFrame(payload);
        } else {
            // Inject the pre-built HTML layout directly from the server
            document.getElementById("dataset-preview").innerHTML = payload.html;
        }
    } catch (err) {
        console.error("Error drawing workspace component:", err);
    } finally {
        hideLoader();
    }
}

// --- Frame view: pick a crop out of the overview -------------------------
// The preview is a downscaled frame, so the only way to judge an object is to
// pull the crop itself at full resolution. The server sends where every crop
// sits in the preview; the rest is hit-testing.

let frameState = null;

// Boxes arrive as [index, x, y, w, h, fullW, fullH].
const BOX_I = 0, BOX_X = 1, BOX_Y = 2, BOX_W = 3, BOX_H = 4, BOX_FW = 5, BOX_FH = 6;

// Small objects can be a pixel or two across in the preview, so allow a little
// slack around each box and prefer the tightest match when they overlap.
const HIT_SLACK = 3;

function boxAt(x, y) {
    if (!frameState) return null;
    let best = null, bestArea = Infinity;
    for (const b of frameState.boxes) {
        if (x >= b[BOX_X] - HIT_SLACK && x <= b[BOX_X] + b[BOX_W] + HIT_SLACK &&
            y >= b[BOX_Y] - HIT_SLACK && y <= b[BOX_Y] + b[BOX_H] + HIT_SLACK) {
            const area = b[BOX_W] * b[BOX_H];
            if (area < bestArea) { best = b; bestArea = area; }
        }
    }
    return best;
}

function drawOverlay() {
    const canvas = document.getElementById("frame-overlay");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const mark = (b, colour, width) => {
        if (!b) return;
        ctx.strokeStyle = colour;
        ctx.lineWidth = width;
        const pad = 2;
        ctx.strokeRect(b[BOX_X] - pad, b[BOX_Y] - pad,
                       b[BOX_W] + pad * 2, b[BOX_H] + pad * 2);
    };
    mark(frameState.hovered, "#f59e0b", 1.5);
    mark(frameState.selected, "#dc2626", 2);
}

function renderFrame(payload) {
    frameState = {
        boxes: payload.boxes,
        dataset: payload.dataset,
        // Both pictures of this frame: the reconstruction the server just
        // sent, and the raw source image, fetched only if asked for.
        reconImage: payload.image,
        rawImage: null,
        // The dataset lives at "<frame>/cropPixelData"; the frame name is what
        // the profile plot matches on to mark where this frame sits.
        frameName: String(payload.dataset || "").split("/").filter(Boolean)[0] || null,
        size: payload.preview_size,
        hovered: null,
        selected: null,
    };

    document.getElementById("dataset-preview").innerHTML = `
        <div class="frame-hint">
            <b>${payload.boxes.length}</b> objects &middot; click one to see it at full resolution
            <label class="frame-toggle">
                <input type="checkbox" id="frame-raw"> raw frame
            </label>
            <span id="frame-note" class="frame-note"></span>
        </div>
        <div class="frame-layout">
            <div class="frame-stage" id="frame-stage">
                <img id="frame-image" alt="Frame preview"
                     width="${payload.preview_size}" height="${payload.preview_size}"
                     src="data:image/png;base64,${payload.image}">
                <canvas id="frame-overlay"
                        width="${payload.preview_size}" height="${payload.preview_size}"></canvas>
            </div>
            <div class="crop-panel">
                <div class="crop-nav">
                    <button id="crop-prev" disabled>&larr;</button>
                    <span id="crop-label">no object selected</span>
                    <button id="crop-next" disabled>&rarr;</button>
                </div>
                <label class="crop-toggle">
                    <input type="checkbox" id="crop-isolate"> isolate object
                </label>
                <div id="crop-holder" class="crop-holder">
                    Click an object in the frame, or use the arrows to step through them.
                </div>
                <div id="crop-meta" class="crop-meta"></div>
            </div>
            <div class="profile-panel">
                <div class="profile-head">
                    <b>Profile</b>
                    <label>bin
                        <select id="profile-bin">
                            <option value="0">per frame</option>
                            <option value="0.1">0.1 dbar</option>
                            <option value="0.25">0.25 dbar</option>
                            <option value="0.5">0.5 dbar</option>
                            <option value="1">1 dbar</option>
                            <option value="2">2 dbar</option>
                        </select>
                    </label>
                    <label><input type="checkbox" id="profile-log"> log</label>
                </div>
                <canvas id="profile-plot" width="360" height="460"></canvas>
                <div id="profile-readout" class="profile-readout">objects against depth</div>
            </div>
        </div>`;

    const stage = document.getElementById("frame-stage");
    const canvas = document.getElementById("frame-overlay");

    // The preview may be laid out smaller than its natural size, so map screen
    // coordinates back onto the image's own pixel grid.
    const toImageCoords = (event) => {
        const rect = canvas.getBoundingClientRect();
        return [(event.clientX - rect.left) * (canvas.width / rect.width),
                (event.clientY - rect.top) * (canvas.height / rect.height)];
    };

    canvas.addEventListener("mousemove", (event) => {
        const [x, y] = toImageCoords(event);
        const hit = boxAt(x, y);
        if (hit !== frameState.hovered) {
            frameState.hovered = hit;
            canvas.style.cursor = hit ? "pointer" : "default";
            drawOverlay();
        }
    });

    canvas.addEventListener("mouseleave", () => {
        frameState.hovered = null;
        drawOverlay();
    });

    canvas.addEventListener("click", (event) => {
        const [x, y] = toImageCoords(event);
        const hit = boxAt(x, y);
        if (hit) selectCrop(hit[BOX_I]);
    });

    document.getElementById("crop-prev").addEventListener("click", () => stepCrop(-1));
    document.getElementById("crop-next").addEventListener("click", () => stepCrop(1));

    const isolateBox = document.getElementById("crop-isolate");
    isolateBox.checked = cropIsolate;
    isolateBox.addEventListener("change", () => {
        cropIsolate = isolateBox.checked;
        // Redraw whatever is on show so the effect is immediate.
        if (frameState && frameState.selected) selectCrop(frameState.selected[BOX_I]);
    });

    const rawBox = document.getElementById("frame-raw");
    rawBox.checked = frameShowRaw;
    rawBox.addEventListener("change", () => {
        frameShowRaw = rawBox.checked;
        applyFrameImage();
    });
    if (frameShowRaw) applyFrameImage();

    const binSelect = document.getElementById("profile-bin");
    binSelect.value = String(profileBin);
    binSelect.addEventListener("change", () => {
        profileBin = parseFloat(binSelect.value) || 0;
        drawProfile();
    });
    const logBox = document.getElementById("profile-log");
    logBox.checked = profileLog;
    logBox.addEventListener("change", () => {
        profileLog = logBox.checked;
        drawProfile();
    });
    document.getElementById("profile-plot")
        .addEventListener("mousemove", onProfileHover);
    document.getElementById("profile-plot")
        .addEventListener("mouseleave", () => setReadout(null));

    loadProfile().then(drawProfile);
}

// The frame view is built from the crops the segmenter kept, so anything it
// discarded is white. The raw file is what the camera saw. Both are rendered
// at the same preview size, so the box overlay stays aligned across the swap.
let frameShowRaw = false;

async function applyFrameImage() {
    const img = document.getElementById("frame-image");
    const note = document.getElementById("frame-note");
    if (!img || !frameState) return;

    if (!frameShowRaw) {
        img.src = `data:image/png;base64,${frameState.reconImage}`;
        if (note) note.textContent = "";
        return;
    }

    if (frameState.rawImage === null) {
        if (note) note.textContent = "loading raw frame…";
        try {
            const response = await fetch(
                `/viewer/hdf-profile-viewer/raw_frame?file=${encodeURIComponent(filePath)}` +
                `&group=${encodeURIComponent(frameState.frameName)}`);
            const payload = await response.json();
            if (!response.ok) {
                // Usually the share is mounted elsewhere than when the file was
                // written. Fall back rather than showing a blank frame.
                if (note) note.textContent = payload.error || "raw frame unavailable";
                frameShowRaw = false;
                const box = document.getElementById("frame-raw");
                if (box) box.checked = false;
                return;
            }
            frameState.rawImage = payload.image;
        } catch (err) {
            console.error("Error loading raw frame:", err);
            if (note) note.textContent = "raw frame unavailable";
            frameShowRaw = false;
            const box = document.getElementById("frame-raw");
            if (box) box.checked = false;
            return;
        }
    }

    img.src = `data:image/png;base64,${frameState.rawImage}`;
    if (note) note.textContent = "";
}

function stepCrop(delta) {
    if (!frameState || !frameState.selected) return;
    const next = frameState.selected[BOX_I] + delta;
    if (next >= 0 && next < frameState.boxes.length) selectCrop(next);
}

async function selectCrop(index) {
    if (!frameState) return;
    const box = frameState.boxes[index];
    frameState.selected = box;
    drawOverlay();

    document.getElementById("crop-label").textContent =
        `object ${index + 1} of ${frameState.boxes.length}`;
    document.getElementById("crop-prev").disabled = index === 0;
    document.getElementById("crop-next").disabled = index === frameState.boxes.length - 1;
    document.getElementById("crop-meta").textContent =
        `${box[BOX_FW]} × ${box[BOX_FH]} px at full resolution`;

    const holder = document.getElementById("crop-holder");
    holder.textContent = "loading…";
    try {
        const response = await fetch(
            `/viewer/hdf-profile-viewer/show_crop?file=${encodeURIComponent(filePath)}` +
            `&dataset=${encodeURIComponent(frameState.dataset)}&element_index=${index}` +
            `&isolate=${cropIsolate ? 1 : 0}`);
        const payload = await response.json();
        holder.innerHTML = payload.html;
    } catch (err) {
        console.error("Error loading crop:", err);
        holder.textContent = "could not load this crop";
    }
}

// NEW FUNCTION: Triggered when a user clicks a matrix cell item
async function loadElementReconstruction(datasetPath, elementIndex) {
    showLoader();
    try {
        const response = await fetch(`/viewer/hdf-profile-viewer/show_crop?file=${encodeURIComponent(filePath)}&dataset=${encodeURIComponent(datasetPath)}&element_index=${elementIndex}`);
        const payload = await response.json();

        // Overwrite your view block container with the calculated image tag markup
        document.getElementById("dataset-preview").innerHTML = `
            <div style="margin-bottom: 15px;">
                <button onclick="loadDatasetData('${datasetPath}')" style="padding: 5px 10px; cursor:pointer;">← Return to Matrix Grid</button>
            </div>
            ${payload.html}
        `;
    } catch (err) {
        console.error("Error performing item calculations:", err);
    } finally {
        hideLoader();
    }
}

// --- Profile overview: objects against depth ------------------------------
// Frames carry their depth in their name, so the whole file can be summarised
// as a depth profile. Drawn on a canvas rather than pulled from a charting
// library, which keeps the viewer dependency-free.

let cropIsolate = false;  // show only the detected object in a crop
let profileData = null;   // as returned by /profile, for the current file
let profileBin = 0;       // dbar per bin; 0 draws one point per frame
let profilePlot = null;   // last drawn geometry, for hit-testing the hover
let profileLog = false;   // log count axis

async function loadProfile() {
    if (profileData) return profileData;
    try {
        const response = await fetch(
            `/viewer/hdf-profile-viewer/profile?file=${encodeURIComponent(filePath)}`);
        profileData = await response.json();
    } catch (err) {
        console.error("Error loading profile:", err);
        profileData = { points: [], with_depth: 0 };
    }
    return profileData;
}

// Bins sum the frames that fall in them, so a point is "objects seen between
// these depths"; unbinned, a point is one frame.
function binnedPoints() {
    if (!profileData) return [];
    const points = profileData.points.filter(p => p.depth !== null);
    if (!profileBin) {
        return points.map(p => ({ depth: p.depth, count: p.count, frames: 1, name: p.name }));
    }
    const bins = new Map();
    for (const p of points) {
        const key = Math.floor(p.depth / profileBin);
        const bin = bins.get(key) || { depth: (key + 0.5) * profileBin, count: 0, frames: 0 };
        bin.count += p.count;
        bin.frames += 1;
        bins.set(key, bin);
    }
    return [...bins.values()].sort((a, b) => a.depth - b.depth);
}

function setReadout(text) {
    const el = document.getElementById("profile-readout");
    if (!el) return;
    if (text) { el.textContent = text; return; }
    const n = profileData ? profileData.with_depth : 0;
    el.textContent = n
        ? `${n} frames with depth${profileBin ? `, binned to ${profileBin} dbar` : ""}`
        : "no depth in the frame names";
}

function drawProfile() {
    const canvas = document.getElementById("profile-plot");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    const style = getComputedStyle(document.body);
    const ink = style.color || "#333";

    const pts = binnedPoints();
    setReadout(null);
    if (!pts.length) {
        ctx.fillStyle = ink;
        ctx.font = "12px sans-serif";
        ctx.fillText("no depth information in this file", 12, 24);
        profilePlot = null;
        return;
    }

    const M = { l: 46, r: 12, t: 14, b: 30 };
    const plotW = W - M.l - M.r, plotH = H - M.t - M.b;

    const maxCount = Math.max(...pts.map(p => p.count)) || 1;
    const minDepth = Math.min(...pts.map(p => p.depth));
    const maxDepth = Math.max(...pts.map(p => p.depth));
    const depthSpan = (maxDepth - minDepth) || 1;

    // On the log axis a frame with no objects has no place, so it sits on
    // the left edge with the smallest count seen.
    const minPositive = Math.min(...pts.map(p => p.count).filter(c => c > 0));
    const logLo = Math.log10(Math.min(isFinite(minPositive) ? minPositive : 1, maxCount));
    const logSpan = (Math.log10(maxCount) - logLo) || 1;
    const x = profileLog
        ? c => M.l + ((Math.log10(Math.max(c, 10 ** logLo)) - logLo) / logSpan) * plotW
        : c => M.l + (c / maxCount) * plotW;

    // Depth increases downwards, the way a profile is read.
    const y = d => M.t + ((d - minDepth) / depthSpan) * plotH;

    ctx.strokeStyle = ink;
    ctx.globalAlpha = 0.35;
    ctx.beginPath();
    ctx.moveTo(M.l, M.t); ctx.lineTo(M.l, M.t + plotH); ctx.lineTo(M.l + plotW, M.t + plotH);
    ctx.stroke();
    ctx.globalAlpha = 1;

    ctx.fillStyle = ink;
    ctx.font = "10px sans-serif";
    ctx.textAlign = "right";
    for (let i = 0; i <= 4; i++) {
        const d = minDepth + (depthSpan * i) / 4;
        ctx.globalAlpha = 0.6;
        ctx.fillText(d.toFixed(2), M.l - 5, y(d) + 3);
        ctx.globalAlpha = 0.12;
        ctx.beginPath(); ctx.moveTo(M.l, y(d)); ctx.lineTo(M.l + plotW, y(d)); ctx.stroke();
    }
    ctx.globalAlpha = 0.6;
    ctx.textAlign = "center";
    const ticks = [];
    if (profileLog) {
        for (let e = Math.ceil(logLo); e <= Math.floor(Math.log10(maxCount)); e++) ticks.push(10 ** e);
        if (ticks.length < 2) ticks.unshift(10 ** logLo), ticks.push(maxCount);
    } else {
        for (let i = 0; i <= 2; i++) ticks.push((maxCount * i) / 2);
    }
    for (const c of ticks) ctx.fillText(String(Math.round(c)), x(c), H - 16);
    ctx.fillText(profileLog ? "objects (log)" : "objects", M.l + plotW / 2, H - 4);
    ctx.save();
    ctx.translate(11, M.t + plotH / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText("dbar", 0, 0);
    ctx.restore();
    ctx.globalAlpha = 1;

    ctx.strokeStyle = "#2563eb";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(x(p.count), y(p.depth)) : ctx.moveTo(x(p.count), y(p.depth))));
    ctx.stroke();

    ctx.fillStyle = "#2563eb";
    for (const p of pts) {
        ctx.beginPath();
        ctx.arc(x(p.count), y(p.depth), 2.5, 0, Math.PI * 2);
        ctx.fill();
    }

    // Mark where the frame on show sits in the profile.
    if (frameState && frameState.frameName) {
        const here = profileData.points.find(p => p.name === frameState.frameName);
        if (here && here.depth !== null) {
            ctx.strokeStyle = "#dc2626";
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(M.l, y(here.depth));
            ctx.lineTo(M.l + plotW, y(here.depth));
            ctx.stroke();
        }
    }

    profilePlot = { pts, x, y };
}

function onProfileHover(event) {
    if (!profilePlot) return;
    const canvas = event.currentTarget;
    const rect = canvas.getBoundingClientRect();
    const px = (event.clientX - rect.left) * (canvas.width / rect.width);
    const py = (event.clientY - rect.top) * (canvas.height / rect.height);

    let best = null, bestD = 14;
    for (const p of profilePlot.pts) {
        const d = Math.hypot(profilePlot.x(p.count) - px, profilePlot.y(p.depth) - py);
        if (d < bestD) { best = p; bestD = d; }
    }
    setReadout(best
        ? `${best.depth.toFixed(2)} dbar · ${best.count} objects` +
          (best.frames > 1 ? ` · ${best.frames} frames` : "")
        : null);
}

// --- Draggable split between the tree and the preview -------------------

const SPLIT_KEY = "hdfProfileViewer.treeWidth";

function setupSplitter() {
    const container = document.querySelector(".hdf-profile-viewer");
    const tree = container && container.querySelector(".hdf-tree");
    const splitter = document.getElementById("hdf-splitter");
    if (!container || !tree || !splitter) return;

    // localStorage is unavailable in some browser configurations, and a
    // remembered width is a convenience rather than something to fail over.
    const remember = (value) => {
        try {
            if (value === null) localStorage.removeItem(SPLIT_KEY);
            else localStorage.setItem(SPLIT_KEY, value);
        } catch (err) { /* not important enough to report */ }
    };
    try {
        const saved = localStorage.getItem(SPLIT_KEY);
        if (saved) tree.style.flexBasis = saved;
    } catch (err) { /* fall back to the stylesheet's width */ }

    // Keep both panes usable no matter where the pointer goes.
    const MIN_TREE = 120, MIN_PREVIEW = 260;

    let dragging = false;

    splitter.addEventListener("pointerdown", (event) => {
        dragging = true;
        splitter.setPointerCapture(event.pointerId);
        document.body.style.userSelect = "none";
        event.preventDefault();
    });

    splitter.addEventListener("pointermove", (event) => {
        if (!dragging) return;
        const rect = container.getBoundingClientRect();
        const limit = Math.max(MIN_TREE, rect.width - MIN_PREVIEW);
        const width = Math.min(limit, Math.max(MIN_TREE, event.clientX - rect.left));
        tree.style.flexBasis = width + "px";
    });

    const endDrag = (event) => {
        if (!dragging) return;
        dragging = false;
        try { splitter.releasePointerCapture(event.pointerId); } catch (err) { /* already released */ }
        document.body.style.userSelect = "";
        remember(tree.style.flexBasis);
    };
    splitter.addEventListener("pointerup", endDrag);
    splitter.addEventListener("pointercancel", endDrag);

    // Double-click hands the width back to the stylesheet.
    splitter.addEventListener("dblclick", () => {
        tree.style.flexBasis = "";
        remember(null);
    });
}

// Open straight onto one object, when an EcoTaxa object_link sent us here.
// Frames are addressed by their group; the crop datasets are what the tree
// clicks on, so we open the group's cropPixelData exactly as a click would.
async function openFocusTarget() {
    if (!focusGroup) return;
    const group = String(focusGroup).replace(/^\/+|\/+$/g, "");
    const dataset = `/${group}/cropPixelData`;

    showLoader();
    loadDatasetInfo(dataset);
    await loadDatasetData(dataset);

    if (!frameState) return;
    // Links carry the object number from the EcoTaxa object_id, which is
    // 1-based; selectCrop indexes from 0.
    const index = Number(focusIndex) - 1;
    if (Number.isInteger(index) && index >= 0 && index < frameState.boxes.length) {
        selectCrop(index);
    }
}

// Initialize workspace
setupSplitter();
setupTreeFilter();
loadTree();
openFocusTarget();
