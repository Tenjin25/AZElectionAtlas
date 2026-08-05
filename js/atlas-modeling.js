(function initializeAtlasModeling(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AtlasModeling = Object.freeze(api);
}(typeof globalThis !== 'undefined' ? globalThis : this, function createAtlasModeling() {
  function stableStringify(value) {
    const seen = new WeakSet();
    const recur = (item) => {
      if (item === null || item === undefined) return item;
      const type = typeof item;
      if (type === 'number') return Number.isFinite(item) ? item : String(item);
      if (type === 'string' || type === 'boolean') return item;
      if (type !== 'object') return String(item);
      if (seen.has(item)) return '[Circular]';
      seen.add(item);
      if (Array.isArray(item)) return item.map(recur);
      const output = {};
      Object.keys(item).sort().forEach(key => { output[key] = recur(item[key]); });
      return output;
    };
    try {
      return JSON.stringify(recur(value));
    } catch (_) {
      return String(value);
    }
  }

  function getDefinitionSignature(modeledDefinition) {
    if (!modeledDefinition) return '';
    const contestType = String(modeledDefinition?.contestType || '').trim();
    const year = Number(modeledDefinition?.year);
    if (!contestType || !Number.isFinite(year)) return '';
    return `${contestType}_${year}|${stableStringify(modeledDefinition)}`;
  }

  function getCountyBehaviorLabel(mode) {
    const key = String(mode || 'balanced').trim().toLowerCase();
    const labels = {
      balanced: 'Balanced counties',
      suburban_rebound: 'Suburban rebound',
      rural_surge: 'Rural surge',
      incumbent_friendly: 'Incumbent-friendly',
      volatile: 'Volatile / low-confidence'
    };
    return labels[key] || labels.balanced;
  }

  function inferConfidence(modeled, baseModeled = null) {
    if (!modeled) return { label: 'Low', range: '\u00b12.0 pts' };
    const blend = Number(modeled?.blendWeight);
    const bonus = Number(modeled?.candidateBonusScale);
    const turnout = Number(modeled?.turnoutFactor);
    const behavior = String(modeled?.countyBehaviorMode || 'balanced').trim().toLowerCase();
    const uncertaintyBoost = Number(modeled?.uncertaintyBoost);
    const blendDelta = (baseModeled && Number.isFinite(blend) && Number.isFinite(Number(baseModeled?.blendWeight)))
      ? Math.abs(blend - Number(baseModeled.blendWeight))
      : 0;
    const turnoutDelta = (baseModeled && Number.isFinite(turnout) && Number.isFinite(Number(baseModeled?.turnoutFactor)))
      ? Math.abs(turnout - Number(baseModeled.turnoutFactor))
      : 0;
    const bonusDelta = (baseModeled && Number.isFinite(bonus) && Number.isFinite(Number(baseModeled?.candidateBonusScale)))
      ? Math.abs(bonus - Number(baseModeled.candidateBonusScale))
      : 0;

    let score = 0;
    score += Math.max(0, 0.30 - (blendDelta * 0.65));
    score += Math.max(0, 0.30 - (turnoutDelta * 0.80));
    score += Math.max(0, 0.22 - (bonusDelta * 0.22));
    if (behavior === 'balanced' || behavior === 'incumbent_friendly') score += 0.14;
    if (behavior === 'suburban_rebound' || behavior === 'rural_surge') score += 0.05;
    if (behavior === 'volatile') score -= 0.22;
    if (Number.isFinite(uncertaintyBoost) && uncertaintyBoost > 1) {
      score -= Math.min(0.18, (uncertaintyBoost - 1) * 0.28);
    }

    let label = 'Medium';
    if (score >= 0.60) label = 'High';
    else if (score < 0.36) label = 'Low';
    const range = label === 'High' ? '\u00b11.4 pts' : (label === 'Medium' ? '\u00b12.0 pts' : '\u00b13.0 pts');
    return { label, range };
  }

  function computeStatewideMarginFromDistrictResults(results, signedMargin) {
    const totals = Object.values(results || {}).reduce((accumulator, row) => {
      accumulator.dem += Number(row?.dem_votes || 0);
      accumulator.rep += Number(row?.rep_votes || 0);
      accumulator.total += Number(row?.total_votes || 0);
      return accumulator;
    }, { dem: 0, rep: 0, total: 0 });
    return signedMargin(totals.dem, totals.rep, totals.total);
  }

  function buildContestRow(baseRow, modeledDefinition, climateSwingPct, options = {}, dependencies = {}) {
    const baseContestType = modeledDefinition.baseContestType;
    const contestType = modeledDefinition.contestType;
    const shifted = dependencies.shiftVotes(
      Number(baseRow?.[`${baseContestType}_dem`] || 0),
      Number(baseRow?.[`${baseContestType}_rep`] || 0),
      Number(baseRow?.[`${baseContestType}_other`] || 0),
      Number(baseRow?.[`${baseContestType}_total`] || 0),
      climateSwingPct
    );
    const targetTotal = options && Number.isFinite(Number(options.targetTotal))
      ? Number(options.targetTotal)
      : null;
    const scaled = targetTotal ? dependencies.rescaleVotes(shifted, targetTotal) : shifted;
    const signed = dependencies.signedMargin(scaled.dem, scaled.rep, scaled.total);
    const winner = signed > 0 ? 'REP' : (signed < 0 ? 'DEM' : 'TIE');
    const winnerKey = winner === 'REP' ? 'R' : (winner === 'DEM' ? 'D' : 'T');
    const color = winnerKey === 'T'
      ? '#9ca3af'
      : dependencies.colorForMargin(Math.abs(signed), winnerKey);
    const row = {
      year: Number(modeledDefinition.year),
      county: baseRow?.county || '',
      [`${contestType}_dem`]: scaled.dem,
      [`${contestType}_rep`]: scaled.rep,
      [`${contestType}_other`]: scaled.other,
      [`${contestType}_total`]: scaled.total,
      [`${contestType}_dem_candidate`]: modeledDefinition.demCandidate || '',
      [`${contestType}_rep_candidate`]: modeledDefinition.repCandidate || '',
      [`${contestType}_margin`]: Math.round(scaled.rep - scaled.dem),
      [`${contestType}_margin_pct`]: signed,
      [`${contestType}_winner`]: winner,
      [`${contestType}_color`]: color
    };
    const modelMeta = options?.modelMeta || null;
    if (modelMeta && Number.isFinite(Number(modelMeta.baselineNoCandidateSigned))) {
      row.__model_baseline_margin_pct = Number(modelMeta.baselineNoCandidateSigned);
      row.__model_with_candidates_margin_pct = Number.isFinite(Number(modelMeta.desiredSigned))
        ? Number(modelMeta.desiredSigned)
        : Number(signed);
      row.__model_candidate_effect_d_pts = Number.isFinite(Number(modelMeta.candidateEffectDemPts))
        ? Number(modelMeta.candidateEffectDemPts)
        : 0;
      row.__model_candidate_effect_durable_pts = Number.isFinite(Number(modelMeta.candidateEffectDurableDemPts))
        ? Number(modelMeta.candidateEffectDurableDemPts)
        : 0;
      row.__model_candidate_effect_personal_pts = Number.isFinite(Number(modelMeta.candidateEffectPersonalDemPts))
        ? Number(modelMeta.candidateEffectPersonalDemPts)
        : 0;
      row.__model_candidate_effect_local_d_pts = Number.isFinite(Number(modelMeta.candidateEffectLocalDemPts))
        ? Number(modelMeta.candidateEffectLocalDemPts)
        : 0;
      row.__model_candidate_effect_county_type_d_pts = Number.isFinite(Number(modelMeta.candidateEffectCountyTypeDemPts))
        ? Number(modelMeta.candidateEffectCountyTypeDemPts)
        : 0;
      row.__model_anchor_spread_pts = Number.isFinite(Number(modelMeta.anchorSpreadPts))
        ? Number(modelMeta.anchorSpreadPts)
        : NaN;
      row.__model_input_disagreement = String(modelMeta.inputDisagreement || '');
      row.__model_anchors_aligned = Number(modelMeta.anchorsAligned || 0);
      row.__model_confidence_label = String(modelMeta.modelConfidenceLabel || '');
      row.__model_confidence_band = String(modelMeta.modelConfidenceBand || '');
      row.__model_influence_presidential_climate_pts = Number.isFinite(Number(modelMeta.influencePresidentialClimatePts))
        ? Number(modelMeta.influencePresidentialClimatePts)
        : NaN;
      row.__model_influence_senate_baseline_pts = Number.isFinite(Number(modelMeta.influenceSenateBaselinePts))
        ? Number(modelMeta.influenceSenateBaselinePts)
        : NaN;
      row.__model_influence_extra_movement_pts = Number.isFinite(Number(modelMeta.influenceExtraModeledMovementPts))
        ? Number(modelMeta.influenceExtraModeledMovementPts)
        : NaN;
      row.__model_influence_crossover_dem_pts = Number.isFinite(Number(modelMeta.influenceCrossoverDemPts))
        ? Number(modelMeta.influenceCrossoverDemPts)
        : NaN;
      row.__model_explain_tags = Array.isArray(modelMeta.explanationTags)
        ? modelMeta.explanationTags.map(value => String(value || '').trim()).filter(Boolean).join(' \u2022 ')
        : '';
    }
    return row;
  }

  function buildDistrictResultRow(baseRow, modeledDefinition, climateSwingPct, targetTotalOverride, dependencies = {}) {
    const shifted = dependencies.shiftVotes(
      Number(baseRow?.dem_votes || 0),
      Number(baseRow?.rep_votes || 0),
      Number(baseRow?.other_votes || 0),
      Number(baseRow?.total_votes || 0),
      climateSwingPct
    );
    const desired = Number(targetTotalOverride);
    const scaled = Number.isFinite(desired) && desired > 0
      ? dependencies.rescaleVotes(shifted, desired)
      : shifted;
    const signed = dependencies.signedMargin(scaled.dem, scaled.rep, scaled.total);
    const winner = signed > 0 ? 'REP' : (signed < 0 ? 'DEM' : 'TIE');
    const winnerKey = winner === 'REP' ? 'R' : (winner === 'DEM' ? 'D' : 'T');
    const color = winnerKey === 'T'
      ? '#9ca3af'
      : dependencies.colorForMargin(Math.abs(signed), winnerKey);
    return {
      ...baseRow,
      dem_votes: scaled.dem,
      rep_votes: scaled.rep,
      other_votes: scaled.other,
      total_votes: scaled.total,
      dem_candidate: modeledDefinition.demCandidate || '',
      rep_candidate: modeledDefinition.repCandidate || '',
      margin: Math.round(scaled.rep - scaled.dem),
      margin_pct: signed,
      winner,
      competitiveness: {
        ...(baseRow?.competitiveness || {}),
        color
      }
    };
  }

  function aggregatePrecinctRowsToDistricts(modeledRows, crosswalkByPrecinct, options = {}) {
    if (!Array.isArray(modeledRows) || !modeledRows.length || !(crosswalkByPrecinct instanceof Map) || !crosswalkByPrecinct.size) {
      return null;
    }

    const contestType = String(options.contestType || '').trim();
    if (!contestType) return null;
    const normalizePrecinctKey = typeof options.normalizePrecinctKey === 'function'
      ? options.normalizePrecinctKey
      : value => String(value || '').replace(/\s+/g, ' ').trim().toUpperCase();
    const normalizeDistrictNumber = typeof options.normalizeDistrictNumber === 'function'
      ? options.normalizeDistrictNumber
      : value => String(value || '').replace(/[^0-9]/g, '').replace(/^0+(?=\d)/, '');
    const referenceResults = options.referenceResults || {};
    const demCandidate = String(options.demCandidate || '');
    const repCandidate = String(options.repCandidate || '');
    const results = {};
    const countyDistrictVotes = new Map();
    const unmatchedRows = [];
    const referenceFallbackCounties = new Set();
    let matchedRows = 0;
    let matchedVotes = 0;
    let allocatedUnmatchedVotes = 0;
    let sourceVotes = 0;

    const addDistrictVotes = (districtId, dem, rep, other) => {
      const district = results[districtId] || {
        dem_votes: 0,
        rep_votes: 0,
        other_votes: 0,
        total_votes: 0,
        dem_candidate: demCandidate,
        rep_candidate: repCandidate
      };
      district.dem_votes += dem;
      district.rep_votes += rep;
      district.other_votes += other;
      district.total_votes += dem + rep + other;
      results[districtId] = district;
    };

    modeledRows.forEach(row => {
      const precinctKey = normalizePrecinctKey(
        row?.precinct || row?.precinct_key || row?.county || row?.name || ''
      );
      const dem = Number(row?.[`${contestType}_dem`] || 0);
      const rep = Number(row?.[`${contestType}_rep`] || 0);
      const other = Number(row?.[`${contestType}_other`] || 0);
      const explicitTotal = Number(row?.[`${contestType}_total`] || 0);
      const total = explicitTotal > 0 ? explicitTotal : (dem + rep + other);
      sourceVotes += total;

      const entries = crosswalkByPrecinct.get(precinctKey) || [];
      const weightTotal = entries.reduce((sum, entry) => sum + Number(entry?.weight || 0), 0);
      if (!entries.length || !(weightTotal > 0)) {
        unmatchedRows.push({ precinctKey, dem, rep, other });
        return;
      }

      matchedRows += 1;
      matchedVotes += total;
      const county = precinctKey.split(' - ')[0].trim();
      if (!countyDistrictVotes.has(county)) countyDistrictVotes.set(county, {});
      const countyDistricts = countyDistrictVotes.get(county);
      entries.forEach(entry => {
        const districtId = normalizeDistrictNumber(entry?.districtNum);
        const weight = Number(entry?.weight || 0) / weightTotal;
        if (!districtId || !(weight > 0)) return;
        const allocatedDem = dem * weight;
        const allocatedRep = rep * weight;
        const allocatedOther = other * weight;
        addDistrictVotes(districtId, allocatedDem, allocatedRep, allocatedOther);
        const countyDistrict = countyDistricts[districtId] || { dem: 0, rep: 0, other: 0 };
        countyDistrict.dem += allocatedDem;
        countyDistrict.rep += allocatedRep;
        countyDistrict.other += allocatedOther;
        countyDistricts[districtId] = countyDistrict;
      });
    });

    // Non-geographic rows follow the matched party distribution within their county.
    // If a county's precinct names changed wholesale, use the reference district slice
    // to retain its district footprint while preserving the new modeled county totals.
    unmatchedRows.forEach(row => {
      const county = row.precinctKey.split(' - ')[0].trim();
      let countyDistricts = countyDistrictVotes.get(county) || {};
      if (!Object.keys(countyDistricts).length) {
        const candidateDistrictIds = new Set();
        crosswalkByPrecinct.forEach((entries, precinctKey) => {
          if (!String(precinctKey || '').startsWith(`${county} - `)) return;
          (entries || []).forEach(entry => {
            const districtId = normalizeDistrictNumber(entry?.districtNum);
            if (districtId) candidateDistrictIds.add(districtId);
          });
        });
        countyDistricts = {};
        candidateDistrictIds.forEach(districtId => {
          const reference = referenceResults[districtId] || {};
          countyDistricts[districtId] = {
            dem: Number(reference.dem_votes || 0),
            rep: Number(reference.rep_votes || 0),
            other: Number(reference.other_votes || 0)
          };
        });
        if (Object.keys(countyDistricts).length) referenceFallbackCounties.add(county);
      }

      const districtIds = Object.keys(countyDistricts);
      if (!districtIds.length) return;
      const componentTotal = component => districtIds.reduce(
        (sum, districtId) => sum + Number(countyDistricts[districtId]?.[component] || 0),
        0
      );
      const fallbackTotal = districtIds.reduce((sum, districtId) => {
        const node = countyDistricts[districtId] || {};
        return sum + Number(node.dem || 0) + Number(node.rep || 0) + Number(node.other || 0);
      }, 0);
      const allocate = (votes, component, districtId) => {
        const componentDenom = componentTotal(component);
        const node = countyDistricts[districtId] || {};
        const numerator = componentDenom > 0
          ? Number(node[component] || 0)
          : (Number(node.dem || 0) + Number(node.rep || 0) + Number(node.other || 0));
        const denominator = componentDenom > 0 ? componentDenom : fallbackTotal;
        return denominator > 0 ? Number(votes || 0) * (numerator / denominator) : 0;
      };
      districtIds.forEach(districtId => {
        addDistrictVotes(
          districtId,
          allocate(row.dem, 'dem', districtId),
          allocate(row.rep, 'rep', districtId),
          allocate(row.other, 'other', districtId)
        );
      });
      allocatedUnmatchedVotes += row.dem + row.rep + row.other;
    });

    Object.values(results).forEach(row => {
      const signed = row.total_votes > 0
        ? ((row.rep_votes - row.dem_votes) / row.total_votes) * 100
        : 0;
      row.margin = row.rep_votes - row.dem_votes;
      row.margin_pct = signed;
      row.winner = signed > 0 ? 'REP' : (signed < 0 ? 'DEM' : 'TIE');
    });

    if (!Object.keys(results).length) return null;
    return {
      results,
      diagnostics: {
        matchedPrecinctRows: matchedRows,
        allocatedNongeographicRows: unmatchedRows.length,
        referenceFallbackCounties: Array.from(referenceFallbackCounties).sort(),
        totalPrecinctRows: modeledRows.length,
        matchCoveragePct: sourceVotes > 0
          ? ((matchedVotes + allocatedUnmatchedVotes) / sourceVotes) * 100
          : 0
      }
    };
  }

  return {
    stableStringify,
    getDefinitionSignature,
    getCountyBehaviorLabel,
    inferConfidence,
    computeStatewideMarginFromDistrictResults,
    buildContestRow,
    buildDistrictResultRow,
    aggregatePrecinctRowsToDistricts
  };
}));
