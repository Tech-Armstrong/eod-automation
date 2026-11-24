// `npm run dev`: the web app on http://localhost:3000 with live reload, plus the Python API it talks to.
// - If the API (server.py) is already running on API_URL (default http://127.0.0.1:8000), it is reused.
// - Otherwise it is started from the project folder with the venv's Python, and stopped again with Ctrl+C.
// `npm run dev:web` starts only the web app.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const project = path.resolve(frontend, "..");
const API_URL = (process.env.API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const win = process.platform === "win32";

function findPython() {
  const candidates = win
    ? ["venv/Scripts/python.exe", ".venv/Scripts/python.exe"]
    : ["venv/bin/python", ".venv/bin/python"];
  const found = candidates.map((c) => path.join(project, c)).find((p) => existsSync(p));
  return found || (win ? "python" : "python3");
}

async function apiUp() {
  try {
    const res = await fetch(`${API_URL}/api/me`, { signal: AbortSignal.timeout(1500) });
    return res.ok;
  } catch {
    return false;
  }
}

async function waitForApi(seconds) {
  for (let i = 0; i < seconds; i++) {
    if (await apiUp()) return true;
    await new Promise((r) => setTimeout(r, 1000));
  }
  return false;
}

const children = [];
function run(cmd, args, opts, label) {
  const child = spawn(cmd, args, { stdio: "inherit", ...opts });
  child.on("exit", (code) => {
    if (!stopping) {
      console.log(`\n[dev] ${label} stopped (exit ${code}). Stopping everything.`);
      stop(code ?? 1);
    }
  });
  children.push(child);
  return child;
}

let stopping = false;
function stop(code = 0) {
  stopping = true;
  for (const c of children) {
    if (c.exitCode === null) {
      if (win) spawn("taskkill", ["/pid", String(c.pid), "/T", "/F"], { stdio: "ignore" });
      else c.kill("SIGINT");
    }
  }
  setTimeout(() => process.exit(code), 500);
}
process.on("SIGINT", () => stop(0));
process.on("SIGTERM", () => stop(0));

if (await apiUp()) {
  console.log(`[dev] Using the API that is already running at ${API_URL}`);
} else {
  const python = findPython();
  console.log(`[dev] Starting the API: ${python} server.py  (in ${project})`);
  const url = new URL(API_URL);
  run(python, ["server.py"], {
    cwd: project,
    env: { ...process.env, PORT: url.port || "8000", PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8" },
  }, "API (server.py)");
  if (!(await waitForApi(90))) {
    console.log("[dev] The API didn't come up within 90 seconds; the page will show errors until it does.");
  }
}

console.log("[dev] Starting the web app on http://localhost:3000");
const nextBin = path.join(frontend, "node_modules", "next", "dist", "bin", "next");
run(process.execPath, [nextBin, "dev", "-p", "3000"], { cwd: frontend }, "Web app (next dev)");
