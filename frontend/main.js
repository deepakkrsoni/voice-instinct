// Electron main process: a hidden, frameless, transparent orb window; tray; single-instance toggle.
const { app, BrowserWindow, Tray, Menu, ipcMain, screen, session } = require("electron");
const path = require("node:path");

const BACKEND = process.env.VI_BACKEND_URL || "http://127.0.0.1:7860";
const DEFAULTS = { name: "Nova", size: 160, position: "bottom-right" };
app.setName("voice-instinct"); // Wayland app_id / X11 class, used by compositor window rules

if (!app.requestSingleInstanceLock()) app.quit();

let win = null;
let tray = null;
let muted = false;

async function guiConfig() {
  try {
    const r = await fetch(`${BACKEND}/gui-config`);
    return { ...DEFAULTS, ...(await r.json()) };
  } catch {
    return DEFAULTS;
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

async function createWindow() {
  const cfg = await guiConfig();
  const [px, py] = placement(cfg);
  win = new BrowserWindow({
    width: cfg.size, height: cfg.size, x: px, y: py,
    frame: false, transparent: true, resizable: false, alwaysOnTop: true, skipTaskbar: true,
    show: false, focusable: false, hasShadow: false, backgroundColor: "#00000000",
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  win.setAlwaysOnTop(true, "floating");
  win.webContents.on("did-finish-load", () => win.webContents.send("mute", muted)); // page reloads forget it
  win.loadFile(path.join(__dirname, "renderer", "index.html"), { query: { backend: BACKEND } });
}

app.on("second-instance", (_event, argv) => {
  if (argv.includes("--toggle")) toggle();
});

app.whenReady().then(async () => {
  // Only the microphone, only for our own page.
  session.defaultSession.setPermissionRequestHandler((_wc, permission, cb) => cb(permission === "media"));
  await createWindow();
  tray = new Tray(path.join(__dirname, "icon.png"));
  const menu = () => Menu.buildFromTemplate([
    { label: "Show", click: toggle },
    { label: "Mute microphone", type: "checkbox", checked: muted, click: (item) => { muted = item.checked; win.webContents.send("mute", muted); } },
    { type: "separator" },
    { label: "Quit", click: () => app.quit() },
  ]);
  tray.setToolTip("voice-instinct");
  tray.setContextMenu(menu());
  if (process.argv.includes("--toggle")) toggle();
});

ipcMain.on("visible", (_e, visible) => {
  if (!win) return;
  if (visible) win.showInactive(); else win.hide();
});

app.on("window-all-closed", (e) => e.preventDefault()); // stay in the tray
