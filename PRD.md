# PRD: Reality Spread / Partisan Premium Index Bot

## 1. Product Summary

Build a local-first prediction-market research system that scans Polymarket political markets, compares market prices against external baselines, classifies narrative/partisan/emotional premium using a local LLM, computes a Partisan Premium Index score, logs paper trades, and surfaces the best opportunities through a dashboard and alerts.

The product is not a real-money auto-trading bot in MVP. It is a research, alerting, and paper-forward-testing engine.

Working name: **Reality Spread**

Core thesis:

Prediction markets are powerful, but they can contain pockets of narrative-driven mispricing. This system detects when political outcomes appear overpriced relative to external baselines, especially when emotional demand, identity alignment, deadlines, legal ambiguity, and institutional friction diverge from real-world probability.

## 2. Primary Goal

Build an end-to-end MVP that can:

1. Pull active Polymarket markets.
2. Filter for politics, elections, law, policy, culture-war, and deadline markets.
3. Pull order-book data for executable prices.
4. Match markets to manually maintained fair-value inputs.
5. Optionally compare against Kalshi/PredictIt when equivalent markets exist.
6. Use a local Ollama LLM to classify market type, ideological coding, emotional side, institutional friction, deadline relevance, and resolution risk.
7. Compute a blended fair value and PPI score.
8. Generate alerts for high-scoring watchlist markets.
9. Simulate paper trades using realistic bid/ask assumptions.
10. Show everything in a local Streamlit dashboard.
11. Store all signals, snapshots, and paper trades for a future post-midterms public reveal.

## 3. Non-Goals for MVP

Do not build real-money auto-execution in MVP.

Do not place trades.

Do not require private wallet keys.

Do not make financial advice claims.

Do not use the LLM to invent final probabilities.

Do not rely on midpoint-only backtests.

Do not make the product partisan in the UI. Internally, the model can classify right-coded, left-coded, populist-coded, establishment-coded, crypto-coded, and anti-institutional outcomes.

## 4. User

Primary user:

A solo builder/researcher who wants to create a serious prediction-market intelligence system and forward-test a Partisan Premium Index through future political events.

Secondary future users:

Prediction-market traders, politics/data nerds, newsletter readers, X/Twitter followers, and people interested in whether political markets overprice narratives.

## 5. Core Concept

The app computes:

```text
Fair YES Probability =
  35% polling probability
+ 25% forecast/fundamentals probability
+ 20% other-market probability
+ 10% expert/race-rating probability
+ 10% news/campaign probability
```

Then:

```text
Premium = Polymarket YES executable price - Fair YES Probability
```

Then:

```text
PPI Score =
  probability premium score
+ identity/narrative intensity
+ institutional friction
+ deadline decay relevance
+ liquidity quality
+ resolution clarity
+ cross-market divergence
- risk penalties
```

The bot should identify cases like:

```text
Polymarket YES: 61%
Fair YES: 49%
Premium: +12 points
PPI Score: 82/100
Suggested action: Alert / paper-buy NO
```

## 6. Product Modules

Build these modules.

### 6.1 Market Ingestion Module

Fetch active Polymarket markets.

Use:

```text
https://gamma-api.polymarket.com/events?active=true&closed=false&limit=100
```

Paginate until all active events are fetched.

For each event, extract all associated markets.

For each market, store:

```text
platform
event_id
market_id
condition_id
question
slug
description
resolution_source
rules
outcomes
outcome_prices
clob_token_ids
volume
liquidity
active
closed
end_date
category
tags
enable_order_book
created_at
updated_at
```

Only keep markets where:

```text
active = true
closed = false
enable_order_book = true if available
question or tags suggest politics/elections/law/policy/culture/geopolitics
```

MVP political keyword filter:

```text
election
president
senate
house
congress
governor
mayor
trump
biden
harris
republican
democrat
gop
dem
government
court
supreme court
law
bill
policy
tariff
immigration
deportation
pardon
indictment
conviction
resign
appointed
cabinet
fed
sec
cftc
crypto regulation
ukraine
russia
israel
gaza
china
canada election
france election
brazil election
```

Store raw API responses in a `raw_api_responses` table for debugging.

### 6.2 Polymarket Order Book Module

For each relevant market, use CLOB token IDs to fetch order-book data.

Fetch for YES and NO token IDs when available.

Store:

```text
token_id
best_bid
best_ask
midpoint
spread
bid_depth_1c
ask_depth_1c
bid_depth_3c
ask_depth_3c
bid_depth_5c
ask_depth_5c
last_trade_price
snapshot_timestamp
```

The app must use executable price, not only displayed outcome price.

For YES-side pricing:

```text
yes_executable_buy_price = best_ask_yes
yes_executable_sell_price = best_bid_yes
```

For NO-side paper buying:

```text
no_executable_buy_price = best_ask_no
```

If direct NO book is unavailable but binary complement exists:

```text
no_buy_price ≈ 1 - best_bid_yes
no_sell_price ≈ 1 - best_ask_yes
```

Reject or mark low-confidence if order-book data is missing.

### 6.3 Other Markets Module

Build a generic cross-market comparison layer.

MVP platforms:

1. Kalshi public market data.
2. PredictIt public market endpoint.
3. Manual CSV override.

Use Kalshi base:

```text
https://external-api.kalshi.com/trade-api/v2
```

Fetch Kalshi markets and order books when available.

For MVP, allow manual mapping in:

```text
data/market_mappings.csv
```

CSV columns:

```csv
polymarket_market_id,platform,external_market_id,external_market_url,match_confidence,notes
```

For each matched external market, store:

```text
platform
external_market_id
external_question
yes_price
no_price
best_bid
best_ask
midpoint
spread
volume
open_interest
last_updated
contract_difference_notes
match_confidence
```

The local LLM should review whether the Polymarket/Kalshi/PredictIt market is actually equivalent.

### 6.4 Manual Fair Value Module

Build a local editable CSV that lets the user manually input fair-value data for each market.

File:

```text
data/fair_values.csv
```

Columns:

```csv
market_id,question,polymarket_slug,polling_prob,forecast_prob,other_markets_prob,expert_prob,news_campaign_prob,manual_fair_yes,confidence,source_notes,last_updated
```

Rules:

If `manual_fair_yes` exists, use it as the fair value.

Otherwise compute:

```text
fair_yes =
  0.35 * polling_prob
+ 0.25 * forecast_prob
+ 0.20 * other_markets_prob
+ 0.10 * expert_prob
+ 0.10 * news_campaign_prob
```

If one component is missing, renormalize weights across available components, but penalize confidence.

Example:

```text
available components: polling, other markets, expert
new weights:
polling = 35 / 65
other markets = 20 / 65
expert = 10 / 65
```

Confidence should be reduced when fewer inputs exist.

### 6.5 Optional Polling/Forecast/FEC/News Ingestion

Build stubs/interfaces for these, but do not block MVP if unavailable.

Create fetchers for:

```text
VoteHub polling API if configured
FiveThirtyEight CSV/GitHub polling data if configured
FEC API if FEC_API_KEY exists
GDELT news search if enabled
Cook/Inside Elections manual rating input
```

If no API keys are present, the app should still run using manual CSV inputs.

### 6.6 Local LLM Module

Use Ollama locally.

Default model:

```text
qwen3:8b
```

Configurable via environment variable:

```text
OLLAMA_MODEL
```

Default Ollama URL:

```text
http://localhost:11434
```

The LLM must return structured JSON using Pydantic schemas.

Do not allow freeform LLM output to directly drive scores without validation.

Create five LLM functions:

#### A. Market Classifier

Input:

```json
{
  "question": "...",
  "description": "...",
  "rules": "...",
  "outcomes": ["Yes", "No"],
  "tags": ["..."],
  "end_date": "..."
}
```

Output schema:

```json
{
  "market_category": "election | policy_deadline | legal_process | culture_war | appointment | resignation | geopolitics | crypto_policy | other",
  "emotional_side": "YES | NO | unclear",
  "ideological_coding": "right_populist | right_establishment | left_populist | left_establishment | centrist_establishment | crypto_bullish | anti_institutional | unclear | none",
  "identity_intensity": 0,
  "institutional_friction": 0,
  "deadline_decay_relevance": 0,
  "classification_confidence": 0.0,
  "summary": "...",
  "warnings": ["..."]
}
```

#### B. Resolution Risk Parser

Input:

```json
{
  "question": "...",
  "rules": "...",
  "resolution_source": "...",
  "end_date": "..."
}
```

Output schema:

```json
{
  "resolution_risk": 0,
  "ambiguous_terms": ["..."],
  "source_dependency": "official | media_call | oracle_discretion | unclear | none",
  "implementation_vs_announcement_risk": 0,
  "date_boundary_risk": 0,
  "summary": "...",
  "warnings": ["..."]
}
```

#### C. Cross-Market Matcher

Input:

```json
{
  "polymarket_question": "...",
  "external_question": "...",
  "polymarket_rules": "...",
  "external_rules": "..."
}
```

Output schema:

```json
{
  "same_underlying_event": true,
  "matching_confidence": 0.0,
  "material_differences": ["..."],
  "safe_to_compare_prices": true,
  "summary": "..."
}
```

#### D. News/Campaign Materiality Classifier

Input:

```json
{
  "market_question": "...",
  "recent_headlines": ["..."],
  "campaign_notes": ["..."]
}
```

Output schema:

```json
{
  "materiality": "none | low | medium | high",
  "direction": "helps_yes | helps_no | mixed | unclear",
  "probability_adjustment_suggestion": 0.0,
  "confidence": 0.0,
  "summary": "...",
  "warnings": ["..."]
}
```

Cap news/campaign adjustment suggestions at plus/minus 5 percentage points.

#### E. Public Explanation Writer

Input:

```json
{
  "market_question": "...",
  "polymarket_yes": 0.61,
  "fair_yes": 0.49,
  "premium": 0.12,
  "ppi_score": 82,
  "classification": {},
  "resolution": {},
  "liquidity": {}
}
```

Output:

```json
{
  "short_alert": "...",
  "public_explanation": "...",
  "private_notes": ["..."],
  "risk_warnings": ["..."]
}
```

### 6.7 PPI Scoring Module

Compute scores deterministically.

#### Premium Score

```text
premium = polymarket_yes_executable_price - fair_yes
```

If side is emotionally attractive YES and premium is positive, it is a candidate for buying/paper-buying NO.

If emotional side is NO, reverse logic appropriately.

Premium score:

```text
premium < 0.05 = 0
0.05 to 0.08 = 20
0.08 to 0.12 = 45
0.12 to 0.20 = 70
0.20+ = 85, but add hidden-info warning
```

#### Identity/Narrative Score

From LLM `identity_intensity`:

```text
0 = 0
1 = 3
2 = 6
3 = 9
4 = 12
5 = 15
```

#### Institutional Friction Score

From LLM `institutional_friction`:

```text
0 = 0
1 = 2
2 = 5
3 = 8
4 = 11
5 = 15
```

#### Deadline Decay Score

Inputs:

```text
days_to_end
deadline_decay_relevance
```

If relevance is high and days to end are low, score higher.

```text
if deadline_decay_relevance >= 4 and days_to_end <= 14: +10
if deadline_decay_relevance >= 3 and days_to_end <= 30: +7
if deadline_decay_relevance >= 2 and days_to_end <= 60: +4
else: +0
```

#### Liquidity Quality Score

Use order-book data.

```text
spread <= 0.02 = +10
spread <= 0.04 = +7
spread <= 0.06 = +3
spread > 0.06 = -10
```

Depth:

```text
depth_3c >= $1000 = +5
depth_3c >= $250 = +3
depth_3c < $100 = -5
```

#### Resolution Risk Penalty

```text
resolution_risk 0 = 0
resolution_risk 1 = -1
resolution_risk 2 = -3
resolution_risk 3 = -8
resolution_risk 4 = -15
resolution_risk 5 = reject trade / alert only
```

#### Confidence Penalty

If fair-value confidence is low:

```text
confidence >= 0.80 = no penalty
0.60 to 0.79 = -5
0.40 to 0.59 = -12
< 0.40 = reject trade / research queue only
```

#### Final PPI Score

```text
PPI Score =
premium_score
+ identity_score
+ institutional_friction_score
+ deadline_decay_score
+ liquidity_score
+ depth_score
+ resolution_penalty
+ confidence_penalty
```

Clamp to 0–100.

Actions:

```text
0–49 = ignore
50–64 = watchlist
65–79 = alert
80–100 = high-conviction paper signal
```

Real-money execution is disabled.

### 6.8 Paper Trading Module

When a signal crosses threshold, create a simulated paper trade.

Default rule:

```text
If PPI Score >= 75
and premium >= 0.10
and spread <= 0.04
and resolution_risk <= 3
and fair_value_confidence >= 0.60
then create paper trade.
```

For emotionally attractive YES overpriced:

```text
paper side = BUY_NO
entry_price = current NO executable buy price
```

For emotionally attractive NO overpriced:

```text
paper side = BUY_YES
entry_price = current YES executable buy price
```

Default simulated size:

```text
$100 notional
```

But cap by depth:

```text
simulated_size = min(100, depth_within_3c * 0.25)
```

If simulated size is less than $10, mark as non-executable and do not create official paper trade.

Track:

```text
entry_timestamp
entry_price
side
size
reason
ppi_score
fair_yes
market_price
premium
max_favorable_excursion
max_adverse_excursion
current_mark_to_market
exit_timestamp
exit_price
pnl
status
resolution_result
notes
```

Exit rules:

```text
Exit 50% when premium compresses by 50%.
Exit 100% when premium disappears.
Exit 100% at resolution.
Exit if resolution risk increases to 5.
Exit if fair-value model changes against trade by 10+ points.
```

For MVP, implement mark-to-market and paper PnL. Manual exit is allowed through dashboard.

### 6.9 Alerting Module

Support Discord webhook first.

Optional Telegram later.

Environment variable:

```text
DISCORD_WEBHOOK_URL
```

Alert format:

```text
🚨 Reality Spread Alert

Market: {question}
Action: {watchlist / alert / paper signal}
PPI Score: {score}/100

Polymarket YES: {yes_price}
Fair YES: {fair_yes}
Premium: {premium_points} pts

Emotional side: {YES/NO}
Coding: {right_populist / left_populist / etc.}
Category: {category}
Spread: {spread}
Depth within 3c: {depth}
Resolution risk: {resolution_risk}/5

Suggested paper side: {BUY_NO / BUY_YES / NONE}

Why:
{short explanation}

Warnings:
- {warning 1}
- {warning 2}
```

Do not send more than one alert per market per 6 hours unless score changes by 10+ points.

### 6.10 Dashboard Module

Build with Streamlit.

Pages:

#### A. Overview

Show:

```text
total active markets scanned
political markets found
markets with fair values
alerts today
paper trades open
paper PnL
highest PPI score
highest premium
highest resolution risk
```

#### B. Market Radar

Table columns:

```text
question
category
ideological coding
emotional side
Polymarket YES
Fair YES
premium
PPI score
spread
liquidity/depth
resolution risk
action
last updated
```

Filters:

```text
category
coding
score range
premium range
resolution risk
alert status
has fair value
has paper trade
```

#### C. Market Detail Page

For a selected market show:

```text
question
description/rules
price chart
order-book summary
fair-value inputs
LLM classification
resolution-risk parse
PPI breakdown
alerts history
paper trades
public explanation
raw API links/IDs
```

#### D. Fair Value Editor

Allow user to edit/create rows in `fair_values.csv` through UI.

Fields:

```text
polling_prob
forecast_prob
other_markets_prob
expert_prob
news_campaign_prob
manual_fair_yes
confidence
source_notes
last_updated
```

Validate all probabilities between 0 and 1.

#### E. Paper Trading Dashboard

Show:

```text
open paper trades
closed paper trades
paper PnL
win rate
avg return
max adverse excursion
max favorable excursion
performance by category
performance by ideological coding
performance by resolution risk
```

#### F. Research Queue

Show markets that are relevant but missing fair values.

Allow user to mark:

```text
ignore
needs fair value
needs external-market mapping
bad resolution rules
not political
```

### 6.11 Public Report Export

Generate markdown reports.

Reports:

```text
daily_report.md
weekly_report.md
signal_autopsy.md
```

Daily report sections:

```text
Reality Spread Daily
Date
Top 10 PPI markets
Biggest premiums
Deadline decay alerts
Highest resolution risk markets
New paper trades
Open paper trade PnL
Markets needing research
```

Weekly report sections:

```text
Week summary
Best signals
Worst signals
Category performance
False positives
Lessons
Next model changes
```

Signal autopsy after market resolution:

```text
Market
Original signal
Entry price
Exit/resolution price
Paper PnL
Why signal triggered
What happened
What the model got right
What the model got wrong
Version 2 improvement
```

## 7. Data Model

Use SQLite for MVP with SQLAlchemy.

Database file:

```text
data/reality_spread.db
```

Tables:

### markets

```text
id
platform
platform_market_id
event_id
condition_id
slug
question
description
rules
resolution_source
outcomes_json
clob_token_ids_json
category
tags_json
end_date
active
closed
enable_order_book
volume
liquidity
created_at
updated_at
first_seen_at
last_seen_at
raw_json
```

### market_snapshots

```text
id
market_id
timestamp
yes_price_displayed
no_price_displayed
yes_best_bid
yes_best_ask
no_best_bid
no_best_ask
yes_midpoint
no_midpoint
spread
depth_1c
depth_3c
depth_5c
volume
liquidity
raw_orderbook_json
```

### fair_values

```text
id
market_id
polling_prob
forecast_prob
other_markets_prob
expert_prob
news_campaign_prob
manual_fair_yes
computed_fair_yes
confidence
source_notes
last_updated
created_at
updated_at
```

### llm_classifications

```text
id
market_id
timestamp
model
market_category
emotional_side
ideological_coding
identity_intensity
institutional_friction
deadline_decay_relevance
classification_confidence
summary
warnings_json
raw_json
```

### resolution_risks

```text
id
market_id
timestamp
model
resolution_risk
ambiguous_terms_json
source_dependency
implementation_vs_announcement_risk
date_boundary_risk
summary
warnings_json
raw_json
```

### external_market_mappings

```text
id
market_id
platform
external_market_id
external_question
external_url
same_underlying_event
matching_confidence
safe_to_compare_prices
material_differences_json
last_checked_at
raw_json
```

### external_market_snapshots

```text
id
mapping_id
timestamp
yes_price
no_price
best_bid
best_ask
midpoint
spread
volume
open_interest
raw_json
```

### ppi_signals

```text
id
market_id
timestamp
polymarket_yes
fair_yes
premium
ppi_score
action
paper_side
score_breakdown_json
explanation
warnings_json
status
```

### paper_trades

```text
id
signal_id
market_id
side
entry_timestamp
entry_price
size
current_price
current_pnl
max_favorable_excursion
max_adverse_excursion
exit_timestamp
exit_price
realized_pnl
status
resolution_result
notes
```

### alerts

```text
id
signal_id
market_id
timestamp
channel
message
sent_successfully
error_message
```

### raw_api_responses

```text
id
source
endpoint
timestamp
request_params_json
response_json
status_code
error_message
```

## 8. Repo Structure

Build this structure:

```text
reality-spread/
  app/
    __init__.py

    config.py

    db/
      __init__.py
      database.py
      models.py
      crud.py

    ingest/
      __init__.py
      polymarket_gamma.py
      polymarket_clob.py
      kalshi.py
      predictit.py
      fec.py
      gdelt.py
      manual_inputs.py

    llm/
      __init__.py
      ollama_client.py
      schemas.py
      prompts.py
      classifiers.py

    scoring/
      __init__.py
      fair_value.py
      ppi_score.py
      liquidity.py
      paper_trading.py

    alerts/
      __init__.py
      discord.py

    reports/
      __init__.py
      markdown_reports.py

    dashboard/
      streamlit_app.py

    jobs/
      __init__.py
      scan_markets.py
      update_snapshots.py
      score_markets.py
      update_paper_trades.py
      send_reports.py

  data/
    fair_values.csv
    market_mappings.csv
    ignore_markets.csv
    reality_spread.db

  tests/
    test_fair_value.py
    test_ppi_score.py
    test_llm_schema.py
    test_paper_trading.py

  scripts/
    init_db.py
    run_scan_once.py
    run_full_pipeline_once.py
    seed_example_fair_values.py

  .env.example
  requirements.txt
  README.md
```

## 9. Environment Variables

Create `.env.example` with:

```env
# App
APP_ENV=development
DATABASE_URL=sqlite:///data/reality_spread.db

# Polymarket
POLYMARKET_GAMMA_BASE_URL=https://gamma-api.polymarket.com
POLYMARKET_CLOB_BASE_URL=https://clob.polymarket.com

# Kalshi
KALSHI_BASE_URL=https://external-api.kalshi.com/trade-api/v2
KALSHI_API_KEY=
KALSHI_API_SECRET=

# PredictIt
PREDICTIT_MARKETDATA_URL=https://www.predictit.org/api/marketdata/all/

# Optional data APIs
FEC_API_KEY=
GDELT_ENABLED=true
VOTEHUB_API_KEY=
COOK_EMAIL=
COOK_PASSWORD=

# Local LLM
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:8b

# Alerts
DISCORD_WEBHOOK_URL=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Runtime
SCAN_INTERVAL_MINUTES=60
ALERT_COOLDOWN_HOURS=6
MIN_PPI_ALERT_SCORE=65
MIN_PPI_PAPER_SCORE=75
MIN_PREMIUM_FOR_PAPER_TRADE=0.10
MAX_ALLOWED_SPREAD=0.04
MIN_FAIR_VALUE_CONFIDENCE=0.60
```

## 10. Values User Must Change Before Running

The user must edit these before running full functionality:

```text
DISCORD_WEBHOOK_URL
OLLAMA_MODEL if not using qwen3:8b
FEC_API_KEY if using FEC
VOTEHUB_API_KEY if using VoteHub
COOK_EMAIL and COOK_PASSWORD if using Cook
data/fair_values.csv with actual fair-value estimates
data/market_mappings.csv with external market mappings
```

For MVP scanner-only mode, the user can run with no API keys if Ollama is installed and Polymarket public endpoints work.

## 11. Required Commands

The final README must support these commands:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python scripts/init_db.py
python scripts/run_scan_once.py
python scripts/run_full_pipeline_once.py
streamlit run app/dashboard/streamlit_app.py
```

Also support:

```bash
python -m app.jobs.scan_markets
python -m app.jobs.update_snapshots
python -m app.jobs.score_markets
python -m app.jobs.update_paper_trades
```

## 12. Requirements.txt

Use:

```text
requests
httpx
pandas
pydantic
pydantic-settings
python-dotenv
sqlalchemy
streamlit
plotly
ollama
apscheduler
tenacity
pytest
```

Optional:

```text
beautifulsoup4
feedparser
numpy
```

## 13. UX Requirements

The app should feel like a local Bloomberg Terminal for political prediction-market mispricing.

Tone:

```text
analytical
skeptical
clear
not hypey
not financial-advice coded
```

Preferred labels:

```text
Reality Spread
PPI Score
Narrative Premium
Resolution Risk
Deadline Decay
Institutional Friction
Executable Premium
Paper Signal
Research Queue
```

Avoid public-facing labels like:

```text
chud tax
free money
guaranteed edge
profit bot
```

## 14. Acceptance Criteria

MVP is complete when:

1. Running `python scripts/run_full_pipeline_once.py` fetches active Polymarket markets.
2. Relevant political markets are stored in SQLite.
3. Order-book snapshots are stored when token IDs are available.
4. The app can load manual fair values from CSV.
5. The app computes blended fair value.
6. The app computes premium.
7. Ollama classifies at least one market and returns valid JSON.
8. Resolution-risk parser returns valid JSON.
9. PPI score is generated with a score breakdown.
10. High-scoring markets appear in dashboard.
11. Discord alert sends successfully if webhook is configured.
12. Paper trade is created when thresholds are met.
13. Paper PnL updates from latest executable price.
14. Daily markdown report can be exported.
15. Streamlit dashboard shows overview, market radar, market detail, fair-value editor, paper trades, and research queue.

## 15. Test Cases

Create tests for:

### Fair Value

```text
If all inputs exist, weighted fair value is correct.
If manual_fair_yes exists, it overrides computed value.
If some inputs are missing, weights are renormalized.
If confidence is low, scoring applies penalty.
```

### PPI Score

```text
High premium + high identity + low spread + low resolution risk = high score.
High premium + resolution risk 5 = no paper trade.
Spread over max allowed = penalty.
Missing fair value = research queue.
```

### LLM Schema

```text
Market classifier output validates against Pydantic schema.
Resolution parser output validates against Pydantic schema.
Invalid JSON is retried once.
If still invalid, mark classification_failed.
```

### Paper Trading

```text
Paper trade uses executable price, not midpoint.
Paper trade does not create if simulated size below $10.
Paper trade exits when premium disappears.
Paper trade tracks max favorable/adverse excursion.
```

### Alerts

```text
No duplicate alert within cooldown window.
New alert is allowed if score changes by 10+ points.
Failed webhook does not crash pipeline.
```

## 16. Build Order

Implement in this order:

### Phase 1: Project Skeleton

1. Create repo structure.
2. Add config system.
3. Add database models.
4. Add init DB script.
5. Add `.env.example`.
6. Add README.

### Phase 2: Polymarket Scanner

1. Build Gamma API fetcher.
2. Store raw responses.
3. Extract events and markets.
4. Filter political markets.
5. Save markets.

### Phase 3: Order Book Snapshots

1. Build CLOB fetcher.
2. Fetch books for token IDs.
3. Compute best bid, best ask, midpoint, spread, and depth.
4. Store snapshots.

### Phase 4: Fair Value Engine

1. Add fair-values CSV loader.
2. Add fair-value computation.
3. Add confidence logic.
4. Add market detail fair-value display.

### Phase 5: Local LLM

1. Add Ollama client.
2. Add Pydantic schemas.
3. Add market classifier.
4. Add resolution-risk parser.
5. Store LLM outputs.
6. Add retries and fallback behavior.

### Phase 6: PPI Score

1. Implement premium logic.
2. Implement score components.
3. Implement final action labels.
4. Store signals.

### Phase 7: Paper Trading

1. Create paper trade from signal.
2. Update mark-to-market.
3. Add exit rules.
4. Track PnL.

### Phase 8: Dashboard

1. Overview page.
2. Market radar page.
3. Market detail page.
4. Fair value editor.
5. Paper trades page.
6. Research queue.

### Phase 9: Alerts and Reports

1. Discord alert integration.
2. Alert cooldown.
3. Daily markdown report.
4. Weekly markdown report.
5. Signal autopsy export.

## 17. Important Implementation Rules

Use defensive coding.

All API calls must:

```text
timeout
retry with backoff
log failures
not crash whole pipeline
store raw error
```

All probabilities must be floats between 0 and 1.

All scores must be clamped between 0 and 100.

All LLM outputs must be schema-validated.

Never use LLM output directly as probability.

Never create paper trade without fair value.

Never create paper trade if resolution risk is 5.

Never create paper trade if spread is greater than max allowed.

Never use midpoint as official paper entry.

Always store timestamped snapshots so the system can be audited later.

## 18. Example Fair Values CSV

Create this sample file:

```csv
market_id,question,polymarket_slug,polling_prob,forecast_prob,other_markets_prob,expert_prob,news_campaign_prob,manual_fair_yes,confidence,source_notes,last_updated
example-1,Will Republicans win the Senate in 2026?,republicans-senate-2026,0.51,0.54,0.55,0.52,0.53,,0.70,Example placeholder values. Replace before using.,2026-06-02
example-2,Will Democrats win the House in 2026?,democrats-house-2026,0.55,0.56,0.54,0.57,0.55,,0.72,Example placeholder values. Replace before using.,2026-06-02
```

## 19. Example Market Mappings CSV

Create this sample file:

```csv
polymarket_market_id,platform,external_market_id,external_market_url,match_confidence,notes
example-1,kalshi,KXEXAMPLE-26SENATE,,0.80,Placeholder only. Replace with actual mapping.
```

## 20. Output Quality

The final product should not be a toy script. It should be a usable local application.

Minimum quality bar:

```text
Clean repo
Clear README
Re-runnable pipeline
Database persistence
Working dashboard
Working local LLM classification
Working paper trading
Working alerts
Useful error messages
No hardcoded secrets
No real-money trading
```

## 21. README Requirements

README must include:

1. What Reality Spread is.
2. What it does and does not do.
3. Setup instructions.
4. Ollama setup.
5. Required environment variables.
6. How to run one scan.
7. How to run dashboard.
8. How to edit fair values.
9. How paper trading works.
10. How PPI score works.
11. Known limitations.
12. Safety/compliance disclaimer.

## 22. Disclaimer Text for README

Include:

```text
Reality Spread is a research and paper-trading tool for analyzing prediction-market pricing. It does not provide financial advice. It does not guarantee profit. The MVP does not execute real-money trades. All signals are experimental and should be evaluated with caution. Prediction markets involve risk, liquidity constraints, resolution ambiguity, and potential regulatory considerations.
```

## 23. Final Instruction to Builder

Build the complete MVP according to this PRD.

Prioritize a working local end-to-end pipeline over perfect data coverage.

If an external API is unavailable, use manual CSV fallback.

If the LLM fails, the scoring engine should still run with classification marked as unavailable.

If fair value is missing, put the market in research queue rather than guessing.

The final app should let the user scan Polymarket, classify markets locally, compute PPI, create paper trades, view the dashboard, and export reports.
