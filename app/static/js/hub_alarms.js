// Alarms on every Hub page: a banner while any alarm is on, and a popup
// for alarms this browser has not acknowledged yet. The Hub's alarm
// monitor decides what is on; acknowledgements are kept in this browser
// (and logged on the Hub), keyed by alarm and start time, so an alarm
// that ends and comes back is shown again.
(() => {
    const banner = document.getElementById('hub-alarm-banner');
    if (!banner) return;

    const ACK_KEY = 'hubAlarmAcknowledged';
    const activeUrl = banner.dataset.activeUrl;
    const ackUrl = banner.dataset.ackUrl;
    const pageUrl = banner.dataset.pageUrl;
    const onStatusPage = location.pathname === new URL(pageUrl, location.href).pathname;

    let overlay = null;
    let shown = new Set();

    function loadAcknowledged() {
        try {
            return new Set(JSON.parse(localStorage.getItem(ACK_KEY) || '[]'));
        } catch (error) {
            return new Set();
        }
    }

    function saveAcknowledged(keys) {
        try {
            localStorage.setItem(ACK_KEY, JSON.stringify([...keys]));
        } catch (error) {
            // Private window or blocked storage: acknowledgements last for this page only.
        }
    }

    let acknowledged = loadAcknowledged();

    function describe(alarm) {
        const where = alarm.device ? `${alarm.device}: ` : '';
        return `${where}${alarm.name} (${alarm.value})`;
    }

    function showBanner(active) {
        // The Status Display has its own alarm board.
        if (!active.length || onStatusPage) {
            banner.hidden = true;
            return;
        }
        const items = document.createElement('span');
        items.className = 'hub-alarm-items';
        for (const alarm of active) {
            const item = document.createElement('span');
            item.textContent = describe(alarm);
            items.append(item);
        }
        const title = document.createElement('strong');
        title.textContent = `⚠ ${active.length} alarm${active.length > 1 ? 's' : ''}`;
        const link = document.createElement('a');
        link.href = pageUrl;
        link.textContent = 'Status Display →';
        banner.replaceChildren(title, items, link);
        banner.hidden = false;
    }

    function showPopup(active) {
        const fresh = active.filter(alarm => !acknowledged.has(alarm.key));
        const keys = new Set(fresh.map(alarm => alarm.key));
        if (!fresh.length) {
            if (overlay) overlay.hidden = true;
            shown = keys;
            return;
        }
        // Rebuild only when the set changed, so the button is not replaced under the cursor.
        if (overlay && !overlay.hidden && [...keys].every(k => shown.has(k)) && keys.size === shown.size) return;
        shown = keys;

        if (!overlay) {
            overlay = document.createElement('div');
            overlay.className = 'hub-alarm-overlay';
            overlay.setAttribute('role', 'alertdialog');
            document.body.append(overlay);
        }
        const popup = document.createElement('div');
        popup.className = 'hub-alarm-popup';
        const title = document.createElement('h2');
        title.textContent = '⚠ Alarm';
        const list = document.createElement('ul');
        for (const alarm of fresh) {
            const item = document.createElement('li');
            item.textContent = `${describe(alarm)} — since ${new Date(alarm.since).toLocaleTimeString()}`;
            list.append(item);
        }
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = 'Acknowledge';
        button.addEventListener('click', () => {
            for (const alarm of fresh) acknowledged.add(alarm.key);
            saveAcknowledged(acknowledged);
            overlay.hidden = true;
            fetch(ackUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids: fresh.map(alarm => alarm.id) }),
                showLoader: false,
            }).catch(() => {});
        });
        popup.append(title, list, button);
        overlay.replaceChildren(popup);
        overlay.hidden = false;
        button.focus();
    }

    async function tick() {
        try {
            const response = await fetch(activeUrl, { showLoader: false });
            if (!response.ok) return;           // monitor not running: no banner
            const state = await response.json();
            const active = state.active || [];
            // Forget acknowledgements of alarms that have ended.
            const live = new Set(active.map(alarm => alarm.key));
            const before = acknowledged.size;
            acknowledged = new Set([...acknowledged].filter(key => live.has(key)));
            if (acknowledged.size !== before) saveAcknowledged(acknowledged);
            showBanner(active);
            showPopup(active);
        } catch (error) {
            // Hub not reachable for a moment; try again on the next tick.
        }
    }

    tick();
    setInterval(tick, 2000);
})();
