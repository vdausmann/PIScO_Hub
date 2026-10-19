// HDF viewer: file tree on the left, the selected dataset in the middle (an
// image, or a grid of values), its shape, type and attributes on the right.

const BASE = "/viewer/hdf-viewer";

let currentFilePath = filePath;
let currentDatasetPath = "";
let currentFrame = 0;
let totalFrames = 1;
let activeItem = null;

// ---- file tree ---------------------------------------------------------------

async function loadTree() {
    const response = await fetch(`${BASE}/tree?path=${encodeURIComponent(filePath)}`);
    const tree = await response.json();
    const root = document.getElementById("tree-root");
    root.innerHTML = "";
    renderNode(root, tree);
}

function renderNode(parent, node) {
    const li = document.createElement("li");
    li.textContent = node.name.split("/").pop() || "/";
    parent.appendChild(li);

    if (node.type === "dataset") {
        li.className = "dataset";
        li.addEventListener("click", (e) => {
            e.stopPropagation();
            if (activeItem) activeItem.classList.remove("active");
            activeItem = li;
            li.classList.add("active");
            currentDatasetPath = node.name;
            currentFrame = 0;
            loadDatasetInfo(node.name);
            loadFrame(0);
        });
    }

    if (node.children && node.children.length) {
        const ul = document.createElement("ul");
        li.appendChild(ul);
        node.children.forEach(child => renderNode(ul, child));
    }
}

// ---- dataset information -------------------------------------------------------

async function loadDatasetInfo(datasetPath) {
    const response = await fetch(`${BASE}/dataset_info?file=${encodeURIComponent(currentFilePath)}` +
                                 `&dataset=${encodeURIComponent(datasetPath)}`);
    const info = await response.json();
    const box = document.getElementById("dataset-info");
    box.innerHTML = "";

    const dl = document.createElement("dl");
    for (const [label, value] of [["Name", info.name], ["Shape", (info.shape || []).join(" × ")],
                                  ["Type", info.dtype], ["Elements", info.size]]) {
        const dt = document.createElement("dt");
        dt.textContent = label;
        const dd = document.createElement("dd");
        dd.textContent = value;
        dl.append(dt, dd);
    }
    box.appendChild(dl);

    // Attribute lists can run to pages; folded, the dataset's own open.
    for (const [label, text, open] of [["Dataset attributes", info.dataset_attrs, true],
                                       ["Group attributes", info.group_attrs, false]]) {
        if (!text) continue;
        const details = document.createElement("details");
        details.open = open;
        const summary = document.createElement("summary");
        summary.textContent = label;
        const pre = document.createElement("pre");
        pre.textContent = text;
        details.append(summary, pre);
        box.appendChild(details);
    }
}

// ---- preview -------------------------------------------------------------------

// The image is asked for at the size it is shown, so it is sharp but not
// larger than the screen needs.
function previewSize() {
    const box = document.getElementById("dataset-preview");
    const px = Math.max(box.clientWidth, box.clientHeight) * (window.devicePixelRatio || 1);
    return Math.round(Math.min(2048, Math.max(256, px)));
}

function loadFrame(frameIndex) {
    if (!currentDatasetPath) return;
    if (frameIndex < 0 || frameIndex >= totalFrames) frameIndex = Math.max(0, Math.min(frameIndex, totalFrames - 1));
    const loader = document.getElementById("global-loader");
    if (loader) loader.style.display = "flex";

    fetch(`${BASE}/dataset_data?file=${encodeURIComponent(currentFilePath)}` +
          `&dataset=${encodeURIComponent(currentDatasetPath)}&frame=${frameIndex}&size=${previewSize()}`)
        .then(res => res.json())
        .then(data => renderDataset(data))
        .catch(err => console.error("Failed to load frame:", err))
        .finally(() => { if (loader) loader.style.display = "none"; });
}

function renderDataset(response) {
    const container = document.getElementById("dataset-preview");
    const nav = document.getElementById("frame-nav");
    const colorbar = document.getElementById("colorbar-container");
    container.innerHTML = "";
    nav.innerHTML = "";

    if (response.error) {
        container.classList.remove("values");
        container.innerHTML = `<p class="hdf-hint"></p>`;
        container.firstChild.textContent = response.error;
        return;
    }

    const series = response.image && response.shape && response.shape.length === 3;
    totalFrames = series ? response.shape[0] : 1;
    currentFrame = series ? (response.current_frame || 0) : 0;

    if (response.image) {
        container.classList.remove("values");
        colorbar.hidden = true;
        if (series && totalFrames > 1) nav.appendChild(frameNav());

        const img = document.createElement("img");
        img.src = `data:image/png;base64,${response.image}`;
        img.alt = "Dataset image";
        img.className = "dataset-img";
        container.appendChild(img);
        return;
    }

    if (response.data) {
        container.classList.add("values");
        let values = response.data;
        if (!Array.isArray(values)) values = [[values]];
        else if (values.length > 0 && !Array.isArray(values[0])) values = [values];

        const flat = values.flat().filter(v => typeof v === "number");
        const min = flat.length ? Math.min(...flat) : 0;
        const max = flat.length ? Math.max(...flat) : 0;
        colorbar.hidden = !flat.length;
        document.getElementById("min-value").textContent = flat.length ? min : "";
        document.getElementById("max-value").textContent = flat.length ? max : "";

        const fragment = document.createDocumentFragment();
        values.forEach(row => {
            const rowDiv = document.createElement("div");
            rowDiv.className = "data-row";
            row.forEach(value => {
                const cell = document.createElement("div");
                cell.className = "data-cell";
                cell.textContent = value;
                cell.style.backgroundColor = typeof value === "number" ? getColor(value, min, max) : "#555";
                rowDiv.appendChild(cell);
            });
            fragment.appendChild(rowDiv);
        });
        container.appendChild(fragment);
    }
}

// Prev / slider / next for a frame series. The slider shows the frame it
// is over while dragging and loads it on release.
function frameNav() {
    const bar = document.createElement("div");
    bar.className = "frame-nav-bar";

    const prev = document.createElement("button");
    prev.className = "nav-btn";
    prev.textContent = "◀ Prev";
    prev.disabled = currentFrame <= 0;
    prev.onclick = () => loadFrame(currentFrame - 1);

    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = 0;
    slider.max = totalFrames - 1;
    slider.value = currentFrame;

    const indicator = document.createElement("span");
    indicator.className = "frame-indicator";
    const label = (i) => `Frame ${i + 1} / ${totalFrames}`;
    indicator.textContent = label(currentFrame);
    slider.oninput = () => { indicator.textContent = label(+slider.value); };
    slider.onchange = () => loadFrame(+slider.value);

    const next = document.createElement("button");
    next.className = "nav-btn";
    next.textContent = "Next ▶";
    next.disabled = currentFrame >= totalFrames - 1;
    next.onclick = () => loadFrame(currentFrame + 1);

    bar.append(prev, slider, indicator, next);
    return bar;
}

// Arrow keys step through frames, unless typing somewhere.
document.addEventListener("keydown", (e) => {
    if (totalFrames <= 1 || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName) && e.target.type !== "range") return;
    if (e.key === "ArrowLeft" && currentFrame > 0) { e.preventDefault(); loadFrame(currentFrame - 1); }
    if (e.key === "ArrowRight" && currentFrame < totalFrames - 1) { e.preventDefault(); loadFrame(currentFrame + 1); }
});

function getColor(value, min, max) {
    if (max === min) return "rgb(128,128,128)";
    let t = Math.max(0, Math.min(1, (value - min) / (max - min)));
    const stops = [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]];
    const scaled = t * (stops.length - 1);
    const i = Math.floor(scaled);
    const f = scaled - i;
    const c1 = stops[i];
    const c2 = stops[Math.min(i + 1, stops.length - 1)];
    const mix = (k) => Math.floor(c1[k] + f * (c2[k] - c1[k]));
    return `rgb(${mix(0)},${mix(1)},${mix(2)})`;
}

loadTree();
