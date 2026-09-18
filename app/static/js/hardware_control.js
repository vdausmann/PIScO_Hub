const hardwareControl =
    document.getElementById("hardware-control");

const hardwareConfig =
    JSON.parse(
        hardwareControl.dataset.config
    );

const filesUrl =
    hardwareControl.dataset.filesUrl;

const sensorDataUrl =
    hardwareControl.dataset.sensorDataUrl;


const fileSelect =
    document.getElementById("file-select");

const sensorsElement =
    document.getElementById("sensors");

const lastUpdateElement =
    document.getElementById("last-update");

let polling = false;
let pollingTimeout = null;


/*
 * Create the sensor layout from the hardware
 * configuration.
 */
function createSensorLayout() {

    sensorsElement.innerHTML = "";

    for (
        const [deviceName, device]
        of Object.entries(hardwareConfig)
    ) {

        const deviceSection =
            document.createElement("section");

        deviceSection.className =
            "device-section";


        const deviceTitle =
            document.createElement("h2");

        deviceTitle.className =
            "device-title";

        deviceTitle.textContent =
            deviceName;


        const sensorGrid =
            document.createElement("div");

        sensorGrid.className =
            "sensor-grid";


        for (const sensor of device.sensors) {

            const sensorElement =
                document.createElement("div");

            sensorElement.className =
                "sensor-entry";

            sensorElement.dataset.shorthand =
                sensor.shorthand;


            const headerElement =
                document.createElement("div");

            headerElement.className =
                "sensor-header";


            const nameElement =
                document.createElement("span");

            nameElement.className =
                "sensor-name";

            nameElement.textContent =
                sensor.name;


            const statusElement =
                document.createElement("span");

            statusElement.className =
                "sensor-status";


            const valueElement =
                document.createElement("span");

            valueElement.className =
                "sensor-value";

            valueElement.textContent =
                "N/A";


            headerElement.appendChild(
                nameElement
            );

            headerElement.appendChild(
                statusElement
            );

            sensorElement.appendChild(
                headerElement
            );

            sensorElement.appendChild(
                valueElement
            );

            sensorGrid.appendChild(
                sensorElement
            );
        }


        deviceSection.appendChild(
            deviceTitle
        );

        deviceSection.appendChild(
            sensorGrid
        );

        sensorsElement.appendChild(
            deviceSection
        );
    }
}


/*
 * Update the existing sensor elements with
 * the values returned by the backend.
 *
 * The backend returns:
 *
 * {
 *     "sensor-name": {
 *         "value": ...,
 *         "text": "...",
 *         "valid": true,
 *         "device_name": "...",
 *         "color": "green"
 *     }
 * }
 */
function displaySensorData(data) {

    for (
        const [name, display]
        of Object.entries(data)
    ) {

        const sensorElement =
            document.querySelector(
                `.sensor-entry[data-shorthand="${CSS.escape(name)}"]`
            );

        if (!sensorElement) {
            continue;
        }


        const valueElement =
            sensorElement.querySelector(
                ".sensor-value"
            );

        const statusElement =
            sensorElement.querySelector(
                ".sensor-status"
            );


        /*
         * Update the displayed value.
         */
        valueElement.textContent =
            display.text;


        /*
         * Reset the LED.
         */
        statusElement.className =
            "sensor-status";

        statusElement.style.display =
            "none";


        /*
         * Show the LED if the backend
         * provides a color.
         */
        if (display.color) {

            statusElement.style.display =
                "block";

            statusElement.classList.add(
                `sensor-status-${display.color}`
            );
        }
    }
}


/*
 * Read the currently selected sensor file
 * and update the displayed values.
 */
async function updateSensorData() {

    const file =
        fileSelect.value;

    if (!file) {
        return;
    }


    try {

        const response =
            await fetch(
                sensorDataUrl
                + "?file="
                + encodeURIComponent(file),
                {
                    showLoader: false
                }
            );


        if (!response.ok) {
            throw new Error(
                `HTTP ${response.status}`
            );
        }


        const data =
            await response.json();


        displaySensorData(data);

		const now =
            new Date();

        lastUpdateElement.textContent =
            "Last update: "
            + now.toLocaleTimeString()

    } catch (error) {

        console.error(
            "Failed to update sensor data:",
            error
        );
    }
}


/*
 * Start polling the selected file once per
 * second.
 *
 * The next request is scheduled only after
 * the previous request has finished.
 */
function startPolling() {

    if (polling) {
        return;
    }

    polling = true;


    async function updateLoop() {

        if (!polling) {
            return;
        }


        await updateSensorData();


        if (!polling) {
            return;
        }


        pollingTimeout =
            setTimeout(
                updateLoop,
                1000
            );
    }


    updateLoop();
}


/*
 * Stop polling.
 */
function stopPolling() {

    polling = false;


    if (pollingTimeout !== null) {

        clearTimeout(
            pollingTimeout
        );

        pollingTimeout = null;
    }
}


/*
 * Reset all sensor displays.
 */
function resetSensorDisplays() {

    for (
        const valueElement
        of document.querySelectorAll(
            ".sensor-value"
        )
    ) {

        valueElement.textContent =
            "N/A";
    }


    for (
        const statusElement
        of document.querySelectorAll(
            ".sensor-status"
        )
    ) {

        statusElement.className =
            "sensor-status";

        statusElement.style.display =
            "none";
    }
}


/*
 * Load all available sensor files.
 */
async function loadFiles() {

    try {

        const response =
            await fetch(
                filesUrl
            );


        if (!response.ok) {
            throw new Error(
                `HTTP ${response.status}`
            );
        }


        const files =
            await response.json();


        for (const file of files) {

            const option =
                document.createElement("option");

            option.value =
                file;

            option.textContent =
                file;

            fileSelect.appendChild(
                option
            );
        }

    } catch (error) {

        console.error(
            "Failed to load sensor files:",
            error
        );
    }
}


/*
 * Handle selecting a different sensor file.
 */
fileSelect.addEventListener(
    "change",
    async () => {

        /*
         * Stop polling the previous file.
         */
        stopPolling();


        /*
         * Reset the displayed values.
         */
        resetSensorDisplays();


        /*
         * Nothing to do if no file is selected.
         */
        if (!fileSelect.value) {
            return;
        }


        /*
         * Immediately load the selected file.
         */
        await updateSensorData();


        /*
         * Continue updating it every second.
         */
        startPolling();
    }
);


/*
 * Initialize the page.
 */
async function initialize() {

    /*
     * Create the sensor layout first.
     */
    createSensorLayout();


    /*
     * Load the available files into the
     * selection box.
     */
    await loadFiles();
}


initialize();
