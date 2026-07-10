// --- Global View State Tracking Elements ---
function showLoader() {
	const globalLoader = document.getElementById('global-loader');
	if (globalLoader) globalLoader.style.display = 'flex'; 
}
function hideLoader() { 
	const globalLoader = document.getElementById('global-loader');
	if (globalLoader) globalLoader.style.display = 'none'; 
}

async function loadTree() {
    const response = await fetch(`/viewer/hdf-profile-viewer/tree?path=${encodeURIComponent(filePath)}`);
    const tree = await response.json();
    renderTree(tree);
}

function renderTree(node) {
    const root = document.getElementById("tree-root");
    root.innerHTML = "";
    renderNode(root, node);
}

function renderNode(parent, node) {
    const li = document.createElement("li");
    li.textContent = node.name.split("/").pop() || "/";
    parent.appendChild(li);

    if (node.type === "dataset") {
        li.style.cursor = "pointer";
        li.addEventListener("click", () => {
            showLoader();
            // Fire both layout components
            loadDatasetInfo(node.name);
            loadDatasetData(node.name);
        });
    }

    if (node.children) {
        const ul = document.createElement("ul");
        li.appendChild(ul);
        node.children.forEach(child => renderNode(ul, child));
    }
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

        // Inject the pre-built HTML layout directly from the server
        document.getElementById("dataset-preview").innerHTML = payload.html;
    } catch (err) {
        console.error("Error drawing workspace component:", err);
    } finally {
        hideLoader();
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

// Initialize workspace
loadTree();
