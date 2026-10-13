// The only bridge between the sandboxed page and the main process.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("vi", {
  setVisible: (visible) => ipcRenderer.send("visible", Boolean(visible)),
  setStatus: (text) => ipcRenderer.send("status", String(text)),
  onToggle: (fn) => ipcRenderer.on("toggle", () => fn()),
  onMute: (fn) => ipcRenderer.on("mute", (_e, muted) => fn(Boolean(muted))),
});
