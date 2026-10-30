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
        const empty
        of document.querySelectorAll(
            ".device-section .device-empty"
        )
    ) {

        // Only the device sections have one: the
        // alarm list below shares their look.
        empty.hidden =
            empty.closest(".device-section")
                .querySelector(".sensor-entry:not([hidden])") !== null;
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

		displayLimits(data.limits || []);

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
     * With live data the Hub-wide popup
     * (js/hub_alarms.js) asks for the
     * acknowledgement; this page's own popup is
     * only for a local test file, which the Hub's
     * alarm monitor does not see.
     */
    if (!statusUrl.includes("file=")) {
        return;
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

    lastSensors = sensors;

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
    lastUserAlarms = alarms;

    const listed =
        alarms.filter(alarm => listedByTools(
            alarm.active,
            [alarm.field, alarm.name, alarm.created_by]));

    const key =
        JSON.stringify([listFilterKey(), listed.map(a =>
            [a.id, a.enabled, a.active, a.current, a.name])]);

    if (key === shownUserAlarms) {
        return;
    }

    shownUserAlarms = key;

    if (listed.length === 0) {
        userAlarmList.textContent = alarms.length === 0
            ? "No alarms set up yet." : "None match the filter.";
        return;
    }

    userAlarmList.replaceChildren(
        ...listed.map(alarm => {

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
            who.textContent = `by ${alarm.created_by}`
                + (alarm.changed_by ? `, changed by ${alarm.changed_by}` : "");

            const edit = document.createElement("button");
            edit.type = "button";
            edit.textContent = "Edit";
            edit.addEventListener("click", () => editUserAlarm(alarm));

            const remove = document.createElement("button");
            remove.type = "button";
            remove.textContent = "Delete";
            remove.addEventListener("click", () => {
                if (window.confirm(`Delete the alarm "${alarm.name}"?`)) {
                    changeUserAlarm(alarm.id, "DELETE");
                }
            });

            row.append(enabled, label, state, who, edit, remove);
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

        const editing = editingAlarmId;

        try {

            const response =
                await fetch(
                    editing
                        ? `${userAlarmsUrl}/${encodeURIComponent(editing)}`
                        : userAlarmsUrl,
                    {
                        method: editing ? "PATCH" : "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body),
                    }
                );

            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.error || `HTTP ${response.status}`);
            }

            userAlarmMessage.textContent =
                `${editing ? "Changed" : "Added"}: ${result.name}`;

            stopEditingUserAlarm();

            shownUserAlarms = "";
            await updateSensorData();

        } catch (error) {

            userAlarmMessage.textContent =
                `${editing ? "Not changed" : "Not added"}: ${error.message}`;
        }
    }
);



/*
 * Editing one of your own alarms: the form
 * above is filled with it and saves it in
 * place.
 */
let editingAlarmId = null;

let lastUserAlarms = [];

const userAlarmSubmit =
    document.getElementById("user-alarm-submit");

const userAlarmCancel =
    document.getElementById("user-alarm-cancel");


function editUserAlarm(alarm) {

    const form = userAlarmForm.elements;

    userAlarmForm.reset();

    form.field.value = alarm.field;
    form.kind.value = alarm.kind;
    form.name.value = alarm.name;
    form.op.value = alarm.op || ">";
    form.value.value = alarm.value ?? "";
    form.min.value = alarm.min ?? "";
    form.max.value = alarm.max ?? "";
    form.seconds.value = alarm.seconds ?? 30;

    if (alarm.only_while) {
        form.guard_field.value = alarm.only_while.field;
        form.guard_op.value = alarm.only_while.op;
        form.guard_value.value = alarm.only_while.value;
    }

    editingAlarmId = alarm.id;
    userAlarmSubmit.textContent = "Save changes";
    userAlarmCancel.hidden = false;
    userAlarmMessage.textContent = `Editing: ${alarm.name}`;

    showConditionInputs();
    userAlarmForm.scrollIntoView({ behavior: "smooth", block: "center" });
}


function stopEditingUserAlarm() {

    editingAlarmId = null;
    userAlarmForm.reset();
    userAlarmSubmit.textContent = "Add alarm";
    userAlarmCancel.hidden = true;
    showConditionInputs();
}

userAlarmCancel.addEventListener("click", () => {
    stopEditingUserAlarm();
    userAlarmMessage.textContent = "";
});


/*
 * The filter above both lists.
 */
const activeOnlyInput =
    document.getElementById("alarm-list-active-only");

const listFilterInput =
    document.getElementById("alarm-list-filter");


function listFilterKey() {
    return `${activeOnlyInput.checked}|${listFilterInput.value.trim().toLowerCase()}`;
}


function listedByTools(active, words) {

    if (activeOnlyInput.checked && !active) {
        return false;
    }

    const filter =
        listFilterInput.value.trim().toLowerCase();

    return !filter
        || words.some(word => String(word ?? "").toLowerCase().includes(filter));
}

for (const input of [activeOnlyInput, listFilterInput]) {
    input.addEventListener("input", () => {
        displayLimits(lastLimits);
        displayUserAlarms(lastUserAlarms, lastSensors);
    });
}


/*
 * The configured limits: on/off, min, max
 * and condition, edited in their row.
 */
const limitList =
    document.getElementById("limit-list");

const limitMessage =
    document.getElementById("limit-message");

const limitsUrl =
    userAlarmsSection.dataset.limitsUrl;

let lastLimits = [];

let lastSensors = {};

let shownLimits = "";

let editingLimit = null;


function limitText(value) {
    return value === null || value === undefined || value === "" ? "–" : String(value);
}


function displayLimits(limits) {

    lastLimits = limits;

    // A rebuild would throw away what is being typed.
    if (editingLimit) {
        return;
    }

    const listed =
        limits.filter(limit => listedByTools(
            limit.active,
            [limit.field, limit.name, limit.device]));

    const key =
        JSON.stringify([listFilterKey(), listed]);

    if (key === shownLimits) {
        return;
    }

    shownLimits = key;

    if (listed.length === 0) {
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = 9;
        cell.textContent = limits.length === 0
            ? "No limits configured." : "None match the filter.";
        row.append(cell);
        limitList.replaceChildren(row);
        return;
    }

    limitList.replaceChildren(...listed.map(limitRow));
}


function cell(text, className) {
    const td = document.createElement("td");
    td.textContent = text;
    if (className) {
        td.className = className;
    }
    return td;
}


function limitRow(limit) {

    const row = document.createElement("tr");
    row.className = limit.active ? "limit-active" : "";

    const enabled = document.createElement("input");
    enabled.type = "checkbox";
    enabled.checked = limit.enabled;
    enabled.title = "Switch this limit's alarm on or off";
    enabled.addEventListener("change", () =>
        saveLimit(limit, { ...limit, enabled: enabled.checked }));

    const on = document.createElement("td");
    on.append(enabled);

    const field = document.createElement("td");
    const name = document.createElement("span");
    name.className = "user-alarm-name";
    name.textContent = limit.name;
    const shorthand = document.createElement("span");
    shorthand.className = "user-alarm-who";
    shorthand.textContent = ` ${limit.field}${limit.unit ? ` (${limit.unit})` : ""}`;
    field.append(name, shorthand);

    if (limit.changed) {
        const changed = document.createElement("span");
        changed.className = "limit-changed";
        const c = limit.configured;
        changed.textContent = "changed";
        changed.title = `by ${limit.changed_by || "?"}; in the config file: `
            + `min ${limitText(c.min)}, max ${limitText(c.max)}`
            + (c.condition ? `, only while ${c.condition}` : "");
        field.append(" ", changed);
    }

    const state =
        !limit.enabled ? "off"
            : limit.stale ? "stale"
                : limit.active ? "ALARM"
                    : limit.valid ? "ok" : "out of range (condition not met)";

    const buttons = document.createElement("td");
    buttons.className = "limit-buttons";

    const edit = document.createElement("button");
    edit.type = "button";
    edit.textContent = "Edit";
    edit.addEventListener("click", () => editLimit(row, limit));
    buttons.append(edit);

    if (limit.changed) {
        const reset = document.createElement("button");
        reset.type = "button";
        reset.textContent = "Reset";
        reset.title = "Back to the config file's limit";
        reset.addEventListener("click", () => resetLimit(limit));
        buttons.append(reset);
    }

    row.append(
        on,
        cell(limit.device),
        field,
        cell(limitText(limit.min)),
        cell(limitText(limit.max)),
        cell(limitText(limit.condition)),
        cell(limitText(limit.current)),
        cell(state, "limit-state"),
        buttons,
    );

    return row;
}


function editLimit(row, limit) {

    editingLimit = limit.field;

    const inputs = {};

    for (const [index, key] of [[3, "min"], [4, "max"], [5, "condition"]]) {
        const input = document.createElement("input");
        input.value = limit[key] ?? "";
        input.placeholder = key === "condition" ? "Pa-PiPwr==1" : "none";
        input.size = key === "condition" ? 14 : 7;
        inputs[key] = input;
        row.children[index].replaceChildren(input);
    }

    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "Save";
    save.addEventListener("click", () => saveLimit(limit, {
        min: inputs.min.value.trim() || null,
        max: inputs.max.value.trim() || null,
        condition: inputs.condition.value.trim() || null,
        enabled: limit.enabled,
    }));

    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "Cancel";
    cancel.addEventListener("click", () => {
        editingLimit = null;
        shownLimits = "";
        displayLimits(lastLimits);
    });

    for (const input of Object.values(inputs)) {
        input.addEventListener("keydown", event => {
            if (event.key === "Enter") save.click();
            if (event.key === "Escape") cancel.click();
        });
    }

    row.lastChild.replaceChildren(save, cancel);
    inputs.min.focus();
}


async function limitRequest(limit, method, body) {

    const response =
        await fetch(
            `${limitsUrl}${encodeURIComponent(limit.field)}`,
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
}


async function saveLimit(limit, body) {

    try {
        await limitRequest(limit, "PUT", {
            min: body.min, max: body.max,
            condition: body.condition, enabled: body.enabled,
        });
        limitMessage.textContent = `Changed: ${limit.name} (${limit.field})`;
        editingLimit = null;
        shownLimits = "";
        await updateSensorData();
    } catch (error) {
        limitMessage.textContent = `Not changed: ${error.message}`;
    }
}


async function resetLimit(limit) {

    try {
        await limitRequest(limit, "DELETE");
        limitMessage.textContent = `Back to the config file: ${limit.name} (${limit.field})`;
        shownLimits = "";
        await updateSensorData();
    } catch (error) {
        limitMessage.textContent = `Not reset: ${error.message}`;
    }
}
