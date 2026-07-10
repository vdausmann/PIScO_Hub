
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

function renderDataset(response)
{
    // 1. Manually force the loader to display immediately
    const loader = document.getElementById('global-loader');
    if (loader) loader.style.display = 'flex';

    // 2. Use setTimeout to yield control to the browser.
    // This gives the browser a frame to actually render the loader overlay on screen.
    setTimeout(() => {
        const container = document.getElementById("dataset-preview");
        container.innerHTML = "";

        let values = response.data;

        // Convert scalar -> [[scalar]]
        if (!Array.isArray(values)) {
            values = [[values]];
        }
        // Convert 1D -> [1D]
        else if (values.length > 0 && !Array.isArray(values[0])) {
            values = [values];
        }

        const flat = values.flat();

        // NOTE: Math.min(...flat) will crash with a Call Stack Max Range Error 
        // if the dataset has > 100,000 elements. Using a basic loop is safer and faster.
        let min = Infinity;
        let max = -Infinity;
        for (let i = 0; i < flat.length; i++) {
            if (flat[i] < min) min = flat[i];
            if (flat[i] > max) max = flat[i];
        }

        document.getElementById("min-value").textContent = min;
        document.getElementById("max-value").textContent = max;

        // Optimize DOM manipulation by using a Fragment instead of appending one by one
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

        // Batch append everything at once to minimize browser reflow layout hits
        container.appendChild(fragment);

        // 3. Hide the loader now that all heavy elements have been drawn
        if (loader) loader.style.display = 'none';
    }, 50); // Small 50ms delay gives the UI thread breathing room to draw the loader
}




console.log(
    "HDF viewer loaded"
);


console.log(
    "File:",
    filePath
);

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


    renderDataset(
        data
    );
}


loadTree();
