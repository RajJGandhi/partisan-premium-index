from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlencode

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    EvidenceItem,
    FairValueProposal,
    JobRun,
    Market,
    MarketSnapshot,
    SourceRun,
)


@dataclass(frozen=True)
class DigestResult:
    markdown: str
    dated_path: Path
    latest_path: Path
    summary: dict[str, object]


@dataclass(frozen=True)
class PriceMovement:
    market: Market
    current: MarketSnapshot
    previous: MarketSnapshot | None
    delta: float | None


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _points(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.1f} pp"


def _escape(value: object) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def _market_link(market_id: int) -> str:
    base = get_settings().app_base_url.rstrip("/")
    return f"{base}/?{urlencode({'page': 'Market detail', 'market_id': market_id})}"


def _admin_link() -> str:
    base = get_settings().app_base_url.rstrip("/")
    return f"{base}/?{urlencode({'page': 'Administration'})}"


def _snapshot_pairs(session: Session, day: date) -> list[tuple[Market, MarketSnapshot, MarketSnapshot | None]]:
    todays = list(
        session.scalars(
            select(MarketSnapshot)
            .where(
                MarketSnapshot.snapshot_kind == "daily",
                MarketSnapshot.snapshot_date == day,
            )
            .order_by(MarketSnapshot.market_id)
        )
    )
    pairs: list[tuple[Market, MarketSnapshot, MarketSnapshot | None]] = []
    for current in todays:
        market = session.get(Market, current.market_id)
        if not market:
            continue
        previous = session.scalar(
            select(MarketSnapshot)
            .where(
                MarketSnapshot.market_id == current.market_id,
                MarketSnapshot.snapshot_kind == "daily",
                MarketSnapshot.snapshot_date < day,
            )
            .order_by(desc(MarketSnapshot.snapshot_date), desc(MarketSnapshot.timestamp))
            .limit(1)
        )
        pairs.append((market, current, previous))
    return pairs


def build_daily_digest(session: Session, job: JobRun, day: date) -> tuple[str, dict[str, object]]:
    start, end = _day_bounds(day)
    pairs = _snapshot_pairs(session, day)

    movements: list[PriceMovement] = []
    for pair_market, pair_current, pair_previous in pairs:
        delta = None
        if pair_previous and pair_current.comparison_price is not None and pair_previous.comparison_price is not None:
            delta = pair_current.comparison_price - pair_previous.comparison_price
        movements.append(PriceMovement(pair_market, pair_current, pair_previous, delta))
    movements.sort(key=lambda row: abs(row.delta or 0), reverse=True)
    material_movements = [row for row in movements if row.delta is not None and abs(row.delta) >= 0.02]

    evidence = list(
        session.scalars(
            select(EvidenceItem)
            .where(
                EvidenceItem.discovered_at >= start,
                EvidenceItem.discovered_at < end,
                EvidenceItem.relevant.is_(True),
            )
            .order_by(desc(EvidenceItem.relevance_score), desc(EvidenceItem.discovered_at))
            .limit(30)
        )
    )
    proposals = list(
        session.scalars(
            select(FairValueProposal)
            .where(
                FairValueProposal.created_at >= start,
                FairValueProposal.created_at < end,
                FairValueProposal.status == "PENDING",
            )
            .order_by(desc(FairValueProposal.confidence), FairValueProposal.created_at)
        )
    )
    failed_sources = list(
        session.scalars(
            select(SourceRun)
            .where(SourceRun.job_run_id == job.id, SourceRun.status == "FAILED")
            .order_by(SourceRun.source_name)
        )
    )
    stale_pairs = [row for row in pairs if row[1].is_stale or row[1].pipeline_status == "FAILED"]

    lines = [
        f"# PPI Daily Digest — {day.isoformat()}",
        "",
        f"Generated: {datetime.now(UTC).replace(microsecond=0).isoformat()}",
        f"Pipeline: **{job.status}** · {job.markets_succeeded}/{job.markets_attempted} markets succeeded · {job.error_count} errors",
        "",
        "## Important market-price movements",
        "",
    ]
    if material_movements:
        lines.extend(
            [
                "| Market | Previous | Current | Movement | PPI fair value | Premium |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for movement in material_movements[:12]:
            previous_snapshot = movement.previous
            assert previous_snapshot is not None
            lines.append(
                f"| [{_escape(movement.market.question)}]({_market_link(movement.market.id)}) | "
                f"{_percent(previous_snapshot.comparison_price)} | "
                f"{_percent(movement.current.comparison_price)} | "
                f"{_points(movement.delta)} | {_percent(movement.current.fair_value)} | "
                f"{_points(movement.current.partisan_premium)} |"
            )
    else:
        lines.append(
            "No tracked market moved by at least 2 percentage points versus its prior canonical daily snapshot."
        )

    lines.extend(["", "## Newly relevant evidence", ""])
    if evidence:
        lines.extend(
            [
                "| Market | Category | Relevance | Direction | Development | Source |",
                "| --- | --- | ---: | --- | --- | --- |",
            ]
        )
        for item in evidence[:15]:
            evidence_market = session.get(Market, item.market_id)
            market_name = evidence_market.question if evidence_market else str(item.market_id)
            source = f"[open]({item.canonical_url})" if item.canonical_url else _escape(item.source_name)
            lines.append(
                f"| [{_escape(market_name)}]({_market_link(item.market_id)}) | {_escape(item.category)} | "
                f"{_percent(item.relevance_score)} | {_escape(item.direction)} | "
                f"{_escape(item.summary or item.title)} | {source} |"
            )
    else:
        lines.append("No new evidence was classified as materially relevant today.")

    lines.extend(["", "## Proposed fair-value changes", ""])
    if proposals:
        lines.extend(
            [
                "| Market | Current | Proposed | Change | Confidence | Rationale |",
                "| --- | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for proposal in proposals:
            proposal_market = session.get(Market, proposal.market_id)
            market_name = proposal_market.question if proposal_market else str(proposal.market_id)
            delta = (
                proposal.proposed_fair_value - proposal.current_published_fair_value
                if proposal.current_published_fair_value is not None
                else None
            )
            lines.append(
                f"| [{_escape(market_name)}]({_market_link(proposal.market_id)}) | "
                f"{_percent(proposal.current_published_fair_value)} | {_percent(proposal.proposed_fair_value)} | "
                f"{_points(delta)} | {_percent(proposal.confidence)} | {_escape(proposal.rationale)} |"
            )
        lines.extend(["", f"[Open the approval queue]({_admin_link()})"])
    else:
        lines.append("No new fair-value proposal is awaiting approval.")

    lines.extend(["", "## Failed or stale sources", ""])
    if failed_sources or stale_pairs:
        if failed_sources:
            lines.append("### Source failures")
            for source_run in failed_sources[:25]:
                source_market = session.get(Market, source_run.market_id) if source_run.market_id else None
                lines.append(
                    f"- **{_escape(source_run.source_name)}** · "
                    f"{_escape(source_market.question if source_market else 'system')} · "
                    f"{_escape(source_run.sanitized_error or 'unknown failure')}"
                )
        if stale_pairs:
            lines.append("### Stale/failed markets")
            for market, snapshot, _previous in stale_pairs:
                lines.append(
                    f"- [{_escape(market.question)}]({_market_link(market.id)}) · "
                    f"{_escape(snapshot.freshness_status)} · {_escape(snapshot.status_message)}"
                )
    else:
        lines.append("No source failures or stale canonical snapshots were recorded in this run.")

    lines.extend(
        [
            "",
            "## Methodology note",
            "",
            "Automated collection can create recommendations, but it cannot silently publish a new PPI fair value. "
            "Every substantive fair-value change remains pending until an administrator approves or edits it.",
            "",
        ]
    )

    summary: dict[str, object] = {
        "date": day.isoformat(),
        "job_run_id": job.id,
        "job_status": job.status,
        "material_price_movements": len(material_movements),
        "relevant_evidence": len(evidence),
        "pending_proposals": len(proposals),
        "failed_sources": len(failed_sources),
        "stale_markets": len(stale_pairs),
        "approval_queue_url": _admin_link(),
    }
    return "\n".join(lines), summary


def write_daily_digest(session: Session, job: JobRun, day: date, output_dir: str | Path = "reports") -> DigestResult:
    markdown, summary = build_daily_digest(session, job, day)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    dated = root / f"ppi_daily_digest_{day.isoformat()}.md"
    latest = root / "ppi_daily_digest_latest.md"
    dated.write_text(markdown, encoding="utf-8")
    latest.write_text(markdown, encoding="utf-8")
    (root / "ppi_daily_digest_latest.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return DigestResult(markdown=markdown, dated_path=dated, latest_path=latest, summary=summary)
