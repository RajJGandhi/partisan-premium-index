import { Search, SlidersHorizontal, X } from "lucide-react";
import { useMemo, useState } from "react";
import { DataStamp } from "../components/DataStamp";
import { MarketCard } from "../components/MarketCard";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState, EmptyState } from "../components/StateViews";
import { usePublicData } from "../hooks/usePublicData";
import { publicData } from "../lib/data";
import type { MarketSummary } from "../lib/types";

const sorters: Record<string, (a: MarketSummary, b: MarketSummary) => number> = {
  "absolute-premium": (a, b) => Math.abs(b.partisan_premium ?? -1) - Math.abs(a.partisan_premium ?? -1),
  "premium-high": (a, b) => (b.partisan_premium ?? -Infinity) - (a.partisan_premium ?? -Infinity),
  "market-high": (a, b) => (b.market_probability ?? -Infinity) - (a.market_probability ?? -Infinity),
  liquidity: (a, b) => (b.liquidity ?? -Infinity) - (a.liquidity ?? -Infinity),
  alphabetical: (a, b) => (a.question ?? "").localeCompare(b.question ?? ""),
};

export function MarketsPage() {
  const { data, loading, error } = usePublicData(publicData.markets, []);
  const [query, setQuery] = useState("");
  const [region, setRegion] = useState("all");
  const [category, setCategory] = useState("all");
  const [freshness, setFreshness] = useState("all");
  const [publication, setPublication] = useState("all");
  const [sort, setSort] = useState("absolute-premium");

  const regions = useMemo(() => [...new Set((data?.markets ?? []).map((market) => market.region).filter(Boolean) as string[])].sort(), [data]);
  const categories = useMemo(() => [...new Set((data?.markets ?? []).map((market) => market.category).filter(Boolean) as string[])].sort(), [data]);

  const filtered = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return [...(data?.markets ?? [])]
      .filter((market) => !normalizedQuery || [market.question, market.region, market.category, market.tracking_id].some((value) => value?.toLowerCase().includes(normalizedQuery)))
      .filter((market) => region === "all" || market.region === region)
      .filter((market) => category === "all" || market.category === category)
      .filter((market) => freshness === "all" || (freshness === "fresh" ? !market.is_stale : market.is_stale))
      .filter((market) => publication === "all" || (publication === "published" ? market.ppi_fair_value != null : market.ppi_fair_value == null))
      .sort(sorters[sort] ?? sorters["absolute-premium"]);
  }, [data, query, region, category, freshness, publication, sort]);

  function resetFilters() {
    setQuery("");
    setRegion("all");
    setCategory("all");
    setFreshness("all");
    setPublication("all");
    setSort("absolute-premium");
  }

  if (loading) return <div className="shell-width page-space"><LoadingState label="Loading market directory…" /></div>;
  if (error || !data) return <div className="shell-width page-space"><ErrorState error={error ?? new Error("Markets unavailable")} /></div>;

  return (
    <div className="shell-width page-space">
      <PageHeader
        eyebrow="Market directory"
        title="Track every published disagreement."
        description="Search and compare live political markets, PPI fair values, current premiums, liquidity, freshness and public evidence."
        actions={<DataStamp generatedAt={data.generated_at} />}
      />

      <section className="filter-panel" aria-label="Market filters">
        <label className="search-field">
          <Search size={18} aria-hidden="true" />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search markets, regions or categories" />
          {query ? <button type="button" onClick={() => setQuery("")} aria-label="Clear search"><X size={16} /></button> : null}
        </label>
        <div className="filter-grid">
          <label><span>Region</span><select value={region} onChange={(event) => setRegion(event.target.value)}><option value="all">All regions</option>{regions.map((item) => <option key={item}>{item}</option>)}</select></label>
          <label><span>Category</span><select value={category} onChange={(event) => setCategory(event.target.value)}><option value="all">All categories</option>{categories.map((item) => <option key={item}>{item}</option>)}</select></label>
          <label><span>Freshness</span><select value={freshness} onChange={(event) => setFreshness(event.target.value)}><option value="all">All data</option><option value="fresh">Fresh only</option><option value="stale">Stale only</option></select></label>
          <label><span>Fair value</span><select value={publication} onChange={(event) => setPublication(event.target.value)}><option value="all">All markets</option><option value="published">Published</option><option value="awaiting">Awaiting publication</option></select></label>
          <label><span>Sort</span><select value={sort} onChange={(event) => setSort(event.target.value)}><option value="absolute-premium">Largest disagreement</option><option value="premium-high">Premium high to low</option><option value="market-high">Market probability</option><option value="liquidity">Liquidity</option><option value="alphabetical">Alphabetical</option></select></label>
        </div>
      </section>

      <div className="directory-toolbar">
        <div><SlidersHorizontal size={16} /><strong>{filtered.length}</strong> of {data.markets.length} markets</div>
        <button className="text-button" type="button" onClick={resetFilters}>Reset filters</button>
      </div>

      {filtered.length ? (
        <section className="market-grid">
          {filtered.map((market) => <MarketCard key={market.slug} market={market} />)}
        </section>
      ) : (
        <EmptyState title="No markets match these filters" description="Reset the filters or broaden the search to return to the full market universe." action={<button className="button button--secondary" type="button" onClick={resetFilters}>Reset filters</button>} />
      )}
    </div>
  );
}
