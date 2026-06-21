// Minimal, safe bridge between the HUD renderer and the Electron main process.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("overlay", {
  getConfig: () => ipcRenderer.invoke("get-config"),
  setClickThrough: (enabled) => ipcRenderer.invoke("set-clickthrough", enabled),
  quit: () => ipcRenderer.invoke("quit-overlay"),
  onClickThroughChanged: (cb) =>
    ipcRenderer.on("clickthrough-changed", (_e, v) => cb(v)),
});
