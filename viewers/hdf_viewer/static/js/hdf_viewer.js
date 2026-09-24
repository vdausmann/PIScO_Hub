async function loadTree()
{
    const response =
        await fetch(
            `/viewer/hdf-viewer/tree?path=${encodeURIComponent(filePath)}`
        );

    const tree =
        await response.json();

    renderTree(tree);
}

function renderTree(node)
{
    const root =
        document.getElementById(
            "tree-root"
        );

    root.innerHTML = "";

    renderNode(
        root,
        node
    );
}

function renderNode(parent, node)
{
    const li =
        document.createElement("li");

    li.textContent =
        node.name.split("/").pop() || "/";

    parent.appendChild(li);

    if(node.type === "dataset")
    {
        li.addEventListener(
            "click",
            () =>
            {
                loadDatasetInfo(
                    node.name
                );

                loadDatasetData(
                    node.name
                );
            }
        );
    }

    if(node.children)
    {
        const ul =
            document.createElement("ul");

        li.appendChild(ul);

        node.children.forEach(
            child =>
            renderNode(
                ul,
                child
            )
        );
    }
}

async function loadDatasetInfo(datasetPath)
{
    const response =
        await fetch(
            `/viewer/hdf-viewer/dataset_info?` +
            `file=${encodeURIComponent(filePath)}` +
            `&dataset=${encodeURIComponent(datasetPath)}`
        );

    const info =
        await response.json();

    document
    .getElementById(
        "dataset-info"
    )
    .innerHTML = `
        <p>
            <b>Group Attributes:</b>
            ${info.group_attrs}
        </p>

        <p>
            <b>Dataset Attributes:</b>
            ${info.dataset_attrs}
        </p>

        <p>
            <b>Name:</b>
            ${info.name}
        </p>

        <p>
            <b>Shape:</b>
            ${info.shape.join(" × ")}
        </p>

        <p>
            <b>Type:</b>
            ${info.dtype}
        </p>

        <p>
            <b>Elements:</b>
            ${info.size}
        </p>
    `;
}

let currentFilePath = "";
let currentDatasetPath = "";
let currentFrame = 0;
let totalFrames = 1;

function renderDataset(response, fileParam, datasetParam) {
    // Save reference context for pagination requests when passed from tree click
    if (fileParam) currentFilePath = fileParam;
    if (datasetParam) currentDatasetPath = datasetParam;
    
    const loader = document.getElementById('global-loader');
    if (loader) loader.style.display = 'flex';

    setTimeout(() => {
        const container = document.getElementById("dataset-preview");
        container.innerHTML = "";

        // Check if dataset is 3D based on its shape array
        const is3D = response.shape && response.shape.length === 3;
        if (is3D) {
            totalFrames = response.shape[0];
            currentFrame = response.current_frame !== undefined ? response.current_frame : 0;
        }

        if (response.image) {
            // If 3D, build navigation toolbar container
            if (is3D) {
                const navBar = document.createElement("div");
                navBar.className = "frame-nav-bar";
                
                const prevBtn = document.createElement("button");
                prevBtn.className = "nav-btn";
                prevBtn.textContent = "◀ Prev";
                prevBtn.disabled = currentFrame <= 0;
                prevBtn.onclick = () => loadFrame(currentFrame - 1);

                const indicator = document.createElement("span");
                indicator.className = "frame-indicator";
                indicator.textContent = `Frame ${currentFrame + 1} / ${totalFrames}`;

                const nextBtn = document.createElement("button");
                nextBtn.className = "nav-btn";
                nextBtn.textContent = "Next ▶";
                nextBtn.disabled = currentFrame >= totalFrames - 1;
                nextBtn.onclick = () => loadFrame(currentFrame + 1);

                navBar.appendChild(prevBtn);
                navBar.appendChild(indicator);
                navBar.appendChild(nextBtn);
                container.appendChild(navBar);
            }

            // Image display element
            const img = document.createElement("img");
            img.src = `data:image/png;base64,${response.image}`;
            img.alt = "Dataset Image Preview";
            img.className = "dataset-img";
            container.appendChild(img);

            // Clear out min/max fields for images
            const minElem = document.getElementById("min-value");
            const maxElem = document.getElementById("max-value");
            if (minElem) minElem.textContent = "-";
            if (maxElem) maxElem.textContent = "-";
        } 
        else if (response.data) {
            let values = response.data;

            if (!Array.isArray(values)) {
                values = [[values]];
            } else if (values.length > 0 && !Array.isArray(values[0])) {
                values = [values];
            }

            const flat = values.flat();
            let min = Infinity;
            let max = -Infinity;
            for (let i = 0; i < flat.length; i++) {
                if (flat[i] < min) min = flat[i];
                if (flat[i] > max) max = flat[i];
            }

            const minElem = document.getElementById("min-value");
            const maxElem = document.getElementById("max-value");
            if (minElem) minElem.textContent = min;
            if (maxElem) maxElem.textContent = max;

            const fragment = document.createDocumentFragment();
            values.forEach(row => {
                const rowDiv = document.createElement("div");
                rowDiv.className = "data-row";

                row.forEach(value => {
                    const cell = document.createElement("div");
                    cell.className = "data-cell";
                    cell.textContent = value;
                    cell.style.backgroundColor = getColor(value, min, max);
                    rowDiv.appendChild(cell);
                });
                fragment.appendChild(rowDiv);
            });
            container.appendChild(fragment);
        }

        if (loader) loader.style.display = 'none';
    }, 50);
}

// Function to fetch a specific frame index via AJAX when clicking buttons
function loadFrame(frameIndex) {
    if (frameIndex < 0 || frameIndex >= totalFrames) return;
    
    const loader = document.getElementById('global-loader');
    if (loader) loader.style.display = 'flex';

    fetch(`/viewer/hdf-viewer/dataset_data?file=${encodeURIComponent(currentFilePath)}&dataset=${encodeURIComponent(currentDatasetPath)}&frame=${frameIndex}`)
        .then(res => res.json())
        .then(data => {
            renderDataset(data);
        })
        .catch(err => {
            console.error("Failed to load frame:", err);
            if (loader) loader.style.display = 'none';
        });
}

console.log("HDF viewer loaded");
console.log("File:", filePath);

function getColor(value, min, max)
{
    if (max === min)
    {
        return "rgb(128,128,128)";
    }

    let t =
        (value - min) /
        (max - min);

    t = Math.max(
        0,
        Math.min(
            1,
            t
        )
    );

    const stops = [
        [68, 1, 84],
        [59, 82, 139],
        [33, 145, 140],
        [94, 201, 98],
        [253, 231, 37]
    ];

    const scaled =
        t * (stops.length - 1);

    const i =
        Math.floor(
            scaled
        );

    const f =
        scaled - i;

    const c1 =
        stops[i];

    const c2 =
        stops[
            Math.min(
                i + 1,
                stops.length - 1
            )
        ];

    const r =
        Math.floor(
            c1[0] +
            f * (c2[0] - c1[0])
        );

    const g =
        Math.floor(
            c1[1] +
            f * (c2[1] - c1[1])
        );

    const b =
        Math.floor(
            c1[2] +
            f * (c2[2] - c1[2])
        );

    return `rgb(${r},${g},${b})`;
}

async function loadDatasetData(datasetPath)
{
    const response =
        await fetch(
            `/viewer/hdf-viewer/dataset_data?` +
            `file=${encodeURIComponent(filePath)}` +
            `&dataset=${encodeURIComponent(datasetPath)}`
        );

    const data =
        await response.json();

    // Pass the active file path and dataset path so currentFilePath/currentDatasetPath are populated
    renderDataset(
        data,
        filePath,
        datasetPath
    );
}

loadTree();
