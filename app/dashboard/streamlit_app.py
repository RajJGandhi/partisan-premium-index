from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import desc, func, select

from app.config import get_settings
from app.db.database import get_session, init_db
from app.db.models import (
    AdminUser,
    DailyIndex,
    EvidenceItem,
    FairValueComponent,
    FairValueProposal,
    FairValueRevision,
    JobRun,
    LLMForecast,
    Market,
    MarketSnapshot,
    MarketSource,
    Prediction,
    SourceRun,
)
from app.ppi.evidence import EvidenceCandidate, insert_and_classify_candidate
from app.ppi.llm_forecast_review import set_llm_forecast_review_status
from app.ppi.llm_forecast_view import (
    FRESHNESS_OK,
    build_forecast_rows,
    classify_forecast_freshness,
    evidence_items_for_forecast,
    forecast_rows_to_export_dataframe,
    latest_forecast_by_market,
)
from app.ppi.methodology import DEFAULT_WEIGHTS, validate_weights
from app.ppi.pipeline import run_daily_pipeline
from app.ppi.publication import publish_proposal, reject_proposal, resolve_market
from app.ppi.run_classification import is_canonical_and_current
from app.ppi.run_health import compute_run_health
from app.ppi.security import (
    validate_external_url,
    verify_password,
    verify_plaintext_password,
)

st.set_page_config(
    page_title="Partisan Premium Index",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)
init_db()
settings = get_settings()

st.markdown(
    """
<style>
:root { --ppi-accent:#7c3aed; --ppi-muted:#64748b; }
.block-container {padding-top: 1.5rem; padding-bottom: 3rem; max-width: 1500px;}
[data-testid="stMetric"] {background: color-mix(in srgb, var(--ppi-accent) 8%, transparent); border:1px solid rgba(124,58,237,.18); border-radius:14px; padding:14px;}
.ppi-hero {border:1px solid rgba(124,58,237,.25); border-radius:20px; padding:26px; background:linear-gradient(135deg,rgba(124,58,237,.13),rgba(14,165,233,.07)); margin-bottom:18px;}
.ppi-kicker {font-size:.75rem; letter-spacing:.14em; text-transform:uppercase; color:var(--ppi-accent); font-weight:700;}
.ppi-title {font-size:2.2rem; font-weight:800; margin:.2rem 0; line-height:1.05;}
.ppi-subtitle {color:var(--ppi-muted); max-width:900px;}
.ppi-badge {display:inline-block; padding:4px 9px; border-radius:999px; font-size:.75rem; font-weight:700; background:rgba(124,58,237,.12); margin-right:5px;}
.small-note {font-size:.82rem; color:var(--ppi-muted);}
</style>
""",
    unsafe_allow_html=True,
)


def pct(value, signed=False):
    if value is None or pd.isna(value):
        return "—"
    return f"{value:+.1%}" if signed else f"{value:.1%}"


def utcnow():
    return datetime.now(timezone.utc)


def safe_json(value, fallback):
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


CLASSIFICATION_LABELS = {
    "canonical": "Canonical",
    "noncanonical_mixed": "Noncanonical (mixed fallback)",
    "contaminated": "Contaminated",
    "failed": "Failed",
    "adhoc": "Adhoc",
}
CLASSIFICATION_HELP = (
    "Canonical: scheduled primary/backup run, live Qwen for every classification and forecast, "
    "zero fallback or contamination -- safe to publish. "
    "Noncanonical (mixed fallback): evidence classification may have silently used the "
    "deterministic classifier. "
    "Contaminated: a strict run whose evidence packet included an item classified by something "
    "other than a live model (reused via dedup from a different run). "
    "Failed: did not complete. "
    "Adhoc: a real, live manual/verification run outside the twice-daily schedule."
)


def classification_label(job) -> str:
    label = CLASSIFICATION_LABELS.get(job.run_classification, job.run_classification)
    return f"{label} (superseded)" if job.superseded_by_id else label


def latest_daily_snapshots(session):
    day_subq = (
        select(func.max(MarketSnapshot.snapshot_date)).where(MarketSnapshot.snapshot_kind == "daily").scalar_subquery()
    )
    return list(
        session.scalars(
            select(MarketSnapshot).where(
                MarketSnapshot.snapshot_kind == "daily",
                MarketSnapshot.snapshot_date == day_subq,
            )
        )
    )


def hero():
    st.markdown(
        """<div class="ppi-hero"><div class="ppi-kicker">Prediction-market research</div>
        <div class="ppi-title">Partisan Premium Index</div>
        <div class="ppi-subtitle">A transparent, timestamped comparison of executable Polymarket probabilities and independently published fair values. No wallet execution. No silent model-driven revisions.</div></div>""",
        unsafe_allow_html=True,
    )


def require_admin(session) -> bool:
    if st.session_state.get("admin_authenticated"):
        return True
    st.subheader("Administrator sign-in")
    st.caption("Authentication is verified server-side. Use a bcrypt hash in production.")
    with st.form("admin_login"):
        username = st.text_input("Username", value=settings.ppi_admin_username)
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        ok = username == settings.ppi_admin_username and (
            verify_password(password, settings.ppi_admin_password_hash)
            or (settings.app_env != "production" and verify_plaintext_password(password, settings.ppi_admin_password))
        )
        if not ok:
            user = session.scalar(select(AdminUser).where(AdminUser.username == username))
            ok = bool(user and user.active and verify_password(password, user.password_hash))
            if ok:
                user.last_login_at = utcnow()
                session.commit()
        if ok:
            st.session_state["admin_authenticated"] = True
            st.session_state["admin_username"] = username
            st.rerun()
        st.error("Invalid credentials.")
    return False


hero()
pages = [
    "Overview",
    "Markets",
    "Market detail",
    "LLM Forecasts",
    "Track record",
    "Methodology",
    "System status",
    "Administration",
]
requested_page = st.query_params.get("page", "Overview")
initial_page_index = pages.index(requested_page) if requested_page in pages else 0
page = st.sidebar.radio("Navigate", pages, index=initial_page_index)
if st.query_params.get("page") != page:
    st.query_params["page"] = page
st.sidebar.caption("Public research product · UTC snapshots")

with get_session() as session:
    if page == "Overview":
        latest_index = session.scalar(select(DailyIndex).order_by(desc(DailyIndex.index_date)).limit(1))
        snapshots = latest_daily_snapshots(session)
        enabled_count = session.scalar(select(func.count(Market.id)).where(Market.enabled.is_(True))) or 0
        stale_count = sum(1 for s in snapshots if s.is_stale)
        last_job = session.scalar(
            select(JobRun).where(JobRun.job_name == "daily_pipeline").order_by(desc(JobRun.started_at)).limit(1)
        )
        if stale_count or (last_job and last_job.status == "FAILED"):
            st.error(
                f"Data warning: {stale_count} current snapshot(s) are stale or unavailable. Confirm system status before interpreting premiums."
            )
        cols = st.columns(5)
        cols[0].metric("Tracked outcomes", enabled_count)
        cols[1].metric(
            "Aggregate signed PPI",
            pct(latest_index.average_signed_premium, True) if latest_index else "—",
        )
        cols[2].metric(
            "Average absolute premium",
            pct(latest_index.average_absolute_premium) if latest_index else "—",
        )
        cols[3].metric(
            "Above fair value",
            pct(latest_index.share_above_fair_value) if latest_index else "—",
        )
        cols[4].metric(
            "Last successful update",
            last_job.finished_at.strftime("%b %d %H:%M UTC") if last_job and last_job.finished_at else "Never",
        )

        st.subheader("Largest current premiums")
        rows = []
        for snap in sorted(snapshots, key=lambda s: abs(s.partisan_premium or 0), reverse=True)[:10]:
            market = session.get(Market, snap.market_id)
            rows.append(
                {
                    "Market": market.question if market else snap.market_id,
                    "Market probability": pct(snap.comparison_price),
                    "Published fair value": pct(snap.fair_value),
                    "Premium": pct(snap.partisan_premium, True),
                    "Spread": pct(snap.spread),
                    "Freshness": snap.freshness_status,
                    "Pipeline": snap.pipeline_status,
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        c1, c2 = st.columns([1.2, 1])
        with c1:
            st.subheader("What the index measures")
            st.markdown(
                "**Partisan premium = executable market probability − published PPI fair value.** Positive values mean the market is trading above the independent fair value; negative values mean below. Published fair values change only through the approval ledger."
            )
        with c2:
            st.subheader("Current methodology")
            st.markdown(
                "35% polling · 25% forecast/fundamentals · 20% comparable markets · 10% expert consensus · 10% campaign/news judgment"
            )
            st.caption(
                "Missing inputs are visibly redistributed only when eligible; substantive changes require approval."
            )

    elif page == "Markets":
        snapshots = {s.market_id: s for s in latest_daily_snapshots(session)}
        markets = list(session.scalars(select(Market).where(Market.enabled.is_(True)).order_by(Market.question)))
        rows = []
        for m in markets:
            s = snapshots.get(m.id)
            evidence_count = (
                session.scalar(
                    select(func.count(EvidenceItem.id)).where(
                        EvidenceItem.market_id == m.id, EvidenceItem.relevant.is_(True)
                    )
                )
                or 0
            )
            rows.append(
                {
                    "ID": m.tracking_id,
                    "Market": m.question,
                    "Region": m.region or "—",
                    "Market probability": s.comparison_price if s else None,
                    "PPI fair value": s.fair_value if s else None,
                    "Partisan premium": s.partisan_premium if s else None,
                    "Spread": s.spread if s else None,
                    "Evidence": evidence_count,
                    "Freshness": s.freshness_status if s else "MISSING",
                    "Status": m.status,
                }
            )
        df = pd.DataFrame(rows)
        f1, f2, f3 = st.columns(3)
        region = f1.multiselect("Region", sorted(df["Region"].dropna().unique()) if not df.empty else [])
        freshness = f2.multiselect(
            "Freshness",
            sorted(df["Freshness"].dropna().unique()) if not df.empty else [],
        )
        sort_by = f3.selectbox(
            "Sort",
            [
                "Absolute premium",
                "Premium high to low",
                "Liquidity/freshness",
                "Market name",
            ],
        )
        if region:
            df = df[df["Region"].isin(region)]
        if freshness:
            df = df[df["Freshness"].isin(freshness)]
        if not df.empty:
            premium_numeric = pd.to_numeric(
                df["Partisan premium"],
                errors="coerce",
            )

            if sort_by == "Absolute premium":
                df = (
                    df.assign(_sort=premium_numeric.abs())
                    .sort_values(
                        "_sort",
                        ascending=False,
                        na_position="last",
                    )
                    .drop(columns="_sort")
                )
            elif sort_by == "Premium high to low":
                df = (
                    df.assign(_sort=premium_numeric)
                    .sort_values(
                        "_sort",
                        ascending=False,
                        na_position="last",
                    )
                    .drop(columns="_sort")
                )
            elif sort_by == "Market name":
                df = df.sort_values("Market")
            df["Market probability"] = df["Market probability"].map(pct)
            df["PPI fair value"] = df["PPI fair value"].map(pct)
            df["Partisan premium"] = df["Partisan premium"].map(lambda x: pct(x, True))
            df["Spread"] = df["Spread"].map(pct)
        st.dataframe(df, use_container_width=True, hide_index=True)

    elif page == "Market detail":
        markets = list(session.scalars(select(Market).where(Market.enabled.is_(True)).order_by(Market.question)))
        if not markets:
            st.info("No markets configured. Seed the production universe first.")
        else:
            labels = {f"{m.tracking_id or m.id} · {m.question}": m.id for m in markets}
            requested_market_id = st.query_params.get("market_id")
            default_market_index = 0
            if requested_market_id:
                for idx, label in enumerate(labels):
                    if str(labels[label]) == str(requested_market_id):
                        default_market_index = idx
                        break
            selected = st.selectbox("Tracked outcome", list(labels), index=default_market_index)
            market = session.get(Market, labels[selected])
            st.query_params["market_id"] = str(market.id)
            st.subheader(market.question)
            st.markdown(
                f'<span class="ppi-badge">{market.region or "Global"}</span><span class="ppi-badge">{market.status}</span>',
                unsafe_allow_html=True,
            )
            st.write(market.description or "No description supplied.")
            with st.expander("Resolution criteria"):
                st.write(market.rules or "No resolution criteria supplied.")
                if market.resolution_source:
                    st.caption(f"Resolution source: {market.resolution_source}")
            history = list(
                session.scalars(
                    select(MarketSnapshot)
                    .where(
                        MarketSnapshot.market_id == market.id,
                        MarketSnapshot.snapshot_kind == "daily",
                    )
                    .order_by(MarketSnapshot.snapshot_date)
                )
            )
            if history:
                fig = go.Figure()
                fig.add_trace(
                    go.Scatter(
                        x=[h.snapshot_date for h in history],
                        y=[h.comparison_price for h in history],
                        name="Polymarket",
                        mode="lines+markers",
                    )
                )
                fig.add_trace(
                    go.Scatter(
                        x=[h.snapshot_date for h in history],
                        y=[h.fair_value for h in history],
                        name="PPI fair value",
                        mode="lines+markers",
                    )
                )
                fig.update_yaxes(tickformat=".0%", range=[0, 1])
                fig.update_layout(
                    height=420,
                    margin=dict(l=10, r=10, t=30, b=10),
                    legend_orientation="h",
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No published daily history yet.")
            components = list(
                session.scalars(
                    select(FairValueComponent)
                    .where(FairValueComponent.market_id == market.id)
                    .order_by(FairValueComponent.component_type)
                )
            )
            revisions = list(
                session.scalars(
                    select(FairValueRevision)
                    .where(FairValueRevision.market_id == market.id)
                    .order_by(desc(FairValueRevision.published_at))
                )
            )
            evidence = list(
                session.scalars(
                    select(EvidenceItem)
                    .where(
                        EvidenceItem.market_id == market.id,
                        EvidenceItem.relevant.is_(True),
                    )
                    .order_by(
                        desc(EvidenceItem.published_at),
                        desc(EvidenceItem.discovered_at),
                    )
                    .limit(100)
                )
            )
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Component breakdown")
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Component": c.component_type,
                                "Probability": pct(c.probability),
                                "Weight": pct(c.weight),
                                "Source": c.source_label,
                                "Observed": c.observed_at,
                                "Method": c.ingestion_method,
                            }
                            for c in components
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
                st.subheader("Current thesis")
                st.write(market.current_thesis or "No fair-value thesis has been published.")
            with c2:
                st.subheader("Revision history")
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Revision": r.revision_number,
                                "Fair value": pct(r.fair_value),
                                "Previous": pct(r.previous_fair_value),
                                "Published": r.published_at,
                                "By": r.published_by,
                                "Justification": r.justification,
                            }
                            for r in revisions
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            st.subheader("Evidence timeline")
            for item in evidence:
                with st.expander(f"{item.published_at.date() if item.published_at else 'Undated'} · {item.title}"):
                    st.write(item.summary or item.reason)
                    st.caption(
                        f"Category: {item.category} · relevance {item.relevance_score or 0:.0%} · source quality {item.source_quality or 0:.0%} · review {item.review_status}"
                    )
                    if item.canonical_url:
                        st.link_button("Open source", item.canonical_url)

    elif page == "LLM Forecasts":
        st.header("Primary blind-LLM (Qwen) forecast history")
        st.caption(
            "Every row is an immutable, append-only observation from the primary blind-LLM series — "
            "at most one per market per twice-daily run slot. The model never sees Polymarket prices "
            "when it generates a forecast; the Polymarket price and PPI-gap columns below are attached "
            "only after the forecast row was already stored, and read “not yet joined” until then."
        )

        all_markets = list(session.scalars(select(Market).order_by(Market.question)))
        markets_by_id = {m.id: m for m in all_markets}
        all_forecasts = list(session.scalars(select(LLMForecast).order_by(desc(LLMForecast.generated_at))))

        enabled_markets = [m for m in all_markets if m.enabled]
        latest_by_market = latest_forecast_by_market(all_forecasts)
        freshness_counts: dict[str, int] = {}
        problem_rows = []
        for m in enabled_markets:
            state = classify_forecast_freshness(
                latest_by_market.get(m.id), stale_hours=float(settings.app_stale_hours)
            )
            freshness_counts[state] = freshness_counts.get(state, 0) + 1
            if state != FRESHNESS_OK:
                latest = latest_by_market.get(m.id)
                problem_rows.append(
                    {
                        "Market": m.question,
                        "State": state,
                        "Latest slot": latest.run_slot if latest else "—",
                        "Latest generation status": latest.status if latest else "—",
                        "Reason": (latest.error_message if latest else None) or "No forecast has ever been generated.",
                    }
                )
        problem_count = sum(v for k, v in freshness_counts.items() if k != FRESHNESS_OK)
        if problem_count:
            st.warning(
                f"{problem_count} of {len(enabled_markets)} tracked market(s) lack a fresh, successful "
                f"blind-LLM forecast — breakdown: {freshness_counts}."
            )
            with st.expander("Markets needing attention", expanded=False):
                st.dataframe(pd.DataFrame(problem_rows), use_container_width=True, hide_index=True)
        elif enabled_markets:
            st.success("Every tracked market has a fresh, successful blind-LLM forecast.")

        st.subheader("History")
        market_options = {"All markets": None}
        market_options.update({f"{m.tracking_id or m.id} · {m.question}": m.id for m in all_markets})
        f1, f2, f3 = st.columns([2, 2, 1.4])
        selected_market_label = f1.selectbox("Market", list(market_options), key="llm_forecast_market_filter")
        selected_market_id = market_options[selected_market_label]

        filtered = (
            all_forecasts
            if selected_market_id is None
            else [f for f in all_forecasts if f.market_id == selected_market_id]
        )

        if filtered:
            available_dates = [f.generated_at.date() for f in filtered]
            min_date, max_date = min(available_dates), max(available_dates)
        else:
            min_date = max_date = utcnow().date()
        date_range = f2.date_input(
            "Date range (UTC)",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
            key="llm_forecast_date_filter",
        )
        start_date, end_date = date_range if isinstance(date_range, tuple) and len(date_range) == 2 else (date_range, date_range)
        filtered = [f for f in filtered if start_date <= f.generated_at.date() <= end_date]

        status_options = sorted({f.status for f in filtered})
        status_filter = f3.multiselect("Generation status", status_options, key="llm_forecast_status_filter")
        if status_filter:
            filtered = [f for f in filtered if f.status in status_filter]

        filtered.sort(key=lambda f: f.generated_at, reverse=True)
        rows = build_forecast_rows(filtered, markets_by_id)

        run_keys = {f.run_key for f in filtered}
        jobs_by_run_key = {
            j.run_key: j
            for j in session.scalars(select(JobRun).where(JobRun.run_key.in_(run_keys)))
        }

        display_df = pd.DataFrame(
            [
                {
                    "Market": r.market_label,
                    "Run slot": r.run_slot,
                    "Run classification": (
                        classification_label(jobs_by_run_key[forecast.run_key])
                        if forecast.run_key in jobs_by_run_key
                        else "Unknown"
                    ),
                    "Generated (UTC)": r.generated_at,
                    "Generation status": r.generation_status,
                    "Review flag": r.reviewed_status,
                    "Fair value": pct(r.fair_value),
                    "Confidence": pct(r.confidence),
                    "Approx. interval": (
                        f"{pct(r.interval_low)} – {pct(r.interval_high)}" if r.interval_low is not None else "—"
                    ),
                    "Abstained": r.should_abstain,
                    "Model": r.model_name,
                    "Prompt version": r.prompt_version,
                    "Retries": r.retries,
                    "Polymarket price": pct(r.comparison_price_at_join) if r.joined_at else "not yet joined",
                    "PPI gap (raw)": pct(r.raw_ppi, True) if r.joined_at else "not yet joined",
                }
                for r, forecast in zip(rows, filtered, strict=True)
            ]
        )
        st.dataframe(display_df, use_container_width=True, hide_index=True)
        st.caption(
            "“Approx. interval” is a derived visual band from the model’s self-reported confidence score "
            "(half-width = (1 − confidence) / 2), not a statistical confidence interval the model produced."
        )

        export_df = forecast_rows_to_export_dataframe(rows)
        st.download_button(
            "Download filtered history (CSV)",
            data=export_df.to_csv(index=False).encode("utf-8"),
            file_name=f"llm_forecasts_{selected_market_id or 'all'}_{start_date}_{end_date}.csv",
            mime="text/csv",
            disabled=export_df.empty,
        )

        st.subheader("Historical probability")
        if selected_market_id is None:
            st.info("Select a single market above to see its historical probability chart.")
        elif not rows:
            st.info("No forecasts in the selected range.")
        else:
            chart_rows = sorted(rows, key=lambda r: r.generated_at)
            fig = go.Figure()
            band_rows = [r for r in chart_rows if r.interval_low is not None and r.interval_high is not None]
            if band_rows:
                fig.add_trace(
                    go.Scatter(
                        x=[r.generated_at for r in band_rows] + [r.generated_at for r in band_rows][::-1],
                        y=[r.interval_high for r in band_rows] + [r.interval_low for r in band_rows][::-1],
                        fill="toself",
                        fillcolor="rgba(124,58,237,0.12)",
                        line=dict(width=0),
                        name="Approx. confidence band",
                        hoverinfo="skip",
                    )
                )
            fig.add_trace(
                go.Scatter(
                    x=[r.generated_at for r in chart_rows],
                    y=[r.fair_value for r in chart_rows],
                    name="Blind LLM fair value",
                    mode="lines+markers",
                )
            )
            joined_rows = [r for r in chart_rows if r.joined_at is not None]
            if joined_rows:
                fig.add_trace(
                    go.Scatter(
                        x=[r.generated_at for r in joined_rows],
                        y=[r.comparison_price_at_join for r in joined_rows],
                        name="Polymarket price (at join)",
                        mode="lines+markers",
                    )
                )
            fig.update_yaxes(tickformat=".0%", range=[0, 1])
            fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10), legend_orientation="h")
            st.plotly_chart(fig, use_container_width=True)

        st.subheader("Forecast detail")
        detail_limit = 100
        if len(rows) > detail_limit:
            st.caption(
                f"Showing detail for the most recent {detail_limit} of {len(rows)} matching rows. "
                "Use CSV export above for the full filtered set."
            )
        for row, forecast in list(zip(rows, filtered, strict=True))[:detail_limit]:
            abstain_note = " · ABSTAINED" if row.should_abstain else ""
            with st.expander(f"{row.market_label} · {row.run_slot} · {row.generation_status}{abstain_note}"):
                c1, c2 = st.columns(2)
                with c1:
                    if row.fair_value is not None:
                        st.markdown(
                            f"**Fair value:** {pct(row.fair_value)}  \n**Confidence:** {pct(row.confidence)}  \n"
                            f"**Approx. interval (derived, not model-native):** {pct(row.interval_low)} – {pct(row.interval_high)}"
                        )
                    else:
                        st.info("No parsed forecast value for this row.")
                    st.markdown(
                        f"**Model version:** {row.model_name}  \n**Prompt version:** {row.prompt_version}  \n"
                        f"**Retries:** {row.retries}  \n**Generated (UTC):** {row.generated_at}"
                    )
                    st.markdown(
                        f"**Review flag:** {row.reviewed_status}  \n"
                        "A canonical OK/ABSTAINED forecast is public automatically; "
                        "FLAGGED is the only review state that suppresses public display."
                    )
                    if forecast.reviewed_by:
                        st.caption(
                            f"Reviewed by {forecast.reviewed_by} at {forecast.reviewed_at} — "
                            f"{forecast.review_notes or 'no notes'}"
                        )
                with c2:
                    if row.joined_at:
                        st.markdown(
                            f"**Polymarket price (at join):** {pct(row.comparison_price_at_join)}  \n"
                            f"**Raw PPI gap:** {pct(row.raw_ppi, True)}  \n**Joined (UTC):** {row.joined_at}"
                        )
                    else:
                        st.info("Not yet joined to a Polymarket price snapshot.")
                    if row.error_message:
                        st.error(row.error_message)
                if row.rationale:
                    st.markdown(f"**Rationale:** {row.rationale}")
                if row.key_uncertainties:
                    st.markdown("**Key uncertainties:** " + "; ".join(row.key_uncertainties))
                if row.base_rate_notes:
                    st.markdown(f"**Base rate notes:** {row.base_rate_notes}")
                evidence = evidence_items_for_forecast(session, forecast)
                if evidence:
                    st.markdown("**Evidence considered (with timestamps):**")
                    for item in evidence:
                        published = (
                            item.published_at.strftime("%Y-%m-%d %H:%M UTC") if item.published_at else "undated"
                        )
                        discovered = (
                            item.discovered_at.strftime("%Y-%m-%d %H:%M UTC") if item.discovered_at else "—"
                        )
                        st.caption(f"· {item.title} — published {published}, discovered {discovered}")
                else:
                    st.caption("No evidence items were referenced at generation time.")

    elif page == "Track record":
        predictions = list(session.scalars(select(Prediction).order_by(desc(Prediction.initial_publication_at))))
        resolved = [p for p in predictions if p.status == "RESOLVED"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Published predictions", len(predictions))
        c2.metric("Resolved", len(resolved))
        c3.metric(
            "PPI mean Brier",
            f"{sum(p.ppi_brier_score for p in resolved if p.ppi_brier_score is not None) / len(resolved):.3f}"
            if resolved
            else "—",
        )
        c4.metric(
            "Market mean Brier",
            f"{sum(p.market_brier_score for p in resolved if p.market_brier_score is not None) / len(resolved):.3f}"
            if resolved and all(p.market_brier_score is not None for p in resolved)
            else "—",
        )
        if len(resolved) < 30:
            st.warning(
                "The resolved sample is too small for strong calibration or superiority claims. Results are descriptive only."
            )
        rows = []
        for p in predictions:
            m = session.get(Market, p.market_id)
            rows.append(
                {
                    "Outcome market": m.question if m else p.market_id,
                    "Published": p.initial_publication_at,
                    "PPI": pct(p.initial_fair_value),
                    "Market probability": pct(p.initial_market_probability),
                    "Outcome": p.final_outcome,
                    "PPI Brier": p.ppi_brier_score,
                    "Market Brier": p.market_brier_score,
                    "PPI advantage": p.performance_difference,
                    "Status": p.status,
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        if resolved:
            bins = pd.cut(
                pd.Series([p.initial_fair_value for p in resolved]),
                bins=[0, 0.2, 0.4, 0.6, 0.8, 1],
                include_lowest=True,
            )
            calibration = (
                pd.DataFrame(
                    {
                        "bin": bins.astype(str),
                        "outcome": [p.final_outcome for p in resolved],
                    }
                )
                .groupby("bin", observed=False)
                .agg(predictions=("outcome", "size"), actual_rate=("outcome", "mean"))
                .reset_index()
            )
            st.subheader("Calibration")
            st.dataframe(calibration, use_container_width=True, hide_index=True)

    elif page == "Methodology":
        st.header("Methodology")
        st.markdown(
            "### Formula\n**Partisan premium = Polymarket executable/implied probability − published PPI fair value.**"
        )
        st.markdown("### Default fair-value components")
        st.dataframe(
            pd.DataFrame([{"Component": k.title(), "Default weight": pct(v)} for k, v in DEFAULT_WEIGHTS.items()]),
            hide_index=True,
            use_container_width=True,
        )
        st.markdown(
            "### Publication controls\nAutomated collection may refresh objective inputs and classify evidence, but it cannot silently change a published fair value. Proposed changes enter an approval queue. Approval creates an immutable revision and, on first publication, a permanent prediction-ledger entry."
        )
        st.markdown(
            "### Missing components\nMissing values are never replaced with an invented neutral probability. Eligible missing weight is redistributed across available inputs, while original and effective weights remain visible. Missingness triggers human review."
        )
        st.markdown(
            "### Evidence standards\nA source is relevant only when it could materially affect outcome probability, resolution, polling, ballot access, candidacy, election rules, or a major forecast input. Mentions alone are insufficient."
        )
        st.markdown(
            "### Limitations\nThe index reflects selected markets, human-supervised inputs, changing liquidity, possible source failures, and a small early track record. It is research—not financial advice or a trading system."
        )

    elif page == "System status":
        jobs = list(session.scalars(select(JobRun).order_by(desc(JobRun.started_at)).limit(50)))
        last = jobs[0] if jobs else None
        c1, c2, c3 = st.columns(3)
        c1.metric("Last run", last.status if last else "Never")
        c2.metric(
            "Last successful markets",
            f"{last.markets_succeeded}/{last.markets_attempted}" if last else "—",
        )
        c3.metric("Errors", last.error_count if last else 0)
        if last:
            metadata = safe_json(last.metadata_json, {})
            digest_markdown = metadata.get("daily_digest_markdown")
            if digest_markdown:
                with st.expander("Latest daily digest", expanded=False):
                    st.markdown(digest_markdown)

        st.subheader("Run classification")
        st.caption(CLASSIFICATION_HELP)
        counts: dict[str, int] = {}
        for j in jobs:
            counts[j.run_classification] = counts.get(j.run_classification, 0) + 1
        cols = st.columns(len(CLASSIFICATION_LABELS))
        for col, (key, label) in zip(cols, CLASSIFICATION_LABELS.items(), strict=True):
            col.metric(label, counts.get(key, 0))
        canonical_current = sum(1 for j in jobs if is_canonical_and_current(j))
        st.caption(f"{canonical_current} run(s) among the last {len(jobs)} are canonical and not superseded.")

        st.subheader("Forecast-outcome observability")
        st.caption(
            "Derived fresh from persisted LLMForecast/BlindIndexRun rows for the selected run, "
            "not from in-memory counters -- see app.ppi.run_health."
        )
        if jobs:
            run_key_options = [j.run_key for j in jobs]
            selected_run_key = st.selectbox("Run", run_key_options, index=0)
            selected_job = next(j for j in jobs if j.run_key == selected_run_key)
            health = compute_run_health(session, selected_job.id)
            h1, h2, h3, h4 = st.columns(4)
            h1.metric("Forecasts OK", health.forecasts_ok)
            h2.metric("Forecasts abstained", health.forecasts_abstained)
            h3.metric("Forecasts error", health.forecasts_error)
            h4.metric("LLM fallback count", health.llm_fallback_count)
            h5, h6, h7, h8 = st.columns(4)
            h5.metric("Evidence classified", f"{health.evidence_successful}/{health.evidence_attempted}")
            h6.metric("Snapshots persisted", health.snapshots_persisted)
            h7.metric("PPI rows persisted", health.ppi_rows_persisted)
            h8.metric("Blind-index rows", health.blind_index_rows_persisted)
            st.caption(f"Job run ID {health.job_run_id} · classification: {health.run_classification}")
        else:
            st.caption("No job runs yet.")

        st.subheader("Job history")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Run": j.run_key,
                        "Trigger": j.trigger_type,
                        "Started": j.started_at,
                        "Finished": j.finished_at,
                        "Status": j.status,
                        "Classification": classification_label(j),
                        "Superseded by": j.superseded_by_id,
                        "Markets": f"{j.markets_succeeded}/{j.markets_attempted}",
                        "Evidence": j.evidence_discovered,
                        "Evidence classification failed": j.evidence_classification_failed,
                        "Proposals": j.proposals_created,
                        "Errors": j.error_count,
                        "Error summary": j.sanitized_error,
                    }
                    for j in jobs
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        source_runs = list(session.scalars(select(SourceRun).order_by(desc(SourceRun.started_at)).limit(100)))
        st.subheader("Source freshness and failures")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Source": s.source_name,
                        "Market ID": s.market_id,
                        "Started": s.started_at,
                        "Finished": s.finished_at,
                        "Status": s.status,
                        "Discovered": s.items_discovered,
                        "Inserted": s.items_inserted,
                        "Error": s.sanitized_error,
                    }
                    for s in source_runs
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        st.caption("Public status intentionally excludes secrets, raw stack traces and sensitive environment values.")

    elif page == "Administration":
        if not require_admin(session):
            st.stop()
        top = st.columns([5, 1])
        top[0].success(f"Signed in as {st.session_state.get('admin_username')}")
        if top[1].button("Sign out"):
            st.session_state.pop("admin_authenticated", None)
            st.rerun()
        tabs = st.tabs(
            [
                "Markets",
                "Components",
                "Evidence",
                "Approval queue",
                "Sources",
                "Pipeline",
                "Resolve",
                "Failures",
                "LLM Forecasts",
            ]
        )
        markets = list(session.scalars(select(Market).order_by(Market.question)))
        market_options = {f"{m.tracking_id or m.id} · {m.question}": m.id for m in markets}

        with tabs[0]:
            st.subheader("Add or update a tracked market")
            with st.form("add_market"):
                tracking_id = st.text_input("Tracking ID")
                gamma_id = st.text_input("Polymarket Gamma market ID")
                slug = st.text_input("Polymarket slug")
                question = st.text_area("Question")
                yes_token = st.text_input("YES token ID")
                no_token = st.text_input("NO token ID")
                region = st.text_input("Region")
                submitted = st.form_submit_button("Save market")
            if submitted:
                if not question or not gamma_id:
                    st.error("Question and Gamma market ID are required.")
                else:
                    market = (
                        session.scalar(select(Market).where(Market.tracking_id == tracking_id)) if tracking_id else None
                    )
                    if not market:
                        market = Market(
                            tracking_id=tracking_id or None,
                            platform="polymarket",
                            platform_market_id=gamma_id,
                            question=question,
                        )
                        session.add(market)
                    (
                        market.slug,
                        market.yes_token_id,
                        market.no_token_id,
                        market.region,
                    ) = (
                        slug or None,
                        yes_token or None,
                        no_token or None,
                        region or None,
                    )
                    market.enabled, market.active, market.closed = True, True, False
                    st.success("Market saved.")
            if market_options:
                selected = st.selectbox(
                    "Edit tracked state and methodology",
                    list(market_options),
                    key="market_edit_select",
                )
                m = session.get(Market, market_options[selected])
                with st.form("edit_market"):
                    enabled = st.checkbox("Enabled", value=m.enabled)
                    thesis = st.text_area("Current thesis", value=m.current_thesis or "")
                    resolution_criteria = st.text_area("Resolution criteria", value=m.rules or "", height=140)
                    source_pack = st.text_area("Source pack JSON", value=m.source_pack_json or "{}", height=220)
                    weights = {
                        k: st.number_input(
                            f"{k.title()} weight",
                            0.0,
                            1.0,
                            float(
                                getattr(
                                    m,
                                    f"{k if k != 'comparable' else 'comparable'}_weight",
                                )
                            ),
                            0.01,
                        )
                        for k in DEFAULT_WEIGHTS
                    }
                    save = st.form_submit_button("Update market")
                if save:
                    try:
                        validate_weights(weights)
                        parsed_pack = json.loads(source_pack)
                        m.enabled = enabled
                        m.current_thesis = thesis
                        m.rules = resolution_criteria
                        m.source_pack_json = source_pack
                        m.preferred_domains_json = json.dumps(parsed_pack.get("preferred_domains", []))
                        (
                            m.polling_weight,
                            m.forecast_weight,
                            m.comparable_weight,
                            m.expert_weight,
                            m.news_weight,
                        ) = (
                            weights["polling"],
                            weights["forecast"],
                            weights["comparable"],
                            weights["expert"],
                            weights["news"],
                        )
                        st.success("Market configuration updated.")
                    except Exception as exc:
                        st.error(str(exc))

        with tabs[1]:
            if market_options:
                selected = st.selectbox("Market", list(market_options), key="component_market")
                mid = market_options[selected]
                existing = {
                    x.component_type: x
                    for x in session.scalars(select(FairValueComponent).where(FairValueComponent.market_id == mid))
                }
                with st.form("components_form"):
                    entries = {}
                    for component, _weight in DEFAULT_WEIGHTS.items():
                        row = existing.get(component)
                        st.markdown(f"**{component.title()}**")
                        c1, c2, c3 = st.columns([1, 1, 2])
                        probability = c1.number_input(
                            "Probability",
                            0.0,
                            1.0,
                            float(row.probability if row and row.probability is not None else 0.5),
                            0.01,
                            key=f"p_{component}",
                        )
                        observed = c2.text_input(
                            "Observed UTC",
                            value=row.observed_at.isoformat() if row and row.observed_at else utcnow().isoformat(),
                            key=f"o_{component}",
                        )
                        source_label = c3.text_input(
                            "Source/provenance",
                            value=row.source_label if row else "",
                            key=f"s_{component}",
                        )
                        source_url = st.text_input(
                            "Source URL",
                            value=row.source_url if row else "",
                            key=f"u_{component}",
                        )
                        notes = st.text_input(
                            "Notes",
                            value=row.notes if row else "",
                            key=f"n_{component}",
                        )
                        entries[component] = (
                            probability,
                            observed,
                            source_label,
                            source_url,
                            notes,
                        )
                    save = st.form_submit_button("Save component inputs")
                if save:
                    for component, values in entries.items():
                        row = existing.get(component) or FairValueComponent(
                            market_id=mid,
                            component_type=component,
                            weight=DEFAULT_WEIGHTS[component],
                        )
                        if row.id is None:
                            session.add(row)
                        row.probability = values[0]
                        row.observed_at = datetime.fromisoformat(values[1].replace("Z", "+00:00"))
                        row.source_label = values[2]
                        row.source_url = validate_external_url(values[3]) if values[3] else None
                        row.notes = values[4]
                        row.ingestion_method = "manual_admin"
                    st.success("Components saved. Run ingestion to generate a proposal.")

        with tabs[2]:
            pending = list(
                session.scalars(
                    select(EvidenceItem)
                    .where(EvidenceItem.review_status == "PENDING")
                    .order_by(desc(EvidenceItem.discovered_at))
                    .limit(100)
                )
            )
            st.write(f"{len(pending)} item(s) pending review.")
            for item in pending:
                with st.expander(f"{item.id} · {item.title}"):
                    st.write(item.summary or item.reason)
                    st.caption(
                        f"Relevant={item.relevant} · score={item.relevance_score} · quality={item.source_quality} · category={item.category}"
                    )
                    notes = st.text_input("Review notes", key=f"review_notes_{item.id}")
                    c1, c2, c3 = st.columns(3)
                    if c1.button("Accept", key=f"accept_{item.id}"):
                        item.review_status, item.reviewed_at, item.review_notes = (
                            "ACCEPTED",
                            utcnow(),
                            notes,
                        )
                        session.commit()
                        st.rerun()
                    if c2.button("Reject", key=f"reject_{item.id}"):
                        item.review_status, item.reviewed_at, item.review_notes = (
                            "REJECTED",
                            utcnow(),
                            notes,
                        )
                        item.relevant = False
                        session.commit()
                        st.rerun()
                    if c3.button("Needs follow-up", key=f"follow_{item.id}"):
                        item.review_status, item.reviewed_at, item.review_notes = (
                            "FOLLOW_UP",
                            utcnow(),
                            notes,
                        )
                        session.commit()
                        st.rerun()
            st.subheader("Submit manual evidence")
            if market_options:
                with st.form("manual_evidence"):
                    selected = st.selectbox("Market", list(market_options), key="manual_market")
                    observation_type = st.selectbox("Observation type", ["manual", "external_market"])
                    title = st.text_input("Title")
                    url = st.text_input("URL")
                    content = st.text_area("Observation or excerpt")
                    submit = st.form_submit_button("Classify and save")
                if submit:
                    clean_url = validate_external_url(url) if url else None
                    market = session.get(Market, market_options[selected])
                    item, inserted = insert_and_classify_candidate(
                        session,
                        market,
                        None,
                        EvidenceCandidate(
                            observation_type,
                            "Administrator" if observation_type == "manual" else "Manual external market",
                            title,
                            clean_url,
                            utcnow(),
                            content,
                            {"manual": True},
                        ),
                    )
                    st.success(f"Evidence {'inserted' if inserted else 'already existed'}: item {item.id}")

        with tabs[3]:
            proposals = list(
                session.scalars(
                    select(FairValueProposal)
                    .where(FairValueProposal.status == "PENDING")
                    .order_by(FairValueProposal.created_at)
                )
            )
            if not proposals:
                st.info("No pending proposals.")
            for p in proposals:
                m = session.get(Market, p.market_id)
                with st.expander(
                    f"{m.question if m else p.market_id} · {pct(p.current_published_fair_value)} → {pct(p.proposed_fair_value)}"
                ):
                    st.write(p.rationale)
                    st.json(
                        {
                            "components": safe_json(p.proposed_components_json, {}),
                            "weights": safe_json(p.proposed_weights_json, {}),
                            "effective_weights": safe_json(p.effective_weights_json, {}),
                        }
                    )
                    edit_value = st.number_input(
                        "Approved fair value",
                        0.0,
                        1.0,
                        float(p.proposed_fair_value),
                        0.005,
                        key=f"proposal_value_{p.id}",
                    )
                    thesis = st.text_area(
                        "Published thesis",
                        value=m.current_thesis or p.rationale,
                        key=f"proposal_thesis_{p.id}",
                    )
                    notes = st.text_area(
                        "Approval/rejection justification",
                        value=p.rationale,
                        key=f"proposal_notes_{p.id}",
                    )
                    c1, c2 = st.columns(2)
                    if c1.button("Approve and publish", type="primary", key=f"approve_{p.id}"):
                        publish_proposal(
                            session,
                            p,
                            edit_value,
                            thesis,
                            notes,
                            st.session_state.get("admin_username", "admin"),
                        )
                        session.commit()
                        st.success("Published as immutable revision.")
                        st.rerun()
                    if c2.button("Reject", key=f"reject_p_{p.id}"):
                        reject_proposal(
                            session,
                            p,
                            st.session_state.get("admin_username", "admin"),
                            notes,
                        )
                        session.commit()
                        st.rerun()

        with tabs[4]:
            if market_options:
                selected = st.selectbox("Market", list(market_options), key="source_market")
                mid = market_options[selected]
                with st.form("add_source"):
                    source_type = st.selectbox(
                        "Adapter",
                        [
                            "google_news",
                            "rss",
                            "gdelt",
                            "json_api",
                            "manual",
                            "external_market",
                        ],
                    )
                    name = st.text_input("Name")
                    url = st.text_input("URL (RSS/JSON only)")
                    query = st.text_input("Query")
                    allowed_domains = st.text_input(
                        "Allowed domains (comma-separated)",
                        help="Recommended for RSS/JSON adapters; redirects are revalidated.",
                    )
                    config = st.text_area("Config JSON", value='{"max_items": 10}')
                    add = st.form_submit_button("Add source")
                if add:
                    if url:
                        validate_external_url(url)
                    json.loads(config)
                    session.add(
                        MarketSource(
                            market_id=mid,
                            source_type=source_type,
                            name=name,
                            url=url or None,
                            query=query or None,
                            config_json=config,
                            allowed_domains_json=json.dumps(
                                [domain.strip().lower() for domain in allowed_domains.split(",") if domain.strip()]
                            ),
                        )
                    )
                    st.success("Source added.")
                sources = list(session.scalars(select(MarketSource).where(MarketSource.market_id == mid)))
                if sources:
                    source_labels = {f"{source.id} · {source.name}": source for source in sources}
                    selected_source_label = st.selectbox(
                        "Edit existing source", list(source_labels), key="source_edit_select"
                    )
                    selected_source = source_labels[selected_source_label]
                    source_enabled = st.checkbox(
                        "Source enabled",
                        value=selected_source.enabled,
                        key="source_enabled",
                    )
                    if st.button("Update source state"):
                        selected_source.enabled = source_enabled
                        session.commit()
                        st.success("Source state updated.")
                        st.rerun()
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "ID": s.id,
                                "Type": s.source_type,
                                "Name": s.name,
                                "URL": s.url,
                                "Query": s.query,
                                "Enabled": s.enabled,
                            }
                            for s in sources
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

        with tabs[5]:
            st.write(
                "The pipeline is idempotent: evidence hashes and one canonical UTC daily snapshot prevent backup-run duplicates."
            )
            c1, c2 = st.columns(2)
            if c1.button("Run daily pipeline now", type="primary"):
                with st.spinner("Running ingestion, evidence classification and snapshots..."):
                    st.json(run_daily_pipeline("admin_manual", force=True))
            if c2.button("Retry today's failed/partial run"):
                with st.spinner("Retrying..."):
                    st.json(run_daily_pipeline("admin_retry", force=True))

        with tabs[6]:
            if market_options:
                with st.form("resolve_market"):
                    selected = st.selectbox("Market", list(market_options), key="resolve_market_select")
                    outcome = st.selectbox("Resolved outcome", ["YES", "NO"])
                    source_url = st.text_input("Official/source URL")
                    notes = st.text_area("Resolution notes")
                    resolve = st.form_submit_button("Record final resolution")
                if resolve:
                    if source_url:
                        source_url = validate_external_url(source_url)
                    resolve_market(
                        session,
                        session.get(Market, market_options[selected]),
                        1.0 if outcome == "YES" else 0.0,
                        source_url or None,
                        notes,
                        st.session_state.get("admin_username", "admin"),
                    )
                    st.success("Resolution recorded and Brier scores calculated.")

        with tabs[7]:
            failed_jobs = list(
                session.scalars(
                    select(JobRun)
                    .where(JobRun.status.in_(["FAILED", "PARTIAL"]))
                    .order_by(desc(JobRun.started_at))
                    .limit(100)
                )
            )
            failed_sources = list(
                session.scalars(
                    select(SourceRun)
                    .where(SourceRun.status == "FAILED")
                    .order_by(desc(SourceRun.started_at))
                    .limit(200)
                )
            )
            st.subheader("Job failures")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Run": j.run_key,
                            "Started": j.started_at,
                            "Status": j.status,
                            "Errors": j.error_count,
                            "Summary": j.sanitized_error,
                        }
                        for j in failed_jobs
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
            st.subheader("Source failures")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Source": s.source_name,
                            "Market": s.market_id,
                            "Started": s.started_at,
                            "Error": s.sanitized_error,
                        }
                        for s in failed_sources
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

        with tabs[8]:
            st.write(
                "A canonical forecast (OK or ABSTAINED, from a run classified `canonical`) publishes "
                "automatically once persisted — there is no approval action here, and none is needed. "
                "The only review action available is flagging a genuine data-integrity concern, which "
                "removes a forecast from public display without editing or fabricating anything. The "
                "forecast itself — fair value, confidence, rationale, evidence — is the immutable "
                "output of the blind model call and is never editable from this screen."
            )
            review_filter = st.selectbox(
                "Show",
                ["UNREVIEWED", "FLAGGED", "All"],
                key="llm_review_filter",
            )
            review_query = (
                select(LLMForecast)
                .where(LLMForecast.status.in_(["OK", "ABSTAINED"]))
                .order_by(desc(LLMForecast.generated_at))
            )
            if review_filter != "All":
                review_query = review_query.where(LLMForecast.reviewed_status == review_filter)
            candidates = list(session.scalars(review_query.limit(100)))
            if not candidates:
                st.info("No matching forecasts.")
            for forecast in candidates:
                m = session.get(Market, forecast.market_id)
                label = f"{m.question if m else forecast.market_id} · {forecast.run_slot} · {pct(forecast.fair_value)} · {forecast.reviewed_status}"
                with st.expander(label):
                    st.write(forecast.rationale or "No rationale recorded.")
                    st.caption(
                        f"Confidence {pct(forecast.confidence)} · model {forecast.model_name} · "
                        f"prompt {forecast.prompt_version} · generated {forecast.generated_at}"
                    )
                    notes = st.text_area(
                        "Review notes",
                        value=forecast.review_notes or "",
                        key=f"llm_review_notes_{forecast.id}",
                    )
                    c1, c2 = st.columns(2)
                    if c1.button("Flag data quality", key=f"llm_flag_{forecast.id}"):
                        set_llm_forecast_review_status(
                            session,
                            forecast,
                            "FLAGGED",
                            st.session_state.get("admin_username", "admin"),
                            notes,
                        )
                        session.commit()
                        st.rerun()
                    if c2.button("Reset to unreviewed", key=f"llm_reset_{forecast.id}"):
                        set_llm_forecast_review_status(
                            session,
                            forecast,
                            "UNREVIEWED",
                            st.session_state.get("admin_username", "admin"),
                            notes,
                        )
                        session.commit()
                        st.rerun()
