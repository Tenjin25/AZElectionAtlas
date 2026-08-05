(function initializeAtlasTrends(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AtlasTrends = Object.freeze(api);
}(typeof globalThis !== 'undefined' ? globalThis : this, function createAtlasTrends() {
  function normalizeSeries(series) {
    return (Array.isArray(series) ? series : [])
      .slice()
      .filter(row => row && Number.isFinite(Number(row.year)))
      .sort((a, b) => Number(a.year) - Number(b.year));
  }

  function analyzeSeries(series, options = {}) {
    const signedMargin = typeof options.signedMargin === 'function'
      ? options.signedMargin
      : (() => 0);
    const displaySignedMargin = typeof options.displaySignedMargin === 'function'
      ? options.displaySignedMargin
      : signedMargin;
    const sorted = normalizeSeries(series);
    if (!sorted.length) return { sorted };

    const latest = sorted[sorted.length - 1];
    const latestSigned = displaySignedMargin(latest);
    const first = sorted[0];
    const firstSigned = displaySignedMargin(first);
    const electionCount = sorted.length;
    const yearSpan = Number(latest?.year) - Number(first?.year);
    const historySparse = electionCount < 4;
    const longShiftTowardGop = latestSigned - firstSigned;
    const row2008 = sorted.find(row => Number(row.year) === 2008);
    const shiftSince2008 = row2008 ? latestSigned - displaySignedMargin(row2008) : null;
    const row2020 = sorted.find(row => Number(row.year) === 2020);
    const shiftSince2020 = row2020 ? latestSigned - displaySignedMargin(row2020) : NaN;
    const row2000 = sorted.find(row => Number(row.year) === 2000);
    const shiftSince2000 = latestSigned - displaySignedMargin(row2000 || first);
    const previous = sorted.length >= 2 ? sorted[sorted.length - 2] : null;
    const previousSigned = previous ? displaySignedMargin(previous) : null;
    const recentShiftTowardGop = previous ? latestSigned - previousSigned : null;
    const recentWindowStart = sorted.length >= 3 ? sorted[Math.max(0, sorted.length - 3)] : first;
    const recentWindowStartSigned = recentWindowStart ? signedMargin(recentWindowStart) : null;
    const recentWindowShiftTowardGop = Number.isFinite(recentWindowStartSigned)
      ? latestSigned - recentWindowStartSigned
      : recentShiftTowardGop;
    const cycleShiftLabel = previous
      ? (Number(previous.year) === Number(first.year) ? 'Last Cycle' : `Since ${previous.year}`)
      : '';

    const recentSlice = sorted.slice(-3);
    let rightwardSteps = 0;
    let leftwardSteps = 0;
    for (let index = 1; index < recentSlice.length; index += 1) {
      const previousMargin = signedMargin(recentSlice[index - 1]);
      const currentMargin = signedMargin(recentSlice[index]);
      const deltaTowardGop = currentMargin - previousMargin;
      if (deltaTowardGop > 0.5) rightwardSteps += 1;
      if (deltaTowardGop < -0.5) leftwardSteps += 1;
    }

    const absLatest = Math.abs(latestSigned);
    const absFirst = Math.abs(firstSigned);
    const sameSideLongTerm =
      (latestSigned > 0 && firstSigned > 0) ||
      (latestSigned < 0 && firstSigned < 0);
    const crossedParties =
      (latestSigned > 0 && firstSigned < 0) ||
      (latestSigned < 0 && firstSigned > 0);
    const towardCurrentSide = Number.isFinite(recentShiftTowardGop)
      ? (latestSigned > 0 ? recentShiftTowardGop : (latestSigned < 0 ? -recentShiftTowardGop : 0))
      : 0;
    const towardCurrentSideLong = Number.isFinite(longShiftTowardGop)
      ? (latestSigned > 0 ? longShiftTowardGop : (latestSigned < 0 ? -longShiftTowardGop : 0))
      : 0;
    const towardCurrentSideWindow = Number.isFinite(recentWindowShiftTowardGop)
      ? (latestSigned > 0 ? recentWindowShiftTowardGop : (latestSigned < 0 ? -recentWindowShiftTowardGop : 0))
      : towardCurrentSide;
    const awayFromCurrentSide = Number.isFinite(recentShiftTowardGop) ? -towardCurrentSide : 0;
    const awayFromCurrentSideLong = Number.isFinite(towardCurrentSideLong) ? -towardCurrentSideLong : 0;
    const hasLongAnchor = !!(row2000 || row2008 || (Number.isFinite(yearSpan) && yearSpan >= 16));

    let swingSum = 0;
    if (electionCount >= 3) {
      for (let index = 1; index < sorted.length; index += 1) {
        swingSum += Math.abs(signedMargin(sorted[index]) - signedMargin(sorted[index - 1]));
      }
    }
    const avgAbsSwing = electionCount < 3 ? 0 : swingSum / Math.max(1, sorted.length - 1);
    const isElastic = electionCount >= 5 && avgAbsSwing >= 4.25 && absLatest < 12;

    return {
      sorted,
      latest,
      latestSigned,
      first,
      firstSigned,
      electionCount,
      yearSpan,
      historySparse,
      longShiftTowardGop,
      row2008,
      shiftSince2008,
      row2020,
      shiftSince2020,
      row2000,
      shiftSince2000,
      previous,
      previousSigned,
      recentShiftTowardGop,
      recentWindowStart,
      recentWindowStartSigned,
      recentWindowShiftTowardGop,
      cycleShiftLabel,
      rightwardSteps,
      leftwardSteps,
      absLatest,
      absFirst,
      sameSideLongTerm,
      crossedParties,
      towardCurrentSide,
      towardCurrentSideLong,
      towardCurrentSideWindow,
      awayFromCurrentSide,
      awayFromCurrentSideLong,
      hasLongAnchor,
      avgAbsSwing,
      isElastic
    };
  }

  return {
    normalizeSeries,
    analyzeSeries
  };
}));
