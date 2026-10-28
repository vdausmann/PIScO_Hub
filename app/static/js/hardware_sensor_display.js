const hardwareControl =
    document.getElementById("hardware-control");

const hardwareConfig =
    JSON.parse(
        hardwareControl.dataset.config
    );

const statusUrl =
    hardwareControl.dataset.statusUrl;

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
 * One sensor entry: name, status LED and value.
 */
function createSensorElement(shorthand, name) {

    const sensorElement =
        document.createElement("div");

    sensorElement.className =
        "sensor-entry";

    sensorElement.dataset.shorthand =
        shorthand;

    // Hidden until a value arrives.
    sensorElement.hidden =
        true;


    const headerElement =
        document.createElement("div");

    headerElement.className =
        "sensor-header";


    const nameElement =
        document.createElement("span");

    nameElement.className =
        "sensor-name";

    nameElement.textContent =
        name;


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

    return sensorElement;
}


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


        /*
         * Connection state of the controller(s)
         * feeding this device.
         */
        const deviceState =
            document.createElement("span");

        deviceState.className =
            "device-state";

        deviceState.dataset.device =
            deviceName;

        deviceTitle.appendChild(
            deviceState
        );


        const sensorGrid =
            document.createElement("div");

        sensorGrid.className =
            "sensor-grid";


        sensorGrid.dataset.device =
            deviceName;

        for (const sensor of device.sensors || []) {

            sensorGrid.appendChild(
                createSensorElement(
                    sensor.shorthand,
                    sensor.name
                )
            );
        }


        /*
         * Shown while the device has no values.
         */
        const emptyElement =
            document.createElement("p");

        emptyElement.className =
            "device-empty";

        emptyElement.textContent =
            "No values.";

        emptyElement.hidden =
            true;


        deviceSection.appendChild(
            deviceTitle
        );

        deviceSection.appendChild(
            sensorGrid
        );

        deviceSection.appendChild(
            emptyElement
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

        let sensorElement =
            document.querySelector(
                `.sensor-entry[data-shorthand="${CSS.escape(name)}"]`
            );

        /*
         * A field the controller sends but the
         * configuration does not declare.
         */
        if (!sensorElement && display.extra) {

            const grid =
                document.querySelector(
                    `.sensor-grid[data-device="${CSS.escape(display.device_name || "")}"]`
                );

            if (!grid) {
                continue;
            }

            sensorElement =
                createSensorElement(
                    name,
                    name + " (not in config)"
                );

            sensorElement.classList.add(
                "sensor-extra"
            );

            grid.appendChild(
                sensorElement
            );
        }

        if (!sensorElement) {
            continue;
        }


        /*
         * Hide fields without a value (not
         * connected, or not sent at all).
         */
        sensorElement.hidden =
            display.value === null;

		if (display.valid) {
			sensorElement.classList.remove("sensor-invalid");
		} else {
			sensorElement.classList.add("sensor-invalid");
		}

		/*
		 * Last known value of a controller that
		 * went quiet.
		 */
		sensorElement.classList.toggle(
			"sensor-stale",
			Boolean(display.stale)
		);


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


    /*
     * Configured fields the controller did not
     * send at all have no value either.
     */
    for (
        const sensorElement
        of document.querySelectorAll(
            ".sensor-entry"
        )
    ) {

        if (!(sensorElement.dataset.shorthand in data)) {
            sensorElement.hidden = true;
        }
    }


    for (
        const section
        of document.querySelectorAll(
            ".device-section"
        )
    ) {

        section.querySelector(".device-empty").hidden =
            section.querySelector(".sensor-entry:not([hidden])") !== null;
    }
}


/*
 * Show, per device, whether its controller(s)
 * are delivering.
 *
 * devices: { "PIScO": [ { name, host, enabled,
 *     stale, error, changed }, ... ] }
 */
function displayDeviceStatus(devices) {

    for (
        const element
        of document.querySelectorAll(
            ".device-state"
        )
    ) {

        const sources =
            (devices[element.dataset.device] || [])
                .filter(source => source.enabled);

        element.innerHTML = "";

        if (sources.length === 0) {

            element.textContent =
                "not connected";

            element.className =
                "device-state device-state-off";

            continue;
        }

        let anyLive = false;

        for (const source of sources) {

            const badge =
                document.createElement("span");

            let text;

            if (!source.stale) {
                text = "live";
                anyLive = true;
            } else if (source.error) {
                text = "unreachable";
            } else if (source.changed) {
                text = "stale since "
                    + new Date(source.changed)
                        .toLocaleTimeString();
            } else {
                text = "no data yet";
            }

            badge.textContent =
                `${source.name} (${source.host}): ${text}`;

            if (source.error) {
                badge.title = source.error;
            }

            element.appendChild(
                badge
            );
        }

        element.className =
            "device-state "
            + (anyLive
                ? "device-state-live"
                : "device-state-stale");
    }
}


/*
 * Fetch the latest values and update the
 * displayed values.
 */
async function updateSensorData() {

    try {

        const response =
            await fetch(
                statusUrl,
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


        displaySensorData(data.sensors);

		displayDeviceStatus(data.devices);

		displayUserAlarms(data.alarms || [], data.sensors);

		updateAlarms(data.sensors, data.alarms || []);

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
 * Start polling once per second.
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


function updateAlarms(data, userAlarms = []) {

    const activeAlarms =
        new Map();


    /*
     * Alarms set up on this page.
     */
    for (const alarm of userAlarms) {

        if (!alarm.active) {
            continue;
        }

        activeAlarms.set(
            "user:" + alarm.id,
            {
                deviceName:
                    "My alarm",

                sensorName:
                    alarm.name,

                text:
                    alarm.current == null
                        ? "no value"
                        : String(alarm.current)
            }
        );
    }


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
     * Fetch the values once per second.
     */
    startPolling();
}


initialize();


/*
 * Alarms set up on this page: the list with
 * their state, and the form to add one.
 */
const userAlarmsSection =
    document.getElementById("user-alarms");

const userAlarmsUrl =
    userAlarmsSection.dataset.url;

const userAlarmList =
    document.getElementById("user-alarm-list");

const userAlarmForm =
    document.getElementById("user-alarm-form");

const userAlarmMessage =
    document.getElementById("user-alarm-message");

const userAlarmFields =
    document.getElementById("user-alarm-fields");

let shownUserAlarms = "";

let shownFieldNames = "";


function displayUserAlarms(alarms, sensors) {

    /*
     * Fields to pick from: everything with a
     * value first, then the rest.
     */
    const names =
        Object.keys(sensors).sort((a, b) =>
            (sensors[a].value === null) - (sensors[b].value === null)
            || a.localeCompare(b));

    if (names.join() !== shownFieldNames) {

        shownFieldNames = names.join();

        userAlarmFields.replaceChildren(
            ...names.map(name => {
                const option = document.createElement("option");
                option.value = name;
                option.label = sensors[name].value === null
                    ? `${name} (no value)` : `${name} = ${sensors[name].text}`;
                return option;
            })
        );
    }


    /*
     * Mark the field's tile while its alarm is
     * active.
     */
    for (
        const element
        of document.querySelectorAll(".sensor-user-alarm")
    ) {
        element.classList.remove("sensor-user-alarm");
    }

    for (const alarm of alarms) {

        if (!alarm.active) {
            continue;
        }

        const tile =
            document.querySelector(
                `.sensor-entry[data-shorthand="${CSS.escape(alarm.field)}"]`
            );

        if (tile) {
            tile.classList.add("sensor-user-alarm");
        }
    }


    /*
     * Rebuild the list only when something in
     * it changed, so a click is not lost.
     */
    const key =
        JSON.stringify(alarms.map(a =>
            [a.id, a.enabled, a.active, a.current]));

    if (key === shownUserAlarms) {
        return;
    }

    shownUserAlarms = key;

    if (alarms.length === 0) {
        userAlarmList.textContent = "No alarms set up yet.";
        return;
    }

    userAlarmList.replaceChildren(
        ...alarms.map(alarm => {

            const row = document.createElement("div");
            row.className = "user-alarm-row"
                + (alarm.active ? " user-alarm-active" : "");

            const enabled = document.createElement("input");
            enabled.type = "checkbox";
            enabled.checked = alarm.enabled !== false;
            enabled.title = "Switch this alarm on or off";
            enabled.addEventListener("change", () =>
                changeUserAlarm(alarm.id, "PATCH", { enabled: enabled.checked }));

            const label = document.createElement("span");
            label.className = "user-alarm-name";
            label.textContent = alarm.name;

            const state = document.createElement("span");
            state.className = "user-alarm-state";
            state.textContent =
                alarm.enabled === false ? "off"
                    : (alarm.active ? "ALARM" : "ok")
                    + ` · ${alarm.field} = ${alarm.current == null ? "no value" : alarm.current}`;

            const who = document.createElement("span");
            who.className = "user-alarm-who";
            who.textContent = `by ${alarm.created_by}`;

            const remove = document.createElement("button");
            remove.type = "button";
            remove.textContent = "Delete";
            remove.addEventListener("click", () => {
                if (window.confirm(`Delete the alarm "${alarm.name}"?`)) {
                    changeUserAlarm(alarm.id, "DELETE");
                }
            });

            row.append(enabled, label, state, who, remove);
            return row;
        })
    );
}


async function changeUserAlarm(id, method, body) {

    try {

        const response =
            await fetch(
                `${userAlarmsUrl}/${encodeURIComponent(id)}`,
                {
                    method,
                    headers: { "Content-Type": "application/json" },
                    body: body ? JSON.stringify(body) : undefined,
                }
            );

        if (!response.ok) {
            const result = await response.json().catch(() => ({}));
            throw new Error(result.error || `HTTP ${response.status}`);
        }

        shownUserAlarms = "";
        await updateSensorData();

    } catch (error) {

        userAlarmMessage.textContent =
            `Could not change the alarm: ${error.message}`;
    }
}


/*
 * Show only the inputs the chosen condition
 * needs.
 */
function showConditionInputs() {

    const kind =
        userAlarmForm.elements.kind.value;

    for (
        const label
        of userAlarmForm.querySelectorAll("[data-for]")
    ) {
        label.hidden = label.dataset.for !== kind;
    }
}

userAlarmForm.elements.kind.addEventListener(
    "change",
    showConditionInputs
);

showConditionInputs();


userAlarmForm.addEventListener(
    "submit",
    async event => {

        event.preventDefault();

        const form = userAlarmForm.elements;

        const body = {
            field: form.field.value.trim(),
            kind: form.kind.value,
            name: form.name.value.trim(),
            op: form.op.value,
            value: form.value.value.trim(),
            min: form.min.value === "" ? null : Number(form.min.value),
            max: form.max.value === "" ? null : Number(form.max.value),
            seconds: Number(form.seconds.value),
        };

        if (form.guard_field.value.trim()) {
            body.only_while = {
                field: form.guard_field.value.trim(),
                op: form.guard_op.value,
                value: form.guard_value.value.trim(),
            };
        }

        try {

            const response =
                await fetch(
                    userAlarmsUrl,
                    {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body),
                    }
                );

            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.error || `HTTP ${response.status}`);
            }

            userAlarmMessage.textContent =
                `Added: ${result.name}`;

            userAlarmForm.reset();
            showConditionInputs();

            shownUserAlarms = "";
            await updateSensorData();

        } catch (error) {

            userAlarmMessage.textContent =
                `Not added: ${error.message}`;
        }
    }
);
