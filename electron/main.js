const { app, BrowserWindow } = require("electron");
const { spawn } = require("child_process");
const path = require("path");

let mainWindow;
let pythonProcess;

function startPythonBackend() {
  const serverPath = path.join(__dirname, "..", "server.py");
  pythonProcess = spawn("python", [serverPath], {
    cwd: path.join(__dirname, ".."),
    stdio: ["ignore", "pipe", "pipe"],
  });

  pythonProcess.stderr.on("data", (data) => {
    console.log("[Python]", data.toString());
  });

  return new Promise((resolve) => {
    // 等待后端启动
    const check = () => {
      const http = require("http");
      http.get("http://127.0.0.1:8765/health", (res) => {
        resolve();
      }).on("error", () => {
        setTimeout(check, 500);
      });
    };
    setTimeout(check, 1000);
  });
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 900,
    height: 650,
    minWidth: 500,
    minHeight: 400,
    title: "Uki",
    backgroundColor: "#1a1a2e",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  mainWindow.loadFile(path.join(__dirname, "..", "ui", "index.html"));
}

app.whenReady().then(async () => {
  await startPythonBackend();
  await createWindow();
});

app.on("window-all-closed", () => {
  if (pythonProcess) pythonProcess.kill();
  app.quit();
});
