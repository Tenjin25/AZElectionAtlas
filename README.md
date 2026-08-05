# AZPrecinctMap

AZPrecinctMap is the Arizona edition of the Election Atlas workspace. It provides an interactive map for exploring Arizona election results at the precinct, county, congressional-district, and legislative-district levels.

## Workspace layout

- `index.html` — application shell and map UI
- `js/` — reusable atlas modules
- `data/geometry/` — generated Arizona county, precinct, and district GeoJSON
- `data/contests/` — county/precinct contest slices and manifests
- `data/district_contests/` — district contest outputs and manifests
- `data/crosswalks/` — generated geography and election crosswalks
- `data/` root — generated manifests and statewide aggregates
- `data/az-geometry/` — source Arizona boundary files
- `data/openelections-data-az/` — source election results
- `scripts/` — data-generation scripts
- `core-tests/` — fast module tests
- `tests/` — browser regression tests
- `tools/` — local development utilities

## Local development

```powershell
npm install
npm run test:core
npm test
```

The Playwright configuration starts the local static server automatically at `http://127.0.0.1:4173`.
