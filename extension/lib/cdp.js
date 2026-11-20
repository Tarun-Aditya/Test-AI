// Real browser input through chrome.debugger (the DevTools protocol): the clicks, key presses and mouse moves reach the page as
// trusted events, like a person's. That is what makes hover menus, :hover styles and links that open new tabs behave.
// Chrome shows its "Autopilot started debugging this browser" bar while a run is attached.
const sessions = new Map();           // tabId -> { dialog: handler }

export const send = (tabId, method, params = {}) => chrome.debugger.sendCommand({ tabId }, method, params);

export async function attach(tabId, onDialog) {
  if (!sessions.has(tabId)) {
    try { await chrome.debugger.attach({ tabId }, "1.3"); }
    catch (e) { if (!/already attached/i.test(String(e.message))) throw e; }
    await send(tabId, "Page.enable");
    sessions.set(tabId, { onDialog });
  } else {
    sessions.get(tabId).onDialog = onDialog;
  }
}

export async function detach(tabId) {
  if (!sessions.delete(tabId)) return;
  try { await chrome.debugger.detach({ tabId }); } catch (e) { /* tab closed */ }
}

export const attached = (tabId) => sessions.has(tabId);

// The tab's picture, frame by frame (Chrome sends one when the page changes): it becomes the run's video on the hub.
export async function startScreencast(tabId, onFrame) {
  const s = sessions.get(tabId); if (!s) return;
  s.onFrame = onFrame;
  await send(tabId, "Page.startScreencast", { format: "jpeg", quality: 60, maxWidth: 1280, maxHeight: 800, everyNthFrame: 1 });
}

chrome.debugger.onEvent.addListener((src, method, params) => {
  const s = sessions.get(src.tabId);
  if (s && method === "Page.screencastFrame") {
    try { s.onFrame && s.onFrame(params.data); } catch (e) { /* the reporter is gone */ }
    send(src.tabId, "Page.screencastFrameAck", { sessionId: params.sessionId }).catch(() => {});
    return;
  }
  if (s && method === "Page.javascriptDialogOpening" && s.onDialog) {
    Promise.resolve(s.onDialog(params)).then((accept) =>
      send(src.tabId, "Page.handleJavaScriptDialog", { accept: accept !== false, promptText: params.defaultPrompt || "" })).catch(() => {});
  }
});
chrome.debugger.onDetach.addListener((src) => sessions.delete(src.tabId));

// ------------------------------------------------------------------------------------------------ mouse
const mouse = (tabId, type, x, y, extra = {}) => send(tabId, "Input.dispatchMouseEvent", { type, x, y, ...extra });

export async function move(tabId, x, y) {
  await mouse(tabId, "mouseMoved", x - 2, y - 2);
  await mouse(tabId, "mouseMoved", x, y);
}

export async function click(tabId, x, y, { button = "left", count = 1 } = {}) {
  await mouse(tabId, "mouseMoved", x, y);
  for (let n = 1; n <= count; n++) {
    await mouse(tabId, "mousePressed", x, y, { button, clickCount: n, buttons: button === "right" ? 2 : 1 });
    await mouse(tabId, "mouseReleased", x, y, { button, clickCount: n });
  }
}

export async function drag(tabId, from, to) {
  await mouse(tabId, "mouseMoved", from.x, from.y);
  await mouse(tabId, "mousePressed", from.x, from.y, { button: "left", clickCount: 1, buttons: 1 });
  const steps = 8;
  for (let i = 1; i <= steps; i++) {
    await mouse(tabId, "mouseMoved", from.x + (to.x - from.x) * i / steps, from.y + (to.y - from.y) * i / steps, { buttons: 1 });
  }
  await mouse(tabId, "mouseReleased", to.x, to.y, { button: "left", clickCount: 1 });
}

// ------------------------------------------------------------------------------------------------ keyboard
export const insertText = (tabId, text) => send(tabId, "Input.insertText", { text: String(text) });

const NAMED = {
  Enter: { code: "Enter", vk: 13, text: "\r" }, Tab: { code: "Tab", vk: 9 }, Escape: { code: "Escape", vk: 27 }, Esc: { key: "Escape", code: "Escape", vk: 27 },
  Backspace: { code: "Backspace", vk: 8 }, Delete: { code: "Delete", vk: 46 }, Space: { key: " ", code: "Space", vk: 32, text: " " },
  ArrowLeft: { code: "ArrowLeft", vk: 37 }, ArrowUp: { code: "ArrowUp", vk: 38 }, ArrowRight: { code: "ArrowRight", vk: 39 }, ArrowDown: { code: "ArrowDown", vk: 40 },
  Home: { code: "Home", vk: 36 }, End: { code: "End", vk: 35 }, PageUp: { code: "PageUp", vk: 33 }, PageDown: { code: "PageDown", vk: 34 },
};
const MODS = { alt: 1, control: 2, ctrl: 2, meta: 4, command: 4, cmd: 4, shift: 8 };
const COMMANDS = { a: ["selectAll"], c: ["copy"], v: ["paste"], x: ["cut"], z: ["undo"] };

// "Enter", "Tab", "Control+A", "Shift+Tab", "a"
export async function press(tabId, spec) {
  const parts = String(spec).split("+").map((p) => p.trim()).filter(Boolean);
  const keyName = parts.pop() || "+";
  let modifiers = 0;
  for (const m of parts) modifiers |= MODS[m.toLowerCase()] || 0;
  const named = NAMED[keyName] || NAMED[keyName[0].toUpperCase() + keyName.slice(1)];
  const single = !named && keyName.length === 1;
  const key = named ? (named.key || keyName) : keyName;
  const code = named ? named.code : (single ? (/[a-z]/i.test(keyName) ? "Key" + keyName.toUpperCase() : /\d/.test(keyName) ? "Digit" + keyName : "") : "");
  const vk = named ? named.vk : (single ? keyName.toUpperCase().charCodeAt(0) : 0);
  const text = named ? named.text : (single && !(modifiers & ~8) ? keyName : undefined);
  const base = { key, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, modifiers };
  const cmds = single && (modifiers & 6) ? COMMANDS[keyName.toLowerCase()] : undefined;
  await send(tabId, "Input.dispatchKeyEvent", { type: text !== undefined ? "keyDown" : "rawKeyDown", ...base, ...(text !== undefined ? { text } : {}), ...(cmds ? { commands: cmds } : {}) });
  await send(tabId, "Input.dispatchKeyEvent", { type: "keyUp", ...base });
}

// ------------------------------------------------------------------------------------------------ pictures
export async function screenshot(tabId) {
  const r = await send(tabId, "Page.captureScreenshot", { format: "jpeg", quality: 55 });
  return "data:image/jpeg;base64," + r.data;
}
