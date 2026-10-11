// LADCP page: actions, operation progress, live plots, setup form, console.
// Talks to app/routes/ladcp.py; everything is drawn on plain canvases so
// the page works on a ship without internet.

(() => {
const root = document.getElementById("ladcp");
const BASE = root.dataset.base.replace(/\/$/, "");
const $ = (id) => document.getElementById(id);

let status = null;
let lastAction = null;          // {path, body} to repeat with force after a confirmation
let formDirty = false;
let activeTab = "live";
const live = {};                // name -> {records, seq, geometry, age, ...}
let transcriptSeq = 0;
let transcriptFor = null;

// ---- helpers -------------------------------------------------------------------
async function api(path, options = {}) {
	// base.html blanks the screen with its loader for every fetch unless
	// told not to; the polls run several times a second, so reads never
	// show it. Actions (POST/PUT) still do.
	const res = await fetch(BASE + path, {
		headers: {"Content-Type": "application/json"},
		credentials: "same-origin",
		showLoader: Boolean(options.method && options.method !== "GET"),
		...options,
	});
	let body = null;
	try { body = await res.json(); } catch (_) { /* empty */ }
	if (!res.ok) throw new Error((body && body.error) || `${res.status} ${res.statusText}`);
	return body;
}
const post = (path, body = {}) => api(path, {method: "POST", body: JSON.stringify(body)});
const pad3 = (n) => String(n).padStart(3, "0");
const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v)) ? "—" : Number(v).toFixed(d);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const hhmmss = (t) => t ? new Date(t * 1000).toISOString().substring(11, 19) : "—";
const css = (name) => getComputedStyle(document.body).getPropertyValue(name).trim() || "#888";

// ---- status, actions, operation -------------------------------------------------
async function refreshStatus() {
	try {
		status = await api("/status");
	} catch (e) {
		$("h-state").textContent = "Hub unreachable";
		$("h-state").className = "badge bad";
		return;
	}
	renderHeader();
	renderOperation();
	renderInstruments();
	if (!formDirty && activeTab === "setup" && !$("config-form").dataset.loaded) renderForm();
	if (activeTab === "log") renderLog();
}

function busy() {
	return status && status.operation && status.operation.state === "running";
}

function renderHeader() {
	const st = status.state, cfg = status.config;
	$("h-cruise").textContent = cfg.cruise_id;
	$("h-next").textContent = pad3((st.profile || 0) + 1);
	$("h-battery").textContent = `${st.total_pings || 0} pings · ${fmt(st.total_ping_hours, 1)} h since battery swap`;
	const badge = $("h-state");
	if (busy()) {
		badge.textContent = status.operation.kind.replace("_", " & ") + " …";
		badge.className = "badge run";
	} else if (st.deployed) {
		badge.textContent = `Deployed · profile ${pad3(st.deployed.profile)} since ${hhmmss(st.deployed.started)}`;
		badge.className = "badge ok";
	} else {
		badge.textContent = st.last_action ? `Idle · last: ${st.last_action}` : "Idle";
		badge.className = "badge";
	}
	const running = busy();
	for (const id of ["btn-detect", "btn-erase", "btn-start", "btn-stop", "btn-stop-download"])
		$(id).disabled = running;
	$("data-dir").textContent = status.data_dir;
}

const ICONS = {pending: "○", running: "◐", done: "✓", failed: "✗", skipped: "–"};

function renderOperation() {
	const op = status.operation;
	const box = $("operation");
	if (!op) { box.classList.add("hidden"); return; }
	box.classList.remove("hidden");
	box.className = `operation ${op.state}`;
	$("op-title").textContent = {detect: "Detect", erase: "Erase", start: "Start",
		stop: "Stop", stop_download: "Stop & download"}[op.kind] || op.kind;
	const stateText = {running: "running", done: "done", failed: "failed",
		needs_confirmation: "needs your answer"}[op.state] || op.state;
	$("op-state").textContent = stateText;
	$("op-state").className = "badge small " + ({done: "ok", failed: "bad", running: "run",
		needs_confirmation: "warn"}[op.state] || "");
	$("op-steps").innerHTML = op.steps.map((s) =>
		`<li class="${s.state}"><span class="icon">${ICONS[s.state] || ""}</span>` +
		`<span>${esc(s.label)}</span><span class="detail">${esc(s.detail)}</span></li>`).join("");
	const msgs = op.messages.map((m) => `<li class="${m.level}">${esc(m.text)}</li>`);
	if (op.state === "failed" && op.error) msgs.unshift(`<li class="error">${esc(op.error)}</li>`);
	$("op-messages").innerHTML = msgs.join("");
	const confirm = $("op-confirm");
	if (op.state === "needs_confirmation" && lastAction) {
		confirm.classList.remove("hidden");
		$("op-confirm-text").textContent = op.error;
	} else {
		confirm.classList.add("hidden");
	}
}

async function act(path, body = {}) {
	lastAction = {path, body};
	try {
		await post(path, body);
	} catch (e) {
		alert(e.message);
	}
	refreshStatus();
}

$("btn-detect").onclick = () => act("/detect");
$("btn-erase").onclick = () => {
	if (confirm("Erase the recorders of the connected ADCPs?")) act("/erase");
};
$("btn-start").onclick = () => {
	const v = $("deck-voltage").value.trim();
	act("/start", {deck_voltage: v || null});
};
$("btn-stop").onclick = () => {
	if (confirm("Stop the ADCPs without downloading?")) act("/stop", {download: false});
};
$("btn-stop-download").onclick = () => act("/stop", {download: true});
$("op-confirm-yes").onclick = () => {
	if (lastAction) act(lastAction.path, {...lastAction.body, force: true});
};
$("op-confirm-no").onclick = () => { $("op-confirm").classList.add("hidden"); lastAction = null; };
$("edit-profile").onclick = async () => {
	const current = status ? status.state.profile : 0;
	const v = prompt("Number of the LAST profile done (the next start uses this + 1):", current);
	if (v === null) return;
	try {
		await post("/profile", {profile: parseInt(v, 10)});
	} catch (e) { alert(e.message); }
	refreshStatus();
};

function renderInstruments() {
	$("instruments").innerHTML = status.instruments.map((i) => {
		const reach = i.reachable === null ? `<span class="badge small">not checked</span>`
			: i.reachable ? `<span class="badge small ok">answers</span>`
			: `<span class="badge small bad">no answer</span>`;
		const files = i.directory ? (i.directory.files || []) : null;
		const free = i.directory && i.directory.free_bytes !== null && i.directory.free_bytes !== undefined
			? `${(i.directory.free_bytes / 1e6).toFixed(1)} MB free` : "";
		return `<div class="inst">
			<h3><span>${esc(i.name)}${i.role ? ` · ${esc(i.role)}` : ""}</span>${reach}</h3>
			<dl>
				<dt>Port</dt><dd><code>${esc(i.port)}</code> @ ${esc(i.baud)}, break ${esc(i.break)}${i.enabled ? "" : " (disabled)"}</dd>
				<dt>Instrument</dt><dd>${i.info && i.info.serial ? `S/N ${esc(i.info.serial)} · ${esc(i.info.orientation || "?")}${i.info.firmware ? " · fw " + esc(i.info.firmware) : ""}` : "—"}</dd>
				<dt>Recorder</dt><dd>${files === null ? "—" : files.length ? files.map((f) => esc(f.name)).join(", ") : "empty"} ${free ? "· " + free : ""}</dd>
				<dt>Stream</dt><dd>${i.streaming ? "receiving" : "—"}${i.recording ? ` → <code>${esc(i.recording.split("/").slice(-3).join("/"))}</code>` : ""}</dd>
				${i.error ? `<dt>Error</dt><dd class="age-bad">${esc(i.error)}</dd>` : ""}
				${i.checked ? `<dt>Checked</dt><dd>${hhmmss(i.checked)} UTC</dd>` : ""}
			</dl></div>`;
	}).join("");
}

// ---- live ----------------------------------------------------------------------
async function refreshLive() {
	const since = Object.entries(live).map(([n, l]) => `${n}:${l.seq}`).join(",");
	let data;
	try { data = await api("/live" + (since ? `?since=${since}` : "")); } catch (_) { return; }
	const cutoff = Date.now() / 1000 - 3700;
	for (const [name, d] of Object.entries(data)) {
		let l = live[name];
		if (!l || d.seq < l.seq) l = live[name] = {records: [], seq: 0};   // a new deployment
		l.records.push(...d.records);
		while (l.records.length && l.records[0].received < cutoff) l.records.shift();
		Object.assign(l, {seq: d.seq, geometry: d.geometry, age: d.age, count: d.count,
			streaming: d.streaming, role: d.role, orientation: d.orientation, errors: d.errors,
			skipped: d.skipped_bytes});
	}
	for (const name of Object.keys(live)) if (!(name in data)) delete live[name];
	renderReadouts();
	drawAll();
}

function isUp(l) {
	if (l.geometry && l.geometry.up !== undefined && l.geometry.up !== null) return l.geometry.up;
	return l.orientation === "UP";
}

function renderReadouts() {
	const scale = status ? status.config.voltage_scale : null;
	const names = Object.keys(live);
	if (!names.length) {
		$("readouts").innerHTML = `<div class="readout"><p class="hint">No live data. The stream starts with
			<b>Start</b> (when the serial output is on in Setup) and continues until <b>Stop</b>.</p></div>`;
		return;
	}
	$("readouts").innerHTML = names.sort((a, b) => isUp(live[b]) - isUp(live[a])).map((name) => {
		const l = live[name];
		const r = l.records[l.records.length - 1] || {};
		const ageClass = l.age === null || l.age === undefined ? "" : l.age < 5 ? "age-ok" : l.age < 15 ? "age-warn" : "age-bad";
		const up = isUp(l);
		const volts = r.voltage_raw !== undefined && r.voltage_raw !== null
			? (scale ? `${fmt(r.voltage_raw * scale, 1)} V` : `${r.voltage_raw}`) : "—";
		const cell = (k, v) => `<div><div class="k">${k}</div><div class="v">${v}</div></div>`;
		return `<div class="readout">
			<h4><span>${esc(name)} · ${up ? "UP" : "DOWN"}${l.role ? " · " + esc(l.role) : ""}</span>
				<span class="${ageClass}">${l.streaming ? "●" : "○"} ${l.age === null || l.age === undefined ? "no data" : fmt(l.age, 0) + " s ago"}</span></h4>
			<div class="grid">
				<div class="target"><div class="k">${up ? "Surface" : "Bottom"} distance</div>
					<div class="v">${r.target ? fmt(r.target, 0) + " m" : "—"}</div></div>
				${cell(up ? "Depth (est.)" : "Depth (est.)", r.depth !== undefined ? fmt(r.depth, 0) + " m" : "—")}
				${cell("w", r.w !== null && r.w !== undefined ? fmt(r.w, 2) + " m/s" : "—")}
				${cell("Heading", fmt(r.heading, 1) + "°")}
				${cell("Pitch", fmt(r.pitch, 1) + "°")}
				${cell("Roll", fmt(r.roll, 1) + "°")}
				${cell("Temp", fmt(r.temperature, 2) + " °C")}
				${cell("Xmit volt.", volts)}
				${cell("Ensembles", l.count ?? "—")}
				${cell("Ens. #", r.number ?? "—")}
				${cell("Skipped", l.skipped ? l.skipped + " B" : "0")}
			</div>
			${l.errors && l.errors.length ? `<p class="age-bad">${esc(l.errors[l.errors.length - 1].text)}</p>` : ""}
		</div>`;
	}).join("");
}

// viridis, 9 stops
const VIRIDIS = [[68,1,84],[71,44,122],[59,81,139],[44,113,142],[33,144,141],[39,173,129],[92,200,99],[170,220,50],[253,231,37]];
function color(v, lo, hi) {
	let t = (v - lo) / (hi - lo);
	t = Math.max(0, Math.min(1, t)) * (VIRIDIS.length - 1);
	const i = Math.min(VIRIDIS.length - 2, Math.floor(t)), f = t - i;
	const a = VIRIDIS[i], b = VIRIDIS[i + 1];
	return `rgb(${a.map((x, k) => Math.round(x + (b[k] - x) * f)).join(",")})`;
}

function setup(canvas) {
	const dpr = window.devicePixelRatio || 1;
	const w = canvas.clientWidth, h = canvas.height / (canvas._dpr || 1);
	if (!canvas._h) canvas._h = canvas.height;
	canvas.width = Math.round(w * dpr);
	canvas.height = Math.round(canvas._h * dpr);
	canvas._dpr = dpr;
	const ctx = canvas.getContext("2d");
	ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
	ctx.clearRect(0, 0, w, canvas._h);
	ctx.font = "11px sans-serif";
	return {ctx, w, h: canvas._h};
}

function ranges() {
	let up = 0, dn = 0;
	for (const l of Object.values(live)) {
		const g = l.geometry;
		if (!g) continue;
		const r = g.bin1_m + (g.n_cells - 0.5) * g.cell_m;
		if (isUp(l)) up = Math.max(up, r); else dn = Math.max(dn, r);
	}
	if (!up && !dn) { up = 200; dn = 200; }
	return {up: up || 20, dn: dn || 20};
}

function drawAll() {
	if (activeTab !== "live") return;
	drawWaterfall();
	drawProfile();
	drawDepth();
}

const M = {l: 46, r: 10, t: 12, b: 24};

function yAxis(ctx, h, rg, w) {
	const span = rg.up + rg.dn;
	const y = (r) => M.t + (rg.up - r) / span * (h - M.t - M.b);   // r: + above, - below
	ctx.fillStyle = css("--text");
	ctx.strokeStyle = "rgba(127,127,127,.25)";
	ctx.textAlign = "right";
	ctx.textBaseline = "middle";
	const step = span > 300 ? 100 : 50;
	for (let r = -Math.floor(rg.dn / step) * step; r <= rg.up; r += step) {
		ctx.beginPath(); ctx.moveTo(M.l, y(r)); ctx.lineTo(w - M.r, y(r)); ctx.stroke();
		ctx.fillText(`${r > 0 ? "+" : ""}${r}`, M.l - 4, y(r));
	}
	ctx.save();
	ctx.translate(11, M.t + (h - M.t - M.b) / 2);
	ctx.rotate(-Math.PI / 2);
	ctx.textAlign = "center";
	ctx.fillText("m from the package (up +)", 0, 0);
	ctx.restore();
	return y;
}

function drawWaterfall() {
	const {ctx, w, h} = setup($("waterfall"));
	const rg = ranges();
	const win = parseInt($("window").value, 10);
	const now = Date.now() / 1000;
	const x = (t) => M.l + (t - (now - win)) / win * (w - M.l - M.r);
	const lo = parseFloat($("ei-min").value) || 40, hi = parseFloat($("ei-max").value) || 160;
	const span = rg.up + rg.dn;
	const ypx = (h - M.t - M.b) / span;
	const y0 = M.t + rg.up * ypx;
	ctx.fillStyle = "rgba(127,127,127,.08)";
	ctx.fillRect(M.l, M.t, w - M.l - M.r, h - M.t - M.b);
	for (const l of Object.values(live)) {
		const g = l.geometry;
		if (!g) continue;
		const sign = isUp(l) ? -1 : 1;
		const recs = l.records.filter((r) => r.received >= now - win - 10);
		for (let i = 0; i < recs.length; i++) {
			const r = recs[i];
			if (!r.ei) continue;
			const x1 = x(r.received);
			const x2 = i + 1 < recs.length ? x(recs[i + 1].received) : x1 + Math.max(2, x1 - (i ? x(recs[i - 1].received) : x1 - 2));
			if (x2 < M.l) continue;
			const wpx = Math.max(1, Math.min(x2 - x1 + 0.5, 30));
			for (let k = 0; k < r.ei.length; k++) {
				const e = r.ei[k];
				const mean = (e[0] + e[1] + e[2] + e[3]) / 4;
				const rr = g.bin1_m + k * g.cell_m;
				ctx.fillStyle = color(mean, lo, hi);
				const yTop = y0 + sign * (rr - g.cell_m / 2) * ypx;
				const yBot = y0 + sign * (rr + g.cell_m / 2) * ypx;
				ctx.fillRect(Math.max(M.l, x1), Math.min(yTop, yBot), wpx, Math.abs(yBot - yTop) + 0.5);
			}
			if (r.target) {
				ctx.fillStyle = "#fff";
				ctx.fillRect(Math.max(M.l, x1), y0 + sign * r.target * ypx - 1.5, wpx, 3);
			}
		}
	}
	ctx.strokeStyle = css("--text");
	ctx.beginPath(); ctx.moveTo(M.l, y0); ctx.lineTo(w - M.r, y0); ctx.stroke();
	yAxis(ctx, h, rg, w);
	// time axis
	ctx.textAlign = "center";
	ctx.textBaseline = "top";
	ctx.fillStyle = css("--text");
	const tick = win <= 300 ? 60 : win <= 900 ? 180 : 600;
	for (let t = Math.ceil((now - win) / tick) * tick; t <= now; t += tick)
		ctx.fillText(hhmmss(t).substring(0, 5), x(t), h - M.b + 6);
	ctx.textAlign = "left";
	ctx.fillText("UP", M.l + 4, M.t + 2);
	ctx.fillText("DOWN", M.l + 4, h - M.b - 14);
}

const BEAM_COLORS = ["#e41a1c", "#377eb8", "#4daf4a", "#ff7f00"];

function drawProfile() {
	const {ctx, w, h} = setup($("profile"));
	const rg = ranges();
	const lo = 20, hi = 220;
	const L = 34;
	const x = (v) => L + (v - lo) / (hi - lo) * (w - L - M.r);
	const span = rg.up + rg.dn;
	const ypx = (h - M.t - M.b) / span;
	const y0 = M.t + rg.up * ypx;
	ctx.strokeStyle = "rgba(127,127,127,.25)";
	ctx.fillStyle = css("--text");
	ctx.textAlign = "center";
	ctx.textBaseline = "top";
	for (let v = 50; v <= 200; v += 50) {
		ctx.beginPath(); ctx.moveTo(x(v), M.t); ctx.lineTo(x(v), h - M.b); ctx.stroke();
		ctx.fillText(v, x(v), h - M.b + 6);
	}
	ctx.strokeStyle = css("--text");
	ctx.beginPath(); ctx.moveTo(L, y0); ctx.lineTo(w - M.r, y0); ctx.stroke();
	for (const l of Object.values(live)) {
		const g = l.geometry;
		const r = l.records[l.records.length - 1];
		if (!g || !r || !r.ei) continue;
		const sign = isUp(l) ? -1 : 1;
		for (let b = 0; b < 4; b++) {
			ctx.strokeStyle = BEAM_COLORS[b];
			ctx.beginPath();
			r.ei.forEach((e, k) => {
				const px = x(e[b]), py = y0 + sign * (g.bin1_m + k * g.cell_m) * ypx;
				if (k) ctx.lineTo(px, py); else ctx.moveTo(px, py);
			});
			ctx.stroke();
		}
	}
	ctx.textAlign = "left";
	ctx.textBaseline = "top";
	BEAM_COLORS.forEach((c, b) => { ctx.fillStyle = c; ctx.fillText(`beam ${b + 1}`, L + 4 + b * 52, M.t); });
	ctx.fillStyle = css("--text");
	ctx.textAlign = "right";
	ctx.fillText("EI counts", w - M.r, h - M.b - 14);
}

function drawDepth() {
	const {ctx, w, h} = setup($("depthplot"));
	const win = parseInt($("window").value, 10);
	const now = Date.now() / 1000;
	const x = (t) => M.l + (t - (now - win)) / win * (w - M.l - M.r);
	const dn = Object.values(live).find((l) => !isUp(l)) || Object.values(live)[0];
	if (!dn) return;
	const recs = dn.records.filter((r) => r.received >= now - win);
	let maxD = 50;
	for (const r of recs) {
		maxD = Math.max(maxD, r.depth || 0);
		if (r.target) maxD = Math.max(maxD, (r.depth || 0) + r.target);
	}
	maxD = Math.ceil(maxD * 1.1 / 50) * 50;
	const y = (d) => M.t + d / maxD * (h - M.t - M.b);
	ctx.strokeStyle = "rgba(127,127,127,.25)";
	ctx.fillStyle = css("--text");
	ctx.textAlign = "right";
	ctx.textBaseline = "middle";
	const step = maxD > 2000 ? 1000 : maxD > 500 ? 200 : 50;
	for (let d = 0; d <= maxD; d += step) {
		ctx.beginPath(); ctx.moveTo(M.l, y(d)); ctx.lineTo(w - M.r, y(d)); ctx.stroke();
		ctx.fillText(d, M.l - 4, y(d));
	}
	ctx.strokeStyle = css("--accent");
	ctx.lineWidth = 2;
	ctx.beginPath();
	recs.forEach((r, i) => { if (i) ctx.lineTo(x(r.received), y(r.depth || 0)); else ctx.moveTo(x(r.received), y(r.depth || 0)); });
	ctx.stroke();
	ctx.lineWidth = 1;
	ctx.fillStyle = "#a16207";
	for (const r of recs) if (r.target) ctx.fillRect(x(r.received) - 1.5, y((r.depth || 0) + r.target) - 1.5, 3, 3);
	ctx.fillStyle = css("--text");
	ctx.textAlign = "left";
	ctx.textBaseline = "top";
	ctx.fillText("depth estimate (m) — line: package, dots: seafloor from the bottom echo", M.l + 4, M.t);
}

$("window").onchange = drawAll;
$("ei-min").onchange = drawAll;
$("ei-max").onchange = drawAll;
window.addEventListener("resize", drawAll);

// ---- setup form ------------------------------------------------------------------
const GENERAL = [
	["cruise_id", "Cruise ID", "text", "Also names the data folder"],
	["data_dir", "Data directory", "text", "Empty: <DATA_ROOT>/ladcp/<cruise>"],
	["backup_dir", "Backup directory", "text", "Each profile folder is copied here (network drive)"],
	["ntp_server", "NTP server", "text", "Checked before each start; empty skips the check"],
	["max_clock_offset_s", "Max. clock offset (s)", "number"],
	["primary_direction", "Primary looks", "select", null, ["DOWN", "UP"]],
	["live_output", "Serial output during the cast (live view)", "checkbox", "Sets the 4th digit of CF"],
	["start_command", "Start command", "select", "The old tool sent $ when not using a hardware break", ["CS", "$"]],
	["memory_warning_bytes", "Warn below free recorder bytes", "number"],
	["voltage_scale", "Transmit voltage scale (V/count)", "number", "0.592157 in the old tool"],
	["current_scale", "Transmit current scale (A/count)", "number"],
	["bottom_threshold", "Bottom echo threshold (counts)", "number"],
	["reattach_on_restart", "Keep reading after a Hub restart", "checkbox"],
	["download.files", "Download", "select", null, ["last", "all"]],
	["download.simultaneous", "Download both at once", "checkbox"],
	["download.baud", "Download baud (hardware-break cable only)", "select", null, [null, 9600, 19200, 38400, 57600, 115200]],
];
const ROLE_FIELDS = [
	["mode_15", "LADCP mode", "WM15", "checkbox"],
	["ambiguity_velocity", "Ambiguity velocity (cm/s)", "WV", "number"],
	["bin_number", "Number of cells", "WN", "number"],
	["bin_length", "Cell size (cm)", "WS", "number"],
	["blank_after_transmit", "Blank after transmit (cm)", "WF", "number"],
	["bandwidth", "Bandwidth (0 wide, 1 narrow)", "WB", "number"],
	["sensor_source", "Sensor source", "EZ", "text"],
	["coordinate_transformation", "Coordinate transformation", "EX", "text"],
	["flow_control", "Flow control", "CF", "text"],
	["pings_per_ensemble", "Pings per ensemble", "WP", "number"],
	["time_between_pings", "Time between pings (s)", "TP", "number"],
	["time_per_ensemble", "Time per ensemble (s)", "TE", "number"],
	["power_output", "Transmit power", "CQ", "number"],
	["when_to_sync", "Sync before/after", "SA", "text"],
	["wait_ensembles_before_sync", "Ensembles between pulses", "SI", "number", "primary"],
	["wait_time_before_sync", "Delay after pulse (0.1 ms)", "SW", "number", "primary"],
	["wait_time_before_start_without_sync", "Start alone after (s)", "ST", "number", "secondary"],
	["extra_commands", "Extra commands (one per line)", "", "lines"],
];

function getPath(obj, path) { return path.split(".").reduce((o, k) => (o || {})[k], obj); }
function setPath(obj, path, value) {
	const keys = path.split(".");
	let o = obj;
	keys.slice(0, -1).forEach((k) => { o = o[k] = o[k] || {}; });
	o[keys[keys.length - 1]] = value;
}

function input(type, name, value, options) {
	if (type === "checkbox") return `<input type="checkbox" name="${name}" ${value ? "checked" : ""}>`;
	if (type === "select") return `<select name="${name}">${options.map((o) =>
		`<option value="${o === null ? "" : esc(o)}" ${String(o) === String(value ?? "") ? "selected" : ""}>${o === null ? "no change" : esc(o)}</option>`).join("")}</select>`;
	if (type === "lines") return `<textarea name="${name}" rows="2" style="width:100%">${esc((value || []).join("\n"))}</textarea>`;
	return `<input type="${type}" name="${name}" value="${esc(value ?? "")}" ${type === "number" ? 'step="any"' : ""}>`;
}

function renderForm() {
	if (!status) return;
	const cfg = status.config;
	const general = GENERAL.map(([path, label, type, help, options]) =>
		`<div class="field"><label>${esc(label)}</label>${input(type, "g:" + path, getPath(cfg, path), options)}
		${help ? `<div class="help">${esc(help)}</div>` : ""}</div>`).join("");
	const insts = cfg.instruments.map((i, n) => `<tr>
		<td>${input("text", `i:${n}:name`, i.name)}</td>
		<td>${input("text", `i:${n}:port`, i.port)}</td>
		<td>${input("select", `i:${n}:baud`, i.baud, [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200])}</td>
		<td>${input("text", `i:${n}:break`, i.break)}</td>
		<td>${input("checkbox", `i:${n}:enabled`, i.enabled !== false)}</td></tr>`).join("");
	const roles = ROLE_FIELDS.map(([key, label, cmd, type, only]) => `<tr>
		<td>${esc(label)}</td><td class="cmd">${cmd}</td>
		<td>${only && only !== "primary" ? "" : input(type, `r:primary:${key}`, cfg.primary[key])}</td>
		<td>${only && only !== "secondary" ? "" : input(type, `r:secondary:${key}`, cfg.secondary[key])}</td></tr>`).join("");
	$("config-form").innerHTML = `
		<fieldset><legend>Cruise and procedure</legend><div class="fields">${general}</div></fieldset>
		<fieldset><legend>Instruments</legend>
			<table class="table"><tr><th>Name</th><th>Port (pyserial URL)</th><th>Baud</th><th>Break</th><th>Enabled</th></tr>${insts}</table>
			<p class="hint">Port examples: <code>/dev/ttyUSB0</code>, <code>rfc2217://10.0.0.20:4001</code>,
			<code>socket://10.0.0.20:4001</code> (no hardware break over a raw socket — use <code>===</code>).
			Break: <code>hardware</code> or the text sent as a software break, usually <code>===</code>.
			Which instrument is primary follows from its orientation, not from the order here.</p>
		</fieldset>
		<fieldset><legend>ADCP setup</legend>
			<table class="table"><tr><th></th><th>Cmd</th><th>Primary</th><th>Secondary</th></tr>${roles}</table>
		</fieldset>`;
	$("config-form").dataset.loaded = "1";
	formDirty = false;
}

function collectForm() {
	const cfg = JSON.parse(JSON.stringify(status.config));
	const form = $("config-form");
	for (const el of form.querySelectorAll("[name]")) {
		const [kind, ...rest] = el.name.split(":");
		let value;
		if (el.type === "checkbox") value = el.checked;
		else if (el.tagName === "TEXTAREA") value = el.value.split("\n").map((s) => s.trim()).filter(Boolean);
		else if (el.type === "number") value = el.value === "" ? null : Number(el.value);
		else value = el.value;
		if (kind === "g") {
			const path = rest[0];
			if (path === "download.baud") value = value === "" ? null : Number(value);
			setPath(cfg, path, value);
		} else if (kind === "i") {
			const [n, key] = rest;
			cfg.instruments[n][key] = key === "baud" ? Number(value) : value;
		} else if (kind === "r") {
			const [role, key] = rest;
			cfg[role][key] = value;
		}
	}
	return cfg;
}

$("config-form").addEventListener("input", () => { formDirty = true; $("cfg-msg").textContent = "unsaved changes"; $("cfg-msg").className = ""; });
$("cfg-save").onclick = async (e) => {
	e.preventDefault();
	try {
		await api("/config", {method: "PUT", body: JSON.stringify(collectForm())});
		formDirty = false;
		$("cfg-msg").textContent = "saved";
		$("cfg-msg").className = "ok";
		await refreshStatus();
		renderForm();
	} catch (err) {
		$("cfg-msg").textContent = err.message;
		$("cfg-msg").className = "err";
	}
};
$("cfg-reload").onclick = (e) => { e.preventDefault(); renderForm(); $("cfg-msg").textContent = ""; };
$("cfg-preview").onclick = async (e) => {
	e.preventDefault();
	try {
		const p = await api("/preview");
		const box = $("preview");
		box.classList.remove("hidden");
		box.innerHTML = Object.entries(p).map(([name, d]) =>
			`<div><b>${esc(name)}</b> as ${esc(d.role)}<pre>${d.commands.map((c) =>
				`${c.cmd.padEnd(18)} ${esc(c.what)}`).join("\n")}\n${status.config.start_command.padEnd(18)} start</pre></div>`).join("")
			+ `<p class="hint">Saved settings; roles from the last Detect (otherwise all as primary).</p>`;
	} catch (err) { alert(err.message); }
};
$("battery-reset").onclick = async () => {
	if (!confirm("Battery swapped? This resets the ping counter and writes it to the battery log.")) return;
	try { await post("/battery-reset"); } catch (e) { alert(e.message); }
	refreshStatus();
};

// ---- console ---------------------------------------------------------------------
async function refreshTranscript() {
	const name = $("console-inst").value;
	if (!name) return;
	if (name !== transcriptFor) { transcriptFor = name; transcriptSeq = 0; $("transcript").innerHTML = ""; }
	let lines;
	try { lines = await api(`/transcript/${encodeURIComponent(name)}?since=${transcriptSeq}`); } catch (_) { return; }
	if (!lines.length) return;
	const box = $("transcript");
	const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 20;
	box.insertAdjacentHTML("beforeend", lines.map((l) =>
		`<span class="${l.kind}">[${l.t.substring(11)}] ${l.kind === "tx" ? "&gt; " : ""}${esc(l.text)}</span>\n`).join(""));
	transcriptSeq = lines[lines.length - 1].seq;
	if (atBottom) box.scrollTop = box.scrollHeight;
}
$("console-form").onsubmit = async (e) => {
	e.preventDefault();
	const cmd = $("console-cmd").value.trim();
	if (!cmd) return;
	try {
		await post(`/console/${encodeURIComponent($("console-inst").value)}`, {command: cmd});
		$("console-cmd").value = "";
	} catch (err) { alert(err.message); }
	refreshTranscript();
};

// ---- log -------------------------------------------------------------------------
function renderLog() {
	const d = status.state.last_download;
	if (d) {
		const rows = Object.entries(d.summaries).map(([name, s]) => `<tr><td>${esc(name)}</td>
			<td>${esc((s.files || []).join(", "))}</td><td>${s.ensembles}</td>
			<td>${hhmmss(s.start)} – ${hhmmss(s.end)}</td><td>${fmt(s.ping_hours, 2)}</td>
			<td>${fmt(s.voltage_mean, 1)}${s.voltage_scaled ? " V" : ""} (${fmt(s.voltage_start, 1)} → ${fmt(s.voltage_end, 1)})</td>
			<td>${fmt(s.max_depth_m, 0)} / ${fmt(s.end_depth_m, 0)}</td></tr>`).join("");
		$("last-download").innerHTML = `<p>Profile <b>${pad3(d.profile)}</b> → <code>${esc(d.folder)}</code></p>
			<table class="table"><tr><th>Instrument</th><th>Files</th><th>Ensembles</th><th>Time (UTC)</th>
			<th>Ping h</th><th>Xmit voltage</th><th>Max / end depth (m)</th></tr>${rows}</table>`;
	}
	$("history").innerHTML = `<tr><th>Started (UTC)</th><th>Operation</th><th>Profile</th><th>Result</th></tr>` +
		status.history.slice().reverse().map((h) => `<tr><td>${hhmmss(h.started)}</td><td>${esc(h.kind)}</td>
			<td>${h.profile ? pad3(h.profile) : ""}</td><td>${esc(h.state)}${h.error ? ": " + esc(h.error) : ""}</td></tr>`).join("");
}

// ---- tabs and the loop -----------------------------------------------------------
document.querySelectorAll(".tabs button").forEach((b) => {
	b.onclick = () => {
		activeTab = b.dataset.tab;
		history.replaceState(null, "", activeTab === "live" ? location.pathname : "#" + activeTab);
		document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
		document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== "tab-" + activeTab));
		if (activeTab === "setup" && !formDirty) renderForm();
		if (activeTab === "console") {
			const sel = $("console-inst");
			if (!sel.options.length && status)
				sel.innerHTML = status.instruments.map((i) => `<option>${esc(i.name)}</option>`).join("");
			refreshTranscript();
		}
		if (activeTab === "log" && status) renderLog();
		if (activeTab === "live") drawAll();
	};
});
$("console-inst").onchange = refreshTranscript;
window.addEventListener("hashchange", openTabFromHash);
function openTabFromHash() {
	const b = document.querySelector(`.tabs button[data-tab="${location.hash.slice(1)}"]`);
	if (b) b.click();
}

setInterval(() => { $("h-clock").textContent = new Date().toISOString().substring(11, 19); }, 250);

async function first() {
	await refreshStatus();
	openTabFromHash();
}

async function loop() {
	await refreshStatus();
	if (activeTab === "live") await refreshLive();
	if (activeTab === "console") await refreshTranscript();
	setTimeout(loop, busy() ? 700 : 1500);
}
first().then(loop);
})();
