(function initializeAtlasClassification(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AtlasClassification = Object.freeze(api);
}(typeof globalThis !== 'undefined' ? globalThis : this, function createAtlasClassification() {
  function classifyLabel(longDelta, recentDelta, flip, options = {}) {
    const tone = String(options.tone || '').trim().toLowerCase();
    const marginParty = String(options.marginParty || '').trim().toLowerCase();
    const longThreshold = Number.isFinite(Number(options.longThreshold))
      ? Number(options.longThreshold)
      : 2.5;
    const recentThreshold = Number.isFinite(Number(options.recentThreshold))
      ? Number(options.recentThreshold)
      : 1.25;

    if (flip) {
      if (tone === 'rep') return 'Recent flip to the GOP';
      if (tone === 'dem') return 'Recent flip to Democrats';
      return 'Recent flip';
    }

    const longSign = Number.isFinite(longDelta)
      ? (longDelta > longThreshold ? 'rep' : (longDelta < -longThreshold ? 'dem' : 'neutral'))
      : 'neutral';
    const recentSign = Number.isFinite(recentDelta)
      ? (recentDelta > recentThreshold ? 'rep' : (recentDelta < -recentThreshold ? 'dem' : 'neutral'))
      : 'neutral';

    if (longSign === 'rep' && recentSign === 'rep') return 'Moving right';
    if (longSign === 'dem' && recentSign === 'dem') return 'Moving left';
    if (longSign === 'rep' && recentSign === 'dem') {
      if (marginParty === 'rep') return 'Red-leaning, cooling';
      if (marginParty === 'dem') return 'Democrats pushing back';
      return 'Mixed signals';
    }
    if (longSign === 'dem' && recentSign === 'rep') {
      if (marginParty === 'dem') return 'Blue-leaning, cooling';
      if (marginParty === 'rep') return 'GOP pushing back';
      return 'Mixed signals';
    }
    if (marginParty === 'rep' && recentSign === 'dem') return 'Red-leaning, cooling';
    if (marginParty === 'dem' && recentSign === 'rep') return 'Blue-leaning, cooling';
    if (marginParty === 'rep' && recentSign === 'rep') return 'Moving right';
    if (marginParty === 'dem' && recentSign === 'dem') return 'Moving left';
    if (marginParty === 'rep') return 'Solidly Republican';
    if (marginParty === 'dem') return 'Solidly Democratic';
    return 'Competitive';
  }

  function deriveBaseFromMargin(margin) {
    const value = Number(margin);
    if (!Number.isFinite(value)) return 'Competitive';
    if (value >= 25) return 'Durable Republican Stronghold';
    if (value >= 15) return 'Durable Republican Lean';
    if (value >= 5) return 'Emerging Republican Edge';
    if (value >= 0) return 'Battleground';
    if (value <= -25) return 'Durable Democratic Stronghold';
    if (value <= -15) return 'Durable Democratic Lean';
    if (value <= -5) return 'Emerging Democratic Edge';
    return 'Battleground';
  }

  function classifyTrajectorySnapshot(data) {
    const currentMargin = Number(data?.current_margin);
    const shiftSince2020 = Number(data?.shift_since_2020);
    const shiftSince2000 = Number(data?.shift_since_2000);
    const base = String(data?.base || '').trim() || deriveBaseFromMargin(currentMargin);
    let subtype = 'Stable / Mixed';
    let momentum = '\u2194 Mixed movement';

    if (
      Number.isFinite(shiftSince2000) && shiftSince2000 <= -20 &&
      Number.isFinite(shiftSince2020) && shiftSince2020 <= -1.5 &&
      currentMargin > 5 && currentMargin < 12
    ) {
      subtype = 'Active Suburban Transition';
      momentum = '\u2190 Moving left faster';
    } else if (
      Number.isFinite(shiftSince2000) && shiftSince2000 >= 20 &&
      Number.isFinite(shiftSince2020) && shiftSince2020 >= 1.5 &&
      currentMargin < -5 && currentMargin > -12
    ) {
      subtype = 'Active Republican Transition';
      momentum = '\u2192 Moving right faster';
    } else if (
      Number.isFinite(shiftSince2000) && shiftSince2000 <= -5 &&
      Number.isFinite(shiftSince2020) && shiftSince2020 > 0
    ) {
      subtype = 'Suburbanizing (Lagging)';
      momentum = '\u2194 Long run left, short run right';
    } else if (
      Number.isFinite(shiftSince2000) && shiftSince2000 >= 5 &&
      Number.isFinite(shiftSince2020) && shiftSince2020 < 0
    ) {
      subtype = 'Counter-Suburbanizing (Lagging)';
      momentum = '\u2194 Long run right, short run left';
    } else if (Number.isFinite(shiftSince2020) && shiftSince2020 <= -2 && currentMargin > 10) {
      subtype = 'Red-leaning, cooling';
      momentum = '\u2190 Moving left';
    } else if (Number.isFinite(shiftSince2020) && shiftSince2020 >= 2 && currentMargin < -10) {
      subtype = 'Blue-leaning, cooling';
      momentum = '\u2192 Moving right';
    } else if (
      Number.isFinite(shiftSince2000) && shiftSince2000 >= 10 &&
      Number.isFinite(shiftSince2020) && shiftSince2020 >= 1.25
    ) {
      subtype = 'Moving right';
      momentum = '\u2192 Moving right';
    } else if (
      Number.isFinite(shiftSince2000) && shiftSince2000 <= -10 &&
      Number.isFinite(shiftSince2020) && shiftSince2020 <= -1.25
    ) {
      subtype = 'Moving left';
      momentum = '\u2190 Moving left';
    } else if (Number.isFinite(shiftSince2000) && shiftSince2000 >= 18 && currentMargin < 15) {
      subtype = 'Breaking right';
      momentum = '\u2192 Strong move right';
    } else if (Number.isFinite(shiftSince2000) && shiftSince2000 <= -18 && currentMargin < 0) {
      subtype = 'Breaking left';
      momentum = '\u2190 Strong move left';
    }

    return { base, subtype, momentum };
  }

  function classifyGrowthType(countyName) {
    const normalized = String(countyName || '').trim().toUpperCase();
    if (!normalized) return '\u{1F3ED} Stable / Local Growth';

    const coastal = new Set(['YUMA', 'PIMA', 'SANTA CRUZ', 'COCHISE', 'MOHAVE', 'LA PAZ']);
    const phoenixMetro = new Set(['MARICOPA', 'PINAL']);
    const border = new Set(['YUMA', 'SANTA CRUZ', 'COCHISE', 'GREENLEE']);

    if (coastal.has(normalized)) return '\u{1F30A} Desert / Border Growth';
    if (phoenixMetro.has(normalized)) return '\u{1F306} Metro Spillover';
    if (border.has(normalized)) return '\u{1F6E3}\uFE0F Border Corridor Growth';
    return '\u{1F3ED} Stable / Local Growth';
  }

  function getSignedMarginTowardGop(row) {
    if (!row) return 0;
    const explicit = Number(row.margin_pct);
    const winnerRaw = String(row.winner || '').trim().toUpperCase();
    const winner = winnerRaw === 'R' ? 'REP' : (winnerRaw === 'D' ? 'DEM' : winnerRaw);
    if (Number.isFinite(explicit) && explicit !== 0) {
      if (winner === 'REP') return Math.abs(explicit);
      if (winner === 'DEM') return -Math.abs(explicit);
      return explicit;
    }
    const repVotes = Number(row.rep_votes);
    const demVotes = Number(row.dem_votes);
    const totalVotes = Number(row.total_votes);
    if (Number.isFinite(repVotes) && Number.isFinite(demVotes) && Number.isFinite(totalVotes) && totalVotes > 0) {
      return ((repVotes - demVotes) / totalVotes) * 100;
    }
    return 0;
  }

  function formatShiftLabel(delta, noiseThreshold = 0) {
    const value = delta;
    const threshold = Math.max(0, Number(noiseThreshold) || 0);
    if (!Number.isFinite(value) || Math.abs(value) < threshold) return '\u2194 No clear shift';
    return `${value > 0 ? '\u2192' : '\u2190'} ${value > 0 ? 'R' : 'D'}+${Math.abs(value).toFixed(2)}%`;
  }

  function formatWinnerLabel(row, signedMargin) {
    const absoluteMargin = Math.abs(signedMargin);
    if (absoluteMargin < 0.005) return `${row.year}: Tie`;
    return `${row.year}: ${signedMargin > 0 ? 'R' : 'D'}+${absoluteMargin.toFixed(2)}%`;
  }

  return {
    classifyLabel,
    deriveBaseFromMargin,
    classifyTrajectorySnapshot,
    classifyGrowthType,
    getSignedMarginTowardGop,
    formatShiftLabel,
    formatWinnerLabel
  };
}));
