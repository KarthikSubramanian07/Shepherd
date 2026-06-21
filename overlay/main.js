// Shepherd Overlay — Electron main process.
//
// A translucent, frameless, always-on-top window that floats over everything
// (including a fullscreen app being driven) and renders Shepherd's live agent
// activity as a compact terminal HUD. `setContentProtection(true)` keeps it
// INVISIBLE to screen capture / share / recording, so an audience sees the Mac
// being driven but not this control panel.
//
// It is a thin shell: the HUD page (index.html) talks directly to the Shepherd
// dashboard backend (WebSocket /ws for events, POST /api/intent + /api/halt for
// control), so no Shepherd backend coupling lives here beyond the URL.

const {
  app, BrowserWindow, ipcMain, globalShortcut, screen, Tray, Menu, nativeImage,
} = require("electron");
const path = require("node:path");

// Keep the renderer's timers/WebSocket alive even when the HUD isn't focused.
app.commandLine.appendSwitch("disable-background-timer-throttling");

// Where the Shepherd dashboard backend lives. Override with SHEPHERD_URL.
const SHEPHERD_URL = process.env.SHEPHERD_URL || "http://localhost:8765";

// ── Visibility toggles ──────────────────────────────────────────────────────
// Transparent windows can fail to composite on some macOS/GPU setups (the whole
// window renders invisible). OVERLAY_DEBUG launches a plain, opaque, framed,
// centered window with devtools so we can confirm rendering. OVERLAY_NO_PROTECT
// turns OFF content protection so the HUD shows in screen captures too.
const DEBUG = !!process.env.OVERLAY_DEBUG;
const TRANSPARENT = !DEBUG && process.env.OVERLAY_OPAQUE !== "1";
const NO_PROTECT = DEBUG || process.env.OVERLAY_NO_PROTECT === "1";

let win = null;
let tray = null;
let clickThrough = false;

function createWindow() {
  const primary = screen.getPrimaryDisplay();
  const { width: sw } = primary.workAreaSize;

  win = new BrowserWindow({
    width: 420,
    height: 560,
    // Top-right corner by default — out of the way of whatever is being driven.
    x: DEBUG ? undefined : sw - 440,
    y: DEBUG ? undefined : 24,
    center: DEBUG,
    minWidth: 300,
    minHeight: 220,
    // Show immediately. Gating on `ready-to-show` is unreliable for transparent
    // windows on macOS (the event can fail to fire → the window stays hidden).
    show: true,
    frame: DEBUG ? true : false,
    transparent: TRANSPARENT,
    hasShadow: DEBUG ? true : false,
    resizable: true,
    movable: true,
    alwaysOnTop: true,
    fullscreenable: false,
    skipTaskbar: true,
    backgroundColor: TRANSPARENT ? "#00000000" : "#0b0e14",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Hide from screen capture / share / recording — the whole point of the HUD.
  // Skipped when NO_PROTECT so it shows in captures (and during debugging).
  if (!NO_PROTECT) win.setContentProtection(true);

  if (process.platform === "darwin") {
    win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    win.setAlwaysOnTop(true, "floating");
    if (win.setHiddenInMissionControl) win.setHiddenInMissionControl(true);
  } else {
    win.setAlwaysOnTop(true);
  }

  win.loadFile(path.join(__dirname, "index.html"), {
    query: { shepherd: SHEPHERD_URL },
  });

  // Belt-and-suspenders: force the window visible and to the front once the page
  // has loaded, regardless of whether `ready-to-show` fired.
  const reveal = () => {
    if (!win || win.isDestroyed()) return;
    win.show();
    win.moveTop();
    win.setAlwaysOnTop(true, "screen-saver");
    const b = win.getBounds();
    console.log(
      `[overlay] reveal: visible=${win.isVisible()} bounds=${b.x},${b.y} ${b.width}x${b.height} ` +
      `transparent=${TRANSPARENT} protect=${!NO_PROTECT}`
    );
    if (DEBUG) win.webContents.openDevTools({ mode: "detach" });
  };
  win.webContents.on("did-finish-load", reveal);
  win.once("ready-to-show", reveal);
  setTimeout(reveal, 1200);

  win.webContents.on("did-fail-load", (_e, code, desc) => {
    console.error("[overlay] failed to load HUD:", code, desc);
  });
  win.on("closed", () => {
    win = null;
  });
}

function applyClickThrough(enabled) {
  clickThrough = enabled;
  if (win && !win.isDestroyed()) {
    win.setIgnoreMouseEvents(enabled, { forward: true });
  }
  return clickThrough;
}

// Force the HUD into view: recreate if needed, move it onto the display under
// the cursor, place it top-right there, then show + focus + assert top level.
// This is the reliable "I can't find the window" escape hatch — it works even
// when the window opened on another Space or display.
function summon() {
  if (!win || win.isDestroyed()) {
    createWindow();
    return;
  }
  const cursor = screen.getCursorScreenPoint();
  const disp = screen.getDisplayNearestPoint(cursor);
  const wa = disp.workArea;
  const b = win.getBounds();
  win.setBounds({
    x: wa.x + wa.width - b.width - 16,
    y: wa.y + 16,
    width: b.width,
    height: b.height,
  });
  if (process.platform === "darwin") {
    win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  }
  win.show();
  win.focus();
  win.moveTop();
  win.setAlwaysOnTop(true, "screen-saver");
}

function createTray() {
  if (tray) return;
  try {
    tray = new Tray(nativeImage.createEmpty());
    if (process.platform === "darwin") tray.setTitle(" 🐑 Shepherd");
    tray.setToolTip("Shepherd Overlay — ⌘⇧Space to summon");
    tray.setContextMenu(
      Menu.buildFromTemplate([
        { label: "Show / Summon (⌘⇧Space)", click: summon },
        {
          label: "Toggle visibility (⌘⇧H)",
          click: () => {
            if (win && win.isVisible()) win.hide();
            else summon();
          },
        },
        {
          label: "Toggle click-through (⌘⇧C)",
          click: () => {
            applyClickThrough(!clickThrough);
            if (win) win.webContents.send("clickthrough-changed", clickThrough);
          },
        },
        { type: "separator" },
        { label: "Quit", accelerator: "Command+Q", click: () => app.quit() },
      ])
    );
    tray.on("double-click", summon);
  } catch (e) {
    console.error("[overlay] tray init failed:", e);
  }
}

function registerShortcuts() {
  // Summon — bring the HUD to the front on the current display.
  globalShortcut.register("CommandOrControl+Shift+Space", summon);
  // Toggle show/hide.
  globalShortcut.register("CommandOrControl+Shift+H", () => {
    if (win && win.isVisible()) win.hide();
    else summon();
  });
  // Toggle click-through (so the HUD doesn't block the app underneath).
  globalShortcut.register("CommandOrControl+Shift+C", () => {
    applyClickThrough(!clickThrough);
    if (win) win.webContents.send("clickthrough-changed", clickThrough);
  });
}

ipcMain.handle("set-clickthrough", (_e, enabled) => applyClickThrough(!!enabled));
ipcMain.handle("quit-overlay", () => app.quit());
ipcMain.handle("get-config", () => ({ shepherdUrl: SHEPHERD_URL }));

app.whenReady().then(() => {
  // Run as a macOS accessory app (no dock icon) — the proper mode for an
  // always-on-top HUD. The tray icon remains the way to summon/quit it.
  if (process.platform === "darwin" && app.dock) app.dock.hide();
  createWindow();
  createTray();
  registerShortcuts();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
    else summon();
  });
});

app.on("will-quit", () => globalShortcut.unregisterAll());
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
