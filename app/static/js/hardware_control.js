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

const alarmOverlay =
    document.getElementById("alarm-overlay");

const alarmMessage =
    document.getElementById("alarm-message");

const alarmAcknowledge =
    document.getElementById("alarm-acknowledge");

let displayedAlarmIds = new Set();

const acknowledgedAlarms =
    new Set();

const alarmBoard =
    document.getElementById("alarm-board");

const alarmList =
    document.getElementById("alarm-list");

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

		if (display.valid) {
			sensorElement.classList.remove("sensor-invalid");
		} else {
			sensorElement.classList.add("sensor-invalid");
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

		updateAlarms(data);

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

function resetAlarms() {

    acknowledgedAlarms.clear();

    displayedAlarmIds.clear();

    alarmList.innerHTML = "";

    alarmBoard.style.display =
        "none";

    alarmMessage.innerHTML = "";

    alarmOverlay.style.display =
        "none";
}

/*
 * Start polling the selected file once per
 * second.
 *
 * The next request is scheduled only after
 * the previous request has finished.
 */
function startPolling() {

    stopPolling();

    resetAlarms();

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

function updateAlarms(data) {

    const activeAlarms =
        new Map();


    /*
     * Find all currently active alarms.
     */
    for (
        const [name, display]
        of Object.entries(data)
    ) {

        if (!display.alarm) {
            continue;
        }


        const alarmId =
            display.device_name
            + ":"
            + name;


        activeAlarms.set(
            alarmId,
            {
                deviceName:
                    display.device_name,

                sensorName:
                    name,

				text:
					display.text

            }
        );
    }


    /*
     * Remove alarms that are no longer active
     * from the acknowledged set.
     */
    for (
        const alarmId
        of acknowledgedAlarms
    ) {

        if (!activeAlarms.has(alarmId)) {

            acknowledgedAlarms.delete(
                alarmId
            );
        }
    }


    /*
     * Update the persistent alarm board.
     *
     * This contains EVERY active alarm,
     * including acknowledged alarms.
     */
    alarmList.innerHTML = "";


    for (
        const [alarmId, alarm]
        of activeAlarms
    ) {

        const alarmElement =
            document.createElement("div");

        alarmElement.className =
            "alarm-entry";

        alarmElement.textContent =
            alarm.deviceName
            + ": "
            + alarm.sensorName;

        alarmList.appendChild(
            alarmElement
        );
    }


    /*
     * Hide the board if there are no active
     * alarms.
     */
    if (activeAlarms.size === 0) {

        alarmBoard.style.display =
            "none";

    } else {

        alarmBoard.style.display =
            "block";
    }


    /*
     * Find active alarms that have NOT been
     * acknowledged yet.
     */
    const unacknowledgedAlarms = [];


    for (
        const [alarmId, alarm]
        of activeAlarms
    ) {

        if (
            !acknowledgedAlarms.has(
                alarmId
            )
        ) {

            unacknowledgedAlarms.push(
                [alarmId, alarm]
            );
        }
    }


    /*
     * No new alarms.
     */
    if (
        unacknowledgedAlarms.length === 0
    ) {
        return;
    }


    /*
     * Remember which alarms are shown
     * in the popup.
     */
    displayedAlarmIds =
        new Set(
            unacknowledgedAlarms.map(
                ([alarmId]) => alarmId
            )
        );


    /*
     * Build popup message.
     */
    alarmMessage.innerHTML =
        unacknowledgedAlarms
            .map(
                ([alarmId, alarm]) =>
                    `<div>
						Device: ${alarm.deviceName}<br>
						Sensor: ${alarm.sensorName}<br>
						Value: ${alarm.text}<br>
						<br>
                    </div>`
            )
            .join("");


    /*
     * Show the modal.
     */
    alarmOverlay.style.display =
        "flex";
}

alarmAcknowledge.addEventListener(
    "click",
    () => {

        /*
         * Mark all alarms currently shown
         * in the popup as acknowledged.
         */
        for (
            const alarmId
            of displayedAlarmIds
        ) {

            acknowledgedAlarms.add(
                alarmId
            );
        }


        displayedAlarmIds =
            new Set();


        alarmOverlay.style.display =
            "none";
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
