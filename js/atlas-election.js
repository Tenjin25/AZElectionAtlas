(function initializeAtlasElection(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AtlasElection = Object.freeze(api);
}(typeof globalThis !== 'undefined' ? globalThis : this, function createAtlasElection() {
  function finalizeVoteSet(demVotes, repVotes, otherVotes, totalHint = 0) {
    const dem = Math.max(0, Math.round(Number(demVotes) || 0));
    const rep = Math.max(0, Math.round(Number(repVotes) || 0));
    const other = Math.max(0, Math.round(Number(otherVotes) || 0));
    const sum = dem + rep + other;
    const hint = Math.max(0, Math.round(Number(totalHint) || 0));
    return { dem, rep, other, total: sum > 0 ? sum : hint };
  }

  function shiftVotesBySwingPct(demVotes, repVotes, otherVotes, totalVotes, swingPct = 0) {
    const total = Number(totalVotes || 0);
    let dem = Number(demVotes || 0);
    let rep = Number(repVotes || 0);
    const other = Number(otherVotes || 0);
    if (!Number.isFinite(total) || total <= 0) return finalizeVoteSet(dem, rep, other, total);
    const swing = Number(swingPct || 0);
    if (Math.abs(swing) < 0.0001) return finalizeVoteSet(dem, rep, other, total);
    const originalTwoParty = Math.max(0, dem + rep);
    if (originalTwoParty <= 0) return finalizeVoteSet(dem, rep, other, total);
    const deltaVotes = (swing / 200) * total;
    dem = Math.max(0, dem + deltaVotes);
    rep = Math.max(0, rep - deltaVotes);
    const adjustedTwoParty = dem + rep;
    if (adjustedTwoParty > 0) {
      const scale = originalTwoParty / adjustedTwoParty;
      dem *= scale;
      rep *= scale;
    }
    return finalizeVoteSet(dem, rep, other, total);
  }

  function quantile(sortedValues, probability) {
    if (!sortedValues || !sortedValues.length) return 0;
    const index = Math.min(
      sortedValues.length - 1,
      Math.max(0, Math.floor((sortedValues.length - 1) * probability))
    );
    return Number(sortedValues[index] || 0);
  }

  function parseContestValue(contestValue) {
    const raw = String(contestValue || '');
    const index = raw.lastIndexOf('_');
    if (index <= 0) return [raw, ''];
    return [raw.substring(0, index), raw.substring(index + 1)];
  }

  function winnerFromSignedMarginPct(signedPct) {
    const value = Number(signedPct) || 0;
    if (value > 0) return 'R';
    if (value < 0) return 'D';
    return 'T';
  }

  function flipInfo(previousSignedPct, currentSignedPct) {
    const from = winnerFromSignedMarginPct(previousSignedPct);
    const to = winnerFromSignedMarginPct(currentSignedPct);
    return { flipped: from !== 'T' && to !== 'T' && from !== to, from, to };
  }

  return {
    finalizeVoteSet,
    shiftVotesBySwingPct,
    quantile,
    parseContestValue,
    winnerFromSignedMarginPct,
    flipInfo
  };
}));
