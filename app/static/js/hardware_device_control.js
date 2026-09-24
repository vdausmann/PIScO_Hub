(() => {
    const root = document.getElementById('device-controls');
    if (!root) return;

    const config = JSON.parse(root.dataset.config || '{}');
    const activeDevices = new Set(JSON.parse(root.dataset.devices || '[]'));
    const testMode = root.dataset.testMode === 'true';
    const testCommands = JSON.parse(root.dataset.testCommands || '{}');
    const commandUrl = root.dataset.commandUrl;
    const message = document.getElementById('control-message');

    function endpoint(device) {
        return commandUrl.replace('DEVICE_NAME', encodeURIComponent(device));
    }

    function fieldValue(input) {
        if (input.dataset.type === 'bool') return input.value === '1';
        if (input.dataset.type === 'int') return Number.parseInt(input.value, 10);
        if (input.dataset.type === 'float') return Number(input.value);
        return input.value;
    }

    function makeInput(command, value) {
        const type = command.type || 'text';
        let input;
        if (type === 'bool') {
            input = document.createElement('select');
            for (const [label, val] of [['Off', '0'], ['On', '1']]) {
                const option = document.createElement('option');
                option.value = val;
                option.textContent = label;
                input.append(option);
            }
            input.value = ['1', 'true', 'on'].includes(String(value).toLowerCase()) ? '1' : '0';
        } else {
            input = document.createElement('input');
            input.type = ['int', 'float'].includes(type) ? 'number' : 'text';
            if (type === 'int') input.step = '1';
            if (type === 'float') input.step = 'any';
            input.value = value == null ? '' : String(value);
        }
        input.name = command.shorthand;
        input.dataset.type = type;
        input.dataset.original = input.value;
        input.required = true;
        return input;
    }

    async function loadDevice(section) {
        const device = section.dataset.device;
        const status = section.querySelector('[data-status]');
        const fields = section.querySelector('[data-command-fields]');
        const form = section.querySelector('[data-command-form]');
        const sendButton = section.querySelector('[data-send]');
        const commands = (config[device] && config[device].commands) || [];

        if (!testMode && !activeDevices.has(device)) {
            status.textContent = 'Device is not enabled or reachable by the poller.';
            form.hidden = true;
            return;
        }

        try {
            let state;
            if (testMode) {
                state = testCommands[device] || {};
            } else {
                const response = await fetch(endpoint(device), { credentials: 'same-origin' });
                state = await response.json();
                if (!response.ok) throw new Error(state.error || `Request failed (${response.status})`);
            }

            fields.replaceChildren();
            const known = new Set();
            for (const command of commands) {
                const key = command.shorthand;
                if (!key || !(key in state)) continue;
                known.add(key);
                fields.append(makeCommandField(command, state[key]));
            }
            // Preserve controller settings even when the metadata config has no entry.
            for (const [key, value] of Object.entries(state)) {
                if (!known.has(key)) fields.append(makeCommandField({ shorthand: key, name: key }, value));
            }
            if (!fields.childElementCount) {
                status.textContent = 'No command settings were returned by the controller.';
                form.hidden = true;
                return;
            }
            status.textContent = testMode ? 'Mock settings loaded from the local test file.' : 'Current settings loaded from the controller.';
            sendButton.disabled = testMode;
        } catch (error) {
            status.textContent = `Could not load command settings: ${error.message}`;
            form.hidden = true;
        }
    }

    function makeCommandField(command, value) {
        const entry = document.createElement('label');
        entry.className = 'sensor-entry';
        const heading = document.createElement('span');
        heading.className = 'sensor-name';
        heading.textContent = command.name ? `${command.name} (${command.shorthand})` : command.shorthand;
        const input = makeInput(command, value);
        input.setAttribute('aria-label', command.name || command.shorthand);
        entry.append(heading, input);
        return entry;
    }

    document.querySelectorAll('[data-device]').forEach(section => {
        loadDevice(section);
        section.querySelector('[data-command-form]').addEventListener('submit', async event => {
            event.preventDefault();
            if (testMode) return;
            if (!event.currentTarget.reportValidity()) return;
            const status = section.querySelector('[data-status]');
            const button = section.querySelector('[data-send]');
            const changes = {};
            section.querySelectorAll('[data-command-fields] input, [data-command-fields] select').forEach(input => {
                if (input.value !== input.dataset.original) changes[input.name] = fieldValue(input);
            });
            if (!Object.keys(changes).length) {
                status.textContent = 'No settings have changed.';
                return;
            }
            button.disabled = true;
            status.textContent = 'Sending changes…';
            message.textContent = '';
            try {
                const response = await fetch(endpoint(section.dataset.device), {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(changes),
                });
                const state = await response.json();
                if (!response.ok) throw new Error(state.error || `Request failed (${response.status})`);
                section.querySelectorAll('[data-command-fields] input, [data-command-fields] select').forEach(input => {
                    if (Object.hasOwn(changes, input.name)) input.dataset.original = input.value;
                });
                status.textContent = 'Changes sent. The displayed values now match the poller command state.';
            } catch (error) {
                status.textContent = `Could not send changes: ${error.message}`;
            } finally {
                button.disabled = false;
            }
        });
    });
})();
