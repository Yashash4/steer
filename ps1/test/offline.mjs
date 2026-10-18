// node test/offline.mjs : builds out/playable.html from the sample and runs the headless offline check (needs uv + Playwright in code/)
import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
execFileSync("node", ["build_playable.mjs", "sample/claims.json", "out/playable.html"], { cwd: root, stdio: "inherit" });
execFileSync("uv", ["run", "python", "ps1/test/offline_check.py", "ps1/out/playable.html", "ps1/out/shots"], { cwd: join(root, ".."), stdio: "inherit" });
