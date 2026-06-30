
let currentPath = "";
let selectedEntry = null;

let contextPosition =
{
	x:0,
	y:0
};


async function loadDirectory(path="")
{
	currentPath = path;

    const response =
        await fetch(
            `/file-explorer/files?path=${encodeURIComponent(path)}`
        );

    const data = await response.json();

	updateBreadcrumbs();

    renderFiles(data);
}

function formatSize(bytes)
{
    if (bytes == null)
        return "";

    const units =
        ["B", "KB", "MB", "GB", "TB"];

    let i = 0;

    while (bytes >= 1024 && i < units.length - 1)
    {
        bytes /= 1024;
        i++;
    }

    return `${bytes.toFixed(1)} ${units[i]}`;
}


function updateBreadcrumbs()
{
    const container =
        document.getElementById(
            "breadcrumbs"
        );

    container.innerHTML = "";

    const parts =
        currentPath
            .split("/")
            .filter(Boolean);

    let accumulated = "";

    const root =
        document.createElement("span");

    root.textContent = "Root";

    root.onclick =
        () => loadDirectory("");

    container.appendChild(root);

    parts.forEach(part =>
    {
        accumulated +=
            (accumulated ? "/" : "")
            + part;

        container.appendChild(
            document.createTextNode(" / ")
        );

        const crumb =
            document.createElement("span");

        crumb.textContent = part;

        const path =
            accumulated;

        crumb.onclick =
            () => loadDirectory(path);

        container.appendChild(crumb);
    });
}


function updateInfo(entry)
{
    const panel =
        document.getElementById(
            "info-content"
        );


    panel.innerHTML = `

        <p>
            <b>Name:</b>
            ${entry.name}
        </p>

        <p>
            <b>Type:</b>
            ${entry.type}
        </p>


        <p>
            <b>Extension:</b>
            ${entry.extension ?? ""}
        </p>


        <p>
            <b>Size:</b>
            ${formatSize(entry.size)}
        </p>


        <p>
            <b>Modified:</b>
            ${new Date(
                entry.last_modified * 1000
            )}
        </p>

    `;
}

function formatDate(timestamp)
{
    if (!timestamp)
        return "";

    return new Date(timestamp * 1000)
        .toLocaleString(
            undefined,
            {
                year:"numeric",
                month:"short",
                day:"numeric",
                hour:"2-digit",
                minute:"2-digit"
            }
        );
}

function renderFiles(data)
{
    const tbody =
        document.querySelector(
            "#file-table tbody"
        );

    tbody.innerHTML = "";

	if (currentPath !== "")
	{
		const row = document.createElement("tr");

		row.innerHTML = `
			<td>📁 ..</td>
			<td></td>
			<td></td>
		`;

		row.addEventListener(
			"dblclick",
			() =>
			{
				const parent =
					currentPath
						.split("/")
						.slice(0, -1)
						.join("/");

				loadDirectory(parent);
			}
		);

		tbody.appendChild(row);
	}

	data.entries.forEach(entry =>
	{
		const row =
			document.createElement("tr");

		row.innerHTML = `
			<td>
				${entry.type === "directory" ? "📁" : "📄"}
				${entry.name}
			</td>
			<td>${formatSize(entry.size)}</td>
			<td>${formatDate(entry.last_modified) ?? ""}</td>

            
		`;

		row.addEventListener(
			"dblclick",
			() =>
			{
				if (entry.type === "directory")
				{
					const nextPath =
						currentPath
						? `${currentPath}/${entry.name}`
						: entry.name;

					loadDirectory(nextPath);
				}
			}
		);

		row.addEventListener(
			"click",
			() =>
			{
				document
				.querySelectorAll(
					"#file-table tr.selected"
				)
				.forEach(
					r =>
					r.classList.remove("selected")
				);


				row.classList.add(
					"selected"
				);


				selectedEntry = entry;


				updateInfo(entry);
			}
		);

		document.addEventListener(
			"click",
			() =>
			{
				hideContextMenu();
				hideViewerMenu();
			}
		);

		row.addEventListener(
			"contextmenu",
			(event) =>
			{

				event.preventDefault();

				document
				.querySelectorAll("#file-table tr.selected")
				.forEach(
					r => r.classList.remove("selected")
				);

				row.classList.add("selected");

				selectedEntry = entry;

				contextPosition.x = event.pageX;
                contextPosition.y = event.pageY;

				showContextMenu(
					event.pageX,
					event.pageY
				);


			}
		);

		tbody.appendChild(row);
	});
}

document.addEventListener(
    "DOMContentLoaded",
    () => loadDirectory("")
);


function showContextMenu(x, y)
{
    const menu =
        document.getElementById(
            "context-menu"
        );


    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;

    menu.style.display = "block";
}



function hideContextMenu()
{
    document
        .getElementById(
            "context-menu"
        )
        .style.display = "none";
}


function executeAction(action)
{
    switch(action)
    {
        case "open":
            showViewerMenu(selectedEntry);
            break;


        case "download":
            downloadFile(selectedEntry);
            break;


        case "copy-path":
            copyPath(selectedEntry);
            break;
    }
}


document
.getElementById("context-menu")
.addEventListener(
    "click",
    (event) =>
    {
        const action =
            event.target.dataset.action;


		executeAction(action);


        hideContextMenu();
    }
);

async function copyPath(entry)
{
    await navigator.clipboard.writeText(
        entry.path
    );
}

function downloadFile(entry)
{
    window.location.href =
        `/file-explorer/download?path=${encodeURIComponent(entry.path)}`;
}


async function showViewerMenu(entry)
{
    const menu =
        document.getElementById(
            "viewer-menu"
        );


    menu.innerHTML = "";


    const response =
        await fetch(
            `/file-explorer/find_viewers?path=${encodeURIComponent(entry.path)}`
        );


    const viewers =
        await response.json();


    if(viewers.length === 0)
    {
        menu.innerHTML =
            "<div>No viewer available</div>";
    }


    viewers.forEach(viewer =>
    {
        const item =
            document.createElement(
                "div"
            );


        item.textContent =
            viewer.name;


        item.addEventListener(
            "click",
            () =>
            {
                openViewer(
                    viewer,
                    entry.path
                );

                hideViewerMenu();
            }
        );


        menu.appendChild(
            item
        );
    });


    menu.style.left =
        contextPosition.x + "px";


    menu.style.top =
        contextPosition.y + "px";


    menu.style.display =
        "block";
}


function hideViewerMenu()
{
	document.getElementById(
		"viewer-menu"
	).style.display = "none";
}


function openViewer(viewer,path)
{
    window.location.href =
        `${viewer.url}?path=`
        +
        encodeURIComponent(path);
}
