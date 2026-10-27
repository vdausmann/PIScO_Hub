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

// Shutdown All: sea-through-controll's shutdown sequence, run by the Hub.
(() => {
    const section = document.getElementById('shutdown-all');
    if (!section) return;

    const url = section.dataset.url;
    const shutdownButton = section.querySelector('[data-shutdown]');
    const dryRunButton = section.querySelector('[data-shutdown-dry-run]');
    const status = section.querySelector('[data-shutdown-status]');
    const log = section.querySelector('[data-shutdown-log]');
    const testMode = document.getElementById('device-controls').dataset.testMode === 'true';
    let timer = null;

    function show(state) {
        const run = state.run;
        const running = Boolean(state.running);
        shutdownButton.disabled = running || testMode;
        dryRunButton.disabled = running;
        if (!run) {
            status.textContent = 'Not run since the Hub started.';
            log.hidden = true;
            return;
        }
        const what = run.dry_run ? 'Dry run' : 'Shutdown All';
        const started = new Date(run.started).toLocaleString();
        if (running) {
            status.textContent = `${what} started by ${run.user} at ${started} — running…`;
        } else if (run.exit_code === 0) {
            status.textContent = `${what} by ${run.user} at ${started}: finished.`;
        } else {
            status.textContent = `${what} by ${run.user} at ${started}: failed (exit ${run.exit_code}). See the log below.`;
        }
        log.textContent = run.log.join('\n');
        log.hidden = false;
        clearTimeout(timer);
        if (running) timer = setTimeout(refresh, 1000);
    }

    async function refresh() {
        try {
            const response = await fetch(url, { credentials: 'same-origin', showLoader: false });
            const state = await response.json();
            if (!response.ok) throw new Error(state.error || `Request failed (${response.status})`);
            show(state);
        } catch (error) {
            status.textContent = `Could not read the shutdown status: ${error.message}`;
        }
    }

    async function start(body) {
        shutdownButton.disabled = dryRunButton.disabled = true;
        try {
            const response = await fetch(url, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const state = await response.json();
            if (!response.ok) throw new Error(state.error || `Request failed (${response.status})`);
            show(state);
        } catch (error) {
            status.textContent = `Could not start: ${error.message}`;
            refresh();
        }
    }

    shutdownButton.addEventListener('click', () => {
        if (!window.confirm(
            'Shutdown All turns off the whole instrument before it comes back on deck:\n\n'
            + '1. fetch the Keller log from the Propel-Akku\n'
            + '2. shut down LIFI, PIScO, BIOLUM and the Propel-Akku, one after another\n\n'
            + 'Do you really want to proceed with the shutdown?')) return;
        start({ confirm: 'SHUTDOWN' });
    });
    dryRunButton.addEventListener('click', () => start({ dry_run: true }));

    refresh();
})();
