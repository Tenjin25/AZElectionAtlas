# Arizona Election Atlas

Interactive Arizona election atlas for exploring presidential, statewide, county, precinct, congressional, and legislative-district results.

The application is a static site built around `index.html`. It can be hosted on GitHub Pages or served locally without a backend.

## Features

- Map views for counties, precincts, congressional districts, State House districts, and State Senate districts.
- Historical election results from 2000 through 2024 where source data is available.
- County-level totals for contests that belong on the county layer.
- District projections onto the current 2022 district lines, including historical presidential results.
- Trend, margin, shift, winner, flip, turnout, and demographic views.

## Repository layout

- `index.html` — application shell and map interface.
- `js/` — reusable browser modules.
- `Data/geometry/` — Arizona boundary GeoJSON files.
- `Data/contests/` — precinct contest slices and manifests.
- `Data/county_contests/` — county contest slices and manifests.
- `Data/district_contests/` — congressional and legislative district outputs.
- `Data/crosswalks/` — geography and election-precinct crosswalks.
- `Data/official-az-precinct-results/` — official historical precinct source files.
- `scripts/` — data aggregation, repair, audit, and generation scripts.
- `core-tests/` — fast JavaScript module tests.
- `tests/` — Playwright browser tests.

## Run locally

Install the development dependencies and run the tests:

```powershell
npm install
npm run test:core
npm test
```

The Playwright configuration starts the static site at `http://127.0.0.1:4173`.

For a simple local preview, use any static-file server from the repository root. Opening `index.html` directly may prevent some browser requests from loading because of local file security rules.

## Data pipeline

The generated files are loaded through the manifests in `Data/contests/`, `Data/county_contests/`, and `Data/district_contests/`. After changing source data, regenerate the relevant outputs with the scripts in `scripts/`, then run:

```powershell
python scripts/audit_arizona_outputs.py
```

Historical precinct coverage is not uniform. County totals are preferred for statewide and county summaries when available. District layers use their corresponding crosswalk or historical geometry assignments; historical results projected onto 2022 lines should be interpreted as line-projection estimates, not historical election-administration boundaries.

## Mapbox

The map uses Mapbox GL JS. Keep the public access token in the local/runtime configuration and do not commit secret tokens, personal credentials, or private API keys.

## License

This project is currently marked as ISC in `package.json`. Source election data and geographic data may have separate terms or attribution requirements.

## CVAP data attribution

Citizen Voting Age Population (CVAP) totals use the U.S. Census Bureau's 2020-2024 American Community Survey five-year CVAP Special Tabulation. Precinct and legacy-boundary aggregates use the Redistricting Data Hub's **2024 CVAP Data Disaggregated to 2020 Census Blocks**.

- Census source: https://www.census.gov/programs-surveys/decennial-census/about/voting-rights/cvap/2020-2024-CVAP.html
- Block-level source and processing: https://redistrictingdatahub.org/

Credit: **U.S. Census Bureau; Redistricting Data Hub.**

## Legend layout (September 2026)

The map key now uses the same expandable, scrollable category-row layout as Margin Categories for Winners, Flips, Shift, and Demographics. Each row pairs a named category with its map color and a short range or interpretation. Population Change uses the same layout where that mode is available.

Shift retains its 15-step diverging spectrum and separates Democratic and Republican movement at 0.5, 1, 5, 10, 15, 20, and 25 percentage points. Movement below 0.5 points is near-white; the 25-point-and-higher category is named **Extreme**. The blue/orange colorblind palette follows the same directional bins.
