// Preload bridge between the Electron main process and the (server-rendered) web app.
// Runs in an isolated world but shares the page DOM, so it can react to main-process events
// without the web app needing to know it's inside Electron.

const { ipcRenderer } = require("electron");

// Global quick-search hotkey (registered in main.js) → focus the search box, or go home if the
// current page has none.
ipcRenderer.on("scryme:focus-search", () => {
  const input = document.querySelector('input[name="q"]');
  if (input) {
    input.focus();
    input.select();
  } else {
    window.location.href = "/";
  }
});
