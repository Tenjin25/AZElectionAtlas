(function initializeAtlasRegions(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AtlasRegions = Object.freeze(api);
}(typeof globalThis !== 'undefined' ? globalThis : this, function createAtlasRegions() {
  function normalizeCountyName(name) {
    return (name || '')
      .toString()
      .replace(/\s+COUNTY$/i, '')
      .replace(/[^a-z0-9 .\-]/gi, '')
      .replace(/\s+/g, ' ')
      .trim()
      .toUpperCase();
  }

  function getCountySet(counties) {
    return new Set(
      (Array.isArray(counties) ? counties : [])
        .map(normalizeCountyName)
        .filter(Boolean)
    );
  }

  function aggregateContestRows(rows, contestType, counties) {
    const countySet = getCountySet(counties);
    const type = String(contestType || '').trim();
    let dem = 0;
    let rep = 0;
    let other = 0;
    let total = 0;
    let demCandidate = '';
    let repCandidate = '';
    const matchedCounties = new Set();

    (Array.isArray(rows) ? rows : []).forEach(row => {
      const countyRaw = ((row?.county || '').toString().split(' - ')[0] || '').trim();
      const countyNorm = normalizeCountyName(countyRaw);
      if (!countyNorm || !countySet.has(countyNorm)) return;

      matchedCounties.add(countyNorm);
      dem += Number(row?.[`${type}_dem`] || 0);
      rep += Number(row?.[`${type}_rep`] || 0);
      other += Number(row?.[`${type}_other`] || 0);
      total += Number(row?.[`${type}_total`] || 0);
      if (!demCandidate) {
        demCandidate = (row?.[`${type}_dem_candidate`] || '').toString().trim();
      }
      if (!repCandidate) {
        repCandidate = (row?.[`${type}_rep_candidate`] || '').toString().trim();
      }
    });

    return {
      dem,
      rep,
      other,
      total,
      demCandidate,
      repCandidate,
      matchedCounties: matchedCounties.size,
      totalCounties: countySet.size
    };
  }

  return {
    normalizeCountyName,
    getCountySet,
    aggregateContestRows
  };
}));
