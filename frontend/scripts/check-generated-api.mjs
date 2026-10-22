import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const cliPath = resolve(frontendRoot, "node_modules/openapi-typescript/bin/cli.js");
const generatedPath = resolve(frontendRoot, "src/api/generated.ts");
const tempDirectory = mkdtempSync(join(tmpdir(), "ai-marking-api-check-"));
const tempGeneratedPath = join(tempDirectory, "generated.ts");

try {
  const result = spawnSync(
    process.execPath,
    [cliPath, "src/api/openapi.json", "-o", tempGeneratedPath],
    { cwd: frontendRoot, stdio: "inherit" },
  );

  if (result.error) {
    throw result.error;
  }

  if (result.status !== 0) {
    process.exitCode = result.status ?? 1;
  } else if (!readFileSync(generatedPath).equals(readFileSync(tempGeneratedPath))) {
    console.error("Generated API types are out of date. Run `npm run api:generate`.");
    process.exitCode = 1;
  } else {
    console.log("Generated API types match the current OpenAPI schema.");
  }
} finally {
  rmSync(tempDirectory, { recursive: true, force: true });
}
