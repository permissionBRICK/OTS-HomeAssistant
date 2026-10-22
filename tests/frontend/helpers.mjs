// Shared test helpers: load the catalog + sanitized snapshot fixture.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
export const FRONTEND = path.join(ROOT, "custom_components/ochsner_local_ots/frontend");
export const lib = (name) => path.join(FRONTEND, "lib", name);

let cache;
export function fixtures() {
  if (!cache) {
    cache = {
      catalog: JSON.parse(fs.readFileSync(path.join(ROOT, "custom_components/ochsner_local_ots/data/discovery_catalog.json"), "utf8")),
      snapshot: JSON.parse(fs.readFileSync(path.join(ROOT, "tests/fixtures/airhawk518_snapshot.json"), "utf8")),
    };
  }
  return cache;
}
