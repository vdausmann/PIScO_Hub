
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
    const container =
        document.getElementById(
            "dataset-preview"
        );

    container.innerHTML = "";

    let values =
        response.data;

    // Convert scalar -> [[scalar]]
    if (!Array.isArray(values))
    {
        values = [[values]];
    }

    // Convert 1D -> [1D]
    else if (
        values.length > 0 &&
        !Array.isArray(values[0])
    )
    {
        values = [values];
    }

    const flat =
        values.flat();

    const min =
        Math.min(...flat);

    const max =
        Math.max(...flat);

    document.getElementById(
        "min-value"
    ).textContent = min;

    document.getElementById(
        "max-value"
    ).textContent = max;

    values.forEach(row =>
    {
        const rowDiv =
            document.createElement(
                "div"
            );

        rowDiv.className =
            "data-row";

        row.forEach(value =>
        {
            const cell =
                document.createElement(
                    "div"
                );

            cell.className =
                "data-cell";

            cell.textContent =
                value;

            cell.style.backgroundColor =
                getColor(
                    value,
                    min,
                    max
                );

            rowDiv.appendChild(
                cell
            );
        });

        container.appendChild(
            rowDiv
        );
    });
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
