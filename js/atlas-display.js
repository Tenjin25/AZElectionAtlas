(function initializeAtlasDisplay(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AtlasDisplay = Object.freeze(api);
}(typeof globalThis !== 'undefined' ? globalThis : this, function createAtlasDisplay() {
  function roundForDisplay(value, digits = 2) {
    const n = Number(value || 0);
    if (!Number.isFinite(n)) return 0;
    const d = Math.max(0, Math.floor(Number(digits) || 0));
    const factor = Math.pow(10, d);
    const epsilonNudge = (Math.sign(n) || 1) * Number.EPSILON * 8;
    return Math.round((n + epsilonNudge) * factor) / factor;
  }

  function roundPct2(value) {
    return roundForDisplay(value, 2);
  }

  function toFixedForDisplay(value, digits = 2) {
    const d = Math.max(0, Math.floor(Number(digits) || 0));
    return roundForDisplay(value, d).toFixed(d);
  }

  function formatVotehubPct(value, digits = 2) {
    const n = Number(value);
    if (!Number.isFinite(n)) return (0).toFixed(digits);
    return Number(roundForDisplay(n, digits)).toFixed(digits);
  }

  function formatVotehubVotes(value) {
    const n = Number(value || 0);
    if (!Number.isFinite(n)) return '0';
    return Math.round(n).toLocaleString();
  }

  function partyPctValue(votes, totalVotes) {
    const total = Number(totalVotes) || 0;
    if (total <= 0) return 0;
    return roundPct2((Number(votes || 0) / total) * 100);
  }

  function marginPctValue(repVotes, demVotes, totalVotes) {
    const total = Number(totalVotes) || 0;
    if (total <= 0) return 0;
    return Math.abs((Number(repVotes || 0) - Number(demVotes || 0)) / total * 100);
  }

  function preservesTwoDecimalMargin(marginPct) {
    const pct = Math.abs(Number(marginPct) || 0);
    return roundForDisplay(pct, 2) >= 0.02;
  }

  function closeRaceDisplayDigits(marginPct) {
    return preservesTwoDecimalMargin(marginPct) ? 2 : 3;
  }

  function countyMarginDisplayDigits(marginPct) {
    return preservesTwoDecimalMargin(marginPct) ? 2 : 3;
  }

  function formatPctForCloseRace(value, marginPct) {
    return toFixedForDisplay(Number(value || 0), closeRaceDisplayDigits(marginPct));
  }

  function formatPctForCountyCloseRace(value, marginPct) {
    return toFixedForDisplay(Number(value || 0), countyMarginDisplayDigits(marginPct));
  }

  function marginDisplayDigits(marginPct) {
    return preservesTwoDecimalMargin(marginPct) ? 2 : 3;
  }

  function formatMarginPctForDisplay(marginPct) {
    const pct = Math.abs(Number(marginPct) || 0);
    return toFixedForDisplay(pct, marginDisplayDigits(pct));
  }

  function formatCountyMarginPctForDisplay(marginPct) {
    const pct = Math.abs(Number(marginPct) || 0);
    return toFixedForDisplay(pct, countyMarginDisplayDigits(pct));
  }

  function marginPctDisplayValue(repVotes, demVotes, totalVotes) {
    const rawMarginPct = marginPctValue(repVotes, demVotes, totalVotes);
    const total = Number(totalVotes) || 0;
    if (total <= 0) return 0;
    const repPctDisplay = Number(formatPctForCloseRace((Number(repVotes || 0) / total) * 100, rawMarginPct));
    const demPctDisplay = Number(formatPctForCloseRace((Number(demVotes || 0) / total) * 100, rawMarginPct));
    if (!Number.isFinite(repPctDisplay) || !Number.isFinite(demPctDisplay)) return rawMarginPct;
    const diff = Math.abs(repPctDisplay - demPctDisplay);
    return diff === 0 && rawMarginPct > 0 ? rawMarginPct : diff;
  }

  function countyMarginPctDisplayValue(repVotes, demVotes, totalVotes) {
    const rawMarginPct = marginPctValue(repVotes, demVotes, totalVotes);
    const total = Number(totalVotes) || 0;
    if (total <= 0) return 0;
    const repPctDisplay = Number(formatPctForCountyCloseRace((Number(repVotes || 0) / total) * 100, rawMarginPct));
    const demPctDisplay = Number(formatPctForCountyCloseRace((Number(demVotes || 0) / total) * 100, rawMarginPct));
    if (!Number.isFinite(repPctDisplay) || !Number.isFinite(demPctDisplay)) return rawMarginPct;
    const diff = Math.abs(repPctDisplay - demPctDisplay);
    return diff === 0 && rawMarginPct > 0 ? rawMarginPct : diff;
  }

  function formatCompactTotal(value, thousandsSuffix) {
    const n = Number(value || 0);
    if (!Number.isFinite(n) || n <= 0) return '0';
    if (n >= 1000000) {
      const digits = n >= 10000000 ? 1 : 2;
      return `${(n / 1000000).toFixed(digits)}M`;
    }
    if (n >= 1000) {
      const digits = n >= 100000 ? 0 : 1;
      return `${(n / 1000).toFixed(digits)}${thousandsSuffix}`;
    }
    return n.toLocaleString();
  }

  function formatCompactVoteTotal(value) {
    return formatCompactTotal(value, 'K');
  }

  function formatCompactDeltaTotal(value) {
    return formatCompactTotal(value, 'k');
  }

  function escapeHtml(text) {
    return String(text || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function formatSignedCompactDelta(value) {
    const n = Number(value || 0);
    if (!Number.isFinite(n) || n === 0) return '0';
    return `${n > 0 ? '+' : '-'}${formatCompactDeltaTotal(Math.abs(n))}`;
  }

  function formatSignedPctDelta(value, digits = 1) {
    const n = Number(value);
    if (!Number.isFinite(n) || n === 0) return '0.0%';
    const d = Math.max(0, Number(digits) || 0);
    return `${n > 0 ? '+' : '-'}${Math.abs(n).toFixed(d)}%`;
  }

  return {
    roundPct2,
    roundForDisplay,
    toFixedForDisplay,
    formatVotehubPct,
    formatVotehubVotes,
    partyPctValue,
    marginPctValue,
    preservesTwoDecimalMargin,
    closeRaceDisplayDigits,
    countyMarginDisplayDigits,
    formatPctForCloseRace,
    formatPctForCountyCloseRace,
    marginDisplayDigits,
    formatMarginPctForDisplay,
    formatCountyMarginPctForDisplay,
    marginPctDisplayValue,
    countyMarginPctDisplayValue,
    formatCompactVoteTotal,
    formatCompactDeltaTotal,
    escapeHtml,
    formatSignedCompactDelta,
    formatSignedPctDelta
  };
}));
