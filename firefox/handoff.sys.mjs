/* browser-handoff, privileged part (imported by browser-handoff.cfg at Firefox startup).
 *
 * Adds one command line flag: `firefox --handoff-restore FILE`. FILE holds Firefox session-store
 * windows ({"windows": [...]}, written by browser_handoff.py). They are restored with
 * SessionStore.setWindowState, the same code Firefox uses after a crash, so every tab comes back
 * with its back/forward history, scroll position and form text. Works when Firefox is already
 * running (the flag is forwarded to it) and when it starts with the flag.
 */
const CID = Components.ID("{6c1f3b7e-2a4d-4e55-9a0b-7d1c8e9f4b21}");
const CONTRACT = "@agent-mesh/browser-handoff-clh;1";
const BLANK = new Set(["about:blank", "about:newtab", "about:home", "about:privatebrowsing"]);
// Firefox 150+ serves these from moz-src:///, older versions from resource:///modules/.
function importFirst(...urls) {
  for (const url of urls) {
    try {
      return ChromeUtils.importESModule(url);
    } catch (e) {}
  }
  throw new Error("browser-handoff: none of " + urls.join(", ") + " loads");
}
const lazy = {
  get SessionStore() {
    return importFirst("moz-src:///browser/components/sessionstore/SessionStore.sys.mjs",
      "resource:///modules/sessionstore/SessionStore.sys.mjs").SessionStore;
  },
  get BrowserWindowTracker() {
    return importFirst("resource:///modules/BrowserWindowTracker.sys.mjs",
      "moz-src:///browser/components/BrowserWindowTracker.sys.mjs").BrowserWindowTracker;
  },
};

function log(msg) {
  Services.console.logStringMessage("browser-handoff: " + msg);
}

function isBlankWindow(win) {
  const tabs = win.gBrowser.tabs;
  if (tabs.length !== 1) return false;
  const url = tabs[0].linkedBrowser.currentURI.spec;
  return BLANK.has(url) || url === win.HomePage?.get?.(win);
}

async function newWindow() {
  let top = lazy.BrowserWindowTracker.getTopWindow({ private: false });
  if (!top) {
    // Started with the flag: wait for the first window.
    await new Promise(resolve => {
      const obs = subject => {
        Services.obs.removeObserver(obs, "browser-delayed-startup-finished");
        resolve();
      };
      Services.obs.addObserver(obs, "browser-delayed-startup-finished");
    });
    top = lazy.BrowserWindowTracker.getTopWindow({ private: false });
  }
  await lazy.SessionStore.promiseAllWindowsRestored;
  if (isBlankWindow(top)) return top;
  const win = top.OpenBrowserWindow({ private: false });
  await win.delayedStartupPromise;
  return win;
}

async function restore(path) {
  try {
    const state = JSON.parse(await IOUtils.readUTF8(path));
    if (!state.windows?.length) throw new Error("no windows in " + path);
    const win = await newWindow();
    // overwrite=true replaces the (blank) tabs of `win`; extra windows in the state open as new windows.
    lazy.SessionStore.setWindowState(win, JSON.stringify(state), true);
    win.focus();
    log(`restored ${state.windows.length} window(s) from ${path}`);
    await IOUtils.writeUTF8(path + ".done", String(Date.now()));
  } catch (e) {
    Cu.reportError("browser-handoff: " + e);
    try { await IOUtils.writeUTF8(path + ".error", String(e)); } catch (_) {}
  }
}

const handler = {
  QueryInterface: ChromeUtils.generateQI(["nsICommandLineHandler"]),
  handle(cmdLine) {
    let path;
    try {
      path = cmdLine.handleFlagWithParam("handoff-restore", false);
    } catch (e) {
      return;
    }
    if (!path) return;
    // A forwarded command line would otherwise also open a default window; at first launch
    // Firefox must still open its normal window, so only skip it for remote calls.
    if (cmdLine.state !== Ci.nsICommandLine.STATE_INITIAL_LAUNCH) cmdLine.preventDefault = true;
    restore(path);
  },
  helpInfo: "  --handoff-restore <file>  Restore browser-handoff windows from <file>.\n",
};
const factory = {
  createInstance(iid) { return handler.QueryInterface(iid); },
  QueryInterface: ChromeUtils.generateQI(["nsIFactory"]),
};
const reg = Components.manager.QueryInterface(Ci.nsIComponentRegistrar);
export function register() {
  if (reg.isCIDRegistered(CID)) return;
  reg.registerFactory(CID, "browser-handoff command line handler", CONTRACT, factory);
  // Sorted by name: runs before Firefox's own "m-browser" handler, so preventDefault counts.
  Services.catMan.addCategoryEntry("command-line-handler", "b-browser-handoff", CONTRACT, false, true);
}
