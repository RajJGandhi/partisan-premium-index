import {
  demoMarketDetail,
  demoMarkets,
  demoOverview,
  demoSystemStatus,
  demoTrackRecord,
} from "../data/demo";
import type {
  MarketDetailData,
  MarketsData,
  OverviewData,
  SystemStatusData,
  TrackRecordData,
} from "./types";

const cache = new Map<string, unknown>();

function dataUrl(path: string): string {
  const base = import.meta.env.BASE_URL.endsWith("/")
    ? import.meta.env.BASE_URL
    : `${import.meta.env.BASE_URL}/`;
  return `${base}data/${path}`;
}

async function fetchJson<T>(path: string, fallback: T): Promise<T> {
  if (cache.has(path)) return cache.get(path) as T;
  try {
    const response = await fetch(dataUrl(path), {
      headers: { Accept: "application/json" },
      cache: "no-cache",
    });
    if (!response.ok) throw new Error(`Data request failed (${response.status})`);
    const payload = (await response.json()) as T;
    cache.set(path, payload);
    return payload;
  } catch (error) {
    if (import.meta.env.DEV) {
      console.warn(`Using development demo data for ${path}`, error);
      cache.set(path, fallback);
      return fallback;
    }
    throw error;
  }
}

export const publicData = {
  overview: () => fetchJson<OverviewData>("overview.json", demoOverview),
  markets: () => fetchJson<MarketsData>("markets.json", demoMarkets),
  trackRecord: () => fetchJson<TrackRecordData>("track-record.json", demoTrackRecord),
  systemStatus: () => fetchJson<SystemStatusData>("system-status.json", demoSystemStatus),
  market: (slug: string) =>
    fetchJson<MarketDetailData>(`markets/${encodeURIComponent(slug)}.json`, {
      ...demoMarketDetail,
      market: { ...demoMarketDetail.market, slug },
    }),
};
