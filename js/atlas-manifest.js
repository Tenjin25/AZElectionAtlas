(function initializeAtlasManifest(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AtlasManifest = Object.freeze(api);
}(typeof globalThis !== 'undefined' ? globalThis : this, function createAtlasManifest() {
  const COUNCIL_OF_STATE_CONTEST_TYPES = new Set([
    'governor',
    'lieutenant_governor',
    'attorney_general',
    'auditor',
    'secretary_of_state',
    'treasurer',
    'labor_commissioner',
    'insurance_commissioner',
    'agriculture_commissioner',
    'superintendent'
  ]);

  const NAMED_JUDICIAL_SEAT_MAP = Object.freeze({
    nc_supreme_court_associate_justice_brady_seat: '1',
    nc_supreme_court_associate_justice_newby_seat: '2',
    nc_supreme_court_associate_justice_hudson_seat: '3',
    nc_supreme_court_associate_justice_beasley_seat: '4',
    nc_supreme_court_associate_justice_martin_seat: '5',
    nc_supreme_court_associate_justice_edmunds_seat: '6',
    nc_supreme_court_chief_justice_parker_seat: '1',
    nc_court_of_appeals_judge_wynn_seat: '1',
    nc_court_of_appeals_judge_calabria_seat: '2',
    nc_court_of_appeals_judge_mcgee_seat: '4',
    nc_court_of_appeals_judge_bryant_seat: '5',
    nc_court_of_appeals_judge_thigpen_seat: '6',
    nc_court_of_appeals_judge_mccullough_seat: '7',
    nc_court_of_appeals_judge_davis_seat: '7',
    nc_court_of_appeals_judge_stephens_seat: '11',
    nc_court_of_appeals_judge_arrowood_seat: '12',
    nc_court_of_appeals_judge_dietz_seat: '12',
    nc_court_of_appeals_judge_martin_seat: '10',
    nc_court_of_appeals_judge_zachary_seat: '14',
    nc_court_of_appeals_judge_geer_seat: '15'
  });

  function parseMajorPartyContested(entry) {
    const raw = entry?.major_party_contested;
    if (typeof raw === 'boolean') return raw;
    if (typeof raw === 'number') return raw !== 0;
    if (typeof raw === 'string') {
      const value = raw.trim().toLowerCase();
      if (value === 'false' || value === '0' || value === 'no') return false;
      if (value === 'true' || value === '1' || value === 'yes') return true;
    }
    const demTotal = Number(entry?.dem_total || 0);
    const repTotal = Number(entry?.rep_total || 0);
    if (demTotal > 0 || repTotal > 0) return demTotal > 0 && repTotal > 0;
    return true;
  }

  function shouldIncludeManifestEntry(entry) {
    if (!entry) return false;
    const contestType = String(entry.contest_type || '').trim();
    const rows = Number(entry.rows || entry.districts || 0) || 0;
    if (!contestType || rows <= 0) return false;
    const isJudicial = contestType.startsWith('nc_supreme_court_') ||
      contestType.startsWith('nc_court_of_appeals_');
    if (isJudicial && !parseMajorPartyContested(entry)) return false;
    if (COUNCIL_OF_STATE_CONTEST_TYPES.has(contestType) && !parseMajorPartyContested(entry)) {
      return false;
    }
    return true;
  }

  function getVisibleManifestEntries(entries) {
    return (Array.isArray(entries) ? entries : []).filter(shouldIncludeManifestEntry);
  }

  function getJudicialSeatFamilyKey(contestType, year = NaN) {
    const value = String(contestType || '').trim();
    if (!value) return null;
    let match = /^nc_supreme_court_associate_justice_seat_0*(\d+)$/i.exec(value);
    if (match) return `sc:${Number(match[1])}`;
    match = /^nc_supreme_court_chief_justice_seat_0*(\d+)$/i.exec(value);
    if (match) return `cj:${Number(match[1])}`;
    match = /^nc_court_of_appeals_judge_seat_0*(\d+)$/i.exec(value);
    if (match) return `coa:${Number(match[1])}`;
    if (value === 'nc_court_of_appeals_judge_hunter_seat') {
      return Number(year) === 2016 ? 'coa:13' : 'coa:8';
    }
    if (value === 'nc_supreme_court_chief_justice_parker_seat') return 'cj:1';
    const namedSeat = NAMED_JUDICIAL_SEAT_MAP[value];
    if (!namedSeat) return null;
    if (value.startsWith('nc_supreme_court_chief_justice_')) return `cj:${Number(namedSeat)}`;
    if (value.startsWith('nc_supreme_court_associate_justice_')) return `sc:${Number(namedSeat)}`;
    if (value.startsWith('nc_court_of_appeals_judge_')) return `coa:${Number(namedSeat)}`;
    return null;
  }

  function judicialContestTypeMatchesFamily(contestType, familyKey, year = NaN) {
    const key = getJudicialSeatFamilyKey(contestType, year);
    return !!(familyKey && key && key === familyKey);
  }

  function listJudicialFamilyManifestEntries(familyKey, manifestEntries) {
    const out = [];
    if (!familyKey || !Array.isArray(manifestEntries)) return out;
    manifestEntries.forEach(entry => {
      if (!entry) return;
      const contestType = String(entry.contest_type || '').trim();
      const year = Number(entry.year);
      if (!contestType || !Number.isFinite(year) || year <= 0) return;
      if (!judicialContestTypeMatchesFamily(contestType, familyKey, year)) return;
      out.push({
        year,
        contest_type: contestType,
        file: entry.file || '',
        scope: entry.scope || ''
      });
    });
    return out;
  }

  function pickPriorJudicialFamilyEntry(familyEntries, year) {
    const targetYear = Number(year);
    if (!Number.isFinite(targetYear) || !Array.isArray(familyEntries) || !familyEntries.length) {
      return null;
    }
    const earlier = familyEntries
      .filter(entry => Number(entry.year) < targetYear)
      .sort((a, b) => Number(b.year) - Number(a.year));
    return earlier[0] || null;
  }

  return {
    COUNCIL_OF_STATE_CONTEST_TYPES,
    NAMED_JUDICIAL_SEAT_MAP,
    parseMajorPartyContested,
    shouldIncludeManifestEntry,
    getVisibleManifestEntries,
    getJudicialSeatFamilyKey,
    judicialContestTypeMatchesFamily,
    listJudicialFamilyManifestEntries,
    pickPriorJudicialFamilyEntry
  };
}));
