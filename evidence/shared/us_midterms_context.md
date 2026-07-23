# US Midterms Context

As of: 2026-06-26

## Scope

This file is shared evidence for U.S. 2026 midterm contracts in the Reality Spread experiment: Senate control, House control, seat-count ranges, Senate races, governor races, House districts, and narrative/candidate proxy races.

The LLM must estimate blind fair value for a specific option-level contract. It must not infer or use market prices. This context should be treated as background evidence, not as a deterministic forecast.

## Election calendar and institutional setup

- General election date for the 2026 U.S. midterms: November 3, 2026.
- Senate: 33 regular seats are up, with 13 Democratic-held and 20 Republican-held seats; two Republican-held seats are also up in special elections. Republicans enter the cycle with a 53-47 Senate majority.
- House: all 435 House seats are up. A party needs 218 seats for a standalone House majority.
- Governors: 36 governorships are up in 2026. Nationally, Republicans hold 26 governorships and Democrats hold 24; among the 36 seats up, Democrats and Republicans each hold 18.

## National environment

- The generic congressional ballot as of early/mid 2026 has generally favored Democrats by a mid-single-digit margin. One aggregation snapshot listed an average Democratic advantage of about +5.8 as of May 8, 2026, with several aggregators in the D+5 to D+6 range.
- The president's party generally faces midterm headwinds, but the size of the backlash depends on presidential approval, economic conditions, candidate quality, district/state exposure, and whether salient events such as war, inflation, or major scandals dominate the news environment.
- 2026 Democratic House strategy appears to be anchored less in deep-blue ideological insurgents and more in conventional swing-district candidates with military, local-office, or establishment profiles. That matters for House control and marginal district estimates.

## Forecasting anchor heuristics

### Senate control

Republicans start with a 53-47 edge. Democrats need a net gain of four seats for outright 51+ control, or three seats plus a favorable tie-breaking arrangement if applicable. Because Senate maps are state-specific and uneven, national generic ballot strength does not translate one-for-one into Senate seat gains.

Fair-value anchors:
- Republican Senate control should start favored unless multiple GOP-held battlegrounds look clearly vulnerable.
- Democratic Senate control requires a broad enough national environment plus actual state-level conversion in races such as Maine, North Carolina, Texas, Alaska/Nebraska/Iowa if competitive, and defense of Democratic-held seats.
- Close state polls months out should not be overinterpreted; Senate polling errors and candidate quality effects can be large.

### House control

The House is more sensitive than the Senate to national environment. A persistent Democratic generic ballot lead around +5 to +6 would normally imply Democrats are competitive or favored for House control, but districting, incumbency, candidate quality, and geographic concentration can blunt the translation into seats.

Fair-value anchors:
- House control is more elastic than Senate control.
- Seat-range contracts should not simply mirror control odds. Range tails are sensitive to wave size.
- A mid-single-digit Democratic generic ballot supports Democratic control, but not necessarily a blowout if structural districting and turnout patterns are unfavorable.

### Governors

Governor races are more state-specific and candidate-quality sensitive than federal control markets. National mood matters but does not dominate. Open seats and unpopular incumbents create high variance.

### House districts

District-level contracts should weight:
- district partisanship and presidential baseline;
- incumbent status;
- candidate quality and fundraising;
- district-specific local issues;
- national generic ballot.

## Polling and uncertainty discipline

- Treat polls as noisy, especially in primaries, low-salience races, and races far from Election Day.
- Avoid false precision: 0.55 vs 0.60 is often not distinguishable unless there is strong data.
- For obscure candidates, tail outcomes should often be low but nonzero.
- For range markets, the full distribution across brackets should be mentally coherent even if the model estimates rows independently.

## Source notes

- Ballotpedia tracks 2026 Senate elections, including 33 regular seats, 13 Democratic-held and 20 Republican-held seats, plus two Republican-held special elections.
- Ballotpedia identifies Senate battlegrounds and notes Republicans' 53-47 starting majority.
- 270ToWin mirrors current Cook Political Report Senate, House, and governor ratings snapshots.
- Ballotpedia states 36 governor seats are up in 2026, split 18 Democratic-held and 18 Republican-held among those seats.
- Generic ballot snapshots in May 2026 show a Democratic national House ballot advantage around the mid-single digits.

## Update checklist

Refresh this file when:
- a major candidate wins/loses a primary;
- Cook/Sabato/Inside Elections changes a rating;
- generic ballot average moves by >2 points;
- presidential approval moves materially;
- a major national event changes the political environment;
- a court/redistricting change affects House maps.
