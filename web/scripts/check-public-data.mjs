import { readFile, readdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import process from "node:process";

const root = path.resolve(process.cwd(), "public", "data");
const required = ["overview.json", "markets.json", "track-record.json", "system-status.json", "manifest.json"];
const forbidden = ["postgresql://", "postgres://", "DATABASE_URL", "SESSION_SECRET", "PASSWORD_HASH", "$2a$", "$2b$", "$2y$"];

function fail(message) {
  console.error(`Public data check failed: ${message}`);
  process.exitCode = 1;
}

async function readJson(file) {
  const fullPath = path.join(root, file);
  if (!existsSync(fullPath)) {
    fail(`missing ${file}. Run \"make export-public\" first.`);
    return null;
  }
  const text = await readFile(fullPath, "utf8");
  for (const marker of forbidden) {
    if (text.includes(marker)) fail(`${file} contains forbidden marker ${marker}`);
  }
  try {
    return JSON.parse(text);
  } catch (error) {
    fail(`${file} is not valid JSON: ${error instanceof Error ? error.message : String(error)}`);
    return null;
  }
}

for (const file of required) await readJson(file);

const marketsDocument = await readJson("markets.json");
const markets = marketsDocument?.markets;
if (!Array.isArray(markets)) {
  fail("markets.json must contain a markets array");
} else {
  const slugs = new Set();
  for (const market of markets) {
    if (!market || typeof market.slug !== "string" || !market.slug) {
      fail("every market summary requires a non-empty slug");
      continue;
    }
    if (slugs.has(market.slug)) fail(`duplicate market slug ${market.slug}`);
    slugs.add(market.slug);
    const detail = await readJson(path.join("markets", `${market.slug}.json`));
    if (detail?.market?.slug !== market.slug) fail(`detail slug mismatch for ${market.slug}`);
  }

  const detailDir = path.join(root, "markets");
  if (existsSync(detailDir)) {
    const extraFiles = (await readdir(detailDir)).filter((name) => name.endsWith(".json") && !slugs.has(name.slice(0, -5)));
    if (extraFiles.length) fail(`orphaned market detail files: ${extraFiles.join(", ")}`);
  }
}

if (!process.exitCode) {
  console.log(`Public data check passed: ${Array.isArray(markets) ? markets.length : 0} market profiles and ${required.length} shared files.`);
}
