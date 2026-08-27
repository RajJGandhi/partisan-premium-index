import type {
  V15CalibrationData,
  V15ProviderStatus,
  V15RaceDetail,
  V15RacesData,
} from "./v15Types";

const cache = new Map<string, unknown>();

function dataUrl(path: string): string {
  const base = import.meta.env.BASE_URL.endsWith("/")
    ? import.meta.env.BASE_URL
    : `${import.meta.env.BASE_URL}/`;
  return `${base}data/v15/${path}`;
}

/** Fetch a v1.5 data file. Returns `null` (not an error) when the file is absent -- the v1.5
 *  pipeline runs in shadow and its export may not exist yet on every deployment. */
async function fetchOptional<T>(path: string): Promise<T | null> {
  if (cache.has(path)) return cache.get(path) as T | null;
  try {
    const response = await fetch(dataUrl(path), { headers: { Accept: "application/json" }, cache: "no-cache" });
    if (response.status === 404) {
      cache.set(path, null);
      return null;
    }
    if (!response.ok) throw new Error(`v1.5 data request failed (${response.status})`);
    const payload = (await response.json()) as T;
    cache.set(path, payload);
    return payload;
  } catch (error) {
    if (import.meta.env.DEV) console.warn(`v1.5 data unavailable for ${path}`, error);
    cache.set(path, null);
    return null;
  }
}

export const v15Data = {
  races: () => fetchOptional<V15RacesData>("races.json"),
  race: (raceId: string) => fetchOptional<V15RaceDetail>(`race/${encodeURIComponent(raceId)}.json`),
  providerStatus: () => fetchOptional<V15ProviderStatus>("provider-status.json"),
  calibration: () => fetchOptional<V15CalibrationData>("calibration.json"),
};

export type V15SortKey =
  | "abs_spread"
  | "signed_spread"
  | "robustness"
  | "dispersion"
  | "data_quality"
  | "ensemble";

const ROBUSTNESS_RANK: Record<string, number> = { HIGH: 3, MEDIUM: 2, LOW: 1 };
const QUALITY_RANK: Record<string, number> = { STRONG: 4, NORMAL: 3, THIN: 2, DEGRADED: 1, ABSTAIN: 0 };

export function sortRaces<T extends {
  abs_spread: number | null;
  market_model_spread: number | null;
  robustness: string | null;
  dispersion: number | null;
  data_quality: string | null;
  ensemble_probability: number | null;
}>(rows: T[], key: V15SortKey): T[] {
  const score = (r: T): number => {
    switch (key) {
      case "abs_spread": return r.abs_spread ?? -1;
      case "signed_spread": return r.market_model_spread ?? -Infinity;
      case "robustness": return ROBUSTNESS_RANK[r.robustness ?? ""] ?? 0;
      case "dispersion": return r.dispersion ?? -1;
      case "data_quality": return QUALITY_RANK[r.data_quality ?? ""] ?? -1;
      case "ensemble": return r.ensemble_probability ?? -1;
    }
  };
  return [...rows].sort((a, b) => score(b) - score(a));
}
