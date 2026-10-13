// Electron main process: a hidden, frameless, transparent orb window; tray; single-instance toggle.
const { app, BrowserWindow, Tray, Menu, ipcMain, screen, session } = require("electron");
const path = require("node:path");

const BACKEND = process.env.VI_BACKEND_URL || "http://127.0.0.1:7860";
const DEFAULTS = { name: "Nova", size: 128, position: "top-right" }; // same as backend/config.py
app.setName("voice-instinct"); // Wayland app_id / X11 class, used by compositor window rules

if (!app.requestSingleInstanceLock()) {
  app.exit(0); // the running instance got our argv (--toggle) through "second-instance"
  return;
}

let win = null;
let tray = null;
let muted = false;
let status = "Not connected (is the backend running?)"; // set by the renderer

// At login the backend may still be loading its models: start with the defaults, keep asking, and
// apply the user's size and position once it answers.
async function guiConfig() {
  for (;;) {
    try {
      const r = await fetch(`${BACKEND}/gui-config`);
      if (r.ok) return { ...DEFAULTS, ...(await r.json()) };
    } catch {}
    await new Promise((res) => setTimeout(res, 2000));
  }
}

function placement(cfg) {
  const { x, y, width, height } = screen.getPrimaryDisplay().workArea;
  const s = cfg.size, m = 24;
  const spots = {
    "bottom-right": [x + width - s - m, y + height - s - m],
    "bottom-left": [x + m, y + height - s - m],
    "top-right": [x + width - s - m, y + m],
    "top-left": [x + m, y + m],
    center: [x + Math.round((width - s) / 2), y + Math.round((height - s) / 2)],
  };
  return spots[cfg.position] || spots["bottom-right"];
}

function toggle() {
  if (win) win.webContents.send("toggle");
}

function createWindow() {
  const cfg = DEFAULTS;
  const [px, py] = placement(cfg);
  win = new BrowserWindow({
    width: cfg.size, height: cfg.size, x: px, y: py,
    frame: false, transparent: true, resizable: false, alwaysOnTop: true, skipTaskbar: true,
    show: false, focusable: false, hasShadow: false, backgroundColor: "#00000000",
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  win.setAlwaysOnTop(true, "floating");
  win.webContents.on("did-finish-load", () => win.webContents.send("mute", muted)); // page reloads forget it
  // Nothing but our own page: no navigation, no new windows.
  win.webContents.on("will-navigate", (e) => e.preventDefault());
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  win.loadFile(path.join(__dirname, "renderer", "index.html"), { query: { backend: BACKEND } });
  guiConfig().then((c) => {
    const [x, y] = placement(c);
    win.setBounds({ x, y, width: c.size, height: c.size });
  });
}

app.on("second-instance", (_event, argv) => {
  if (argv.includes("--toggle")) toggle();
});

app.whenReady().then(async () => {
  // Only the microphone, only for our own page.
  const ours = (wc) => wc === win?.webContents;
  session.defaultSession.setPermissionRequestHandler((wc, permission, cb) => cb(permission === "media" && ours(wc)));
  session.defaultSession.setPermissionCheckHandler((wc, permission) => permission === "media" && ours(wc));
  createWindow();
  tray = new Tray(path.join(__dirname, "icon.png"));
  const menu = () => Menu.buildFromTemplate([
    { label: status, enabled: false },
    { label: "Show / Hide", click: toggle },
    { label: "Mute microphone", type: "checkbox", checked: muted, click: (item) => { muted = item.checked; win.webContents.send("mute", muted); } },
    { type: "separator" },
    { label: "Quit", click: () => app.quit() },
  ]);
  const refresh = () => { tray.setToolTip(`voice-instinct: ${status}`); tray.setContextMenu(menu()); };
  refresh();
  ipcMain.on("status", (_e, text) => { status = text; refresh(); });
  if (process.argv.includes("--toggle")) toggle();
});

ipcMain.on("visible", (_e, visible) => {
  if (!win) return;
  if (visible) win.showInactive(); else win.hide();
});

app.on("window-all-closed", (e) => e.preventDefault()); // stay in the tray
