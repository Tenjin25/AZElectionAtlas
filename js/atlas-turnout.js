(function initializeAtlasTurnout(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AtlasTurnout = Object.freeze(api);
}(typeof globalThis !== 'undefined' ? globalThis : this, function createAtlasTurnout() {
  function quantile(sortedValues, probability) {
    if (!sortedValues || !sortedValues.length) return 0;
    const index = Math.min(
      sortedValues.length - 1,
      Math.max(0, Math.floor((sortedValues.length - 1) * probability))
    );
    return Number(sortedValues[index] || 0);
  }

  function buildOpacityMeta(totals) {
    const values = (totals || [])
      .map(value => Number(value || 0))
      .filter(value => Number.isFinite(value) && value > 0)
      .sort((a, b) => a - b);
    if (!values.length) return null;
    return {
      q20: quantile(values, 0.20),
      q40: quantile(values, 0.40),
      q60: quantile(values, 0.60),
      q80: quantile(values, 0.80)
    };
  }

  function opacityFromTotal(totalVotes, meta, baseOpacity) {
    const total = Number(totalVotes || 0);
    if (!meta || !Number.isFinite(total) || total <= 0) return baseOpacity;
    if (total >= meta.q80) return Math.min(0.98, baseOpacity + 0.08);
    if (total >= meta.q60) return Math.min(0.94, baseOpacity + 0.02);
    if (total >= meta.q40) return Math.max(0.35, baseOpacity - 0.06);
    if (total >= meta.q20) return Math.max(0.30, baseOpacity - 0.12);
    return Math.max(0.24, baseOpacity - 0.18);
  }

  function buildCountyExpression(baseOpacity, totals, enabled = true) {
    const entries = Object.entries(totals || {});
    if (!enabled || !entries.length) return baseOpacity;
    const meta = buildOpacityMeta(entries.map(([, total]) => total));
    if (!meta) return baseOpacity;
    const expression = ['case'];
    entries.forEach(([countyNorm, totalVotes]) => {
      expression.push([
        '==',
        ['upcase', ['coalesce', ['get', 'NAME20'], ['get', 'CountyName'], ['get', 'COUNTYNAME'], ['get', 'county_nam'], ['get', 'NAME']]],
        countyNorm
      ]);
      expression.push(opacityFromTotal(totalVotes, meta, baseOpacity));
    });
    expression.push(Math.max(0.16, baseOpacity - 0.20));
    return expression;
  }

  function buildDistrictExpression(baseOpacity, totals, enabled = true) {
    const entries = Object.entries(totals || {});
    if (!enabled || !entries.length) return baseOpacity;
    const meta = buildOpacityMeta(entries.map(([, total]) => total));
    if (!meta) return baseOpacity;
    const expression = ['case'];
    entries.forEach(([districtId, totalVotes]) => {
      const districtNumber = Number(districtId);
      if (!Number.isFinite(districtNumber)) return;
      expression.push([
        '==',
        ['to-number', ['coalesce', ['get', 'DISTRICT'], ['get', 'CD118FP'], ['get', 'CD119FP'], ['get', 'SLDLST'], ['get', 'SLDUST'], ['get', 'district_id'], ['get', 'district']]],
        districtNumber
      ]);
      expression.push(opacityFromTotal(totalVotes, meta, baseOpacity));
    });
    expression.push(Math.max(0.18, baseOpacity - 0.20));
    return expression;
  }

  function buildPrecinctExpression(baseOpacity, totals, enabled = true) {
    const entries = totals instanceof Map ? Array.from(totals.entries()) : [];
    if (!enabled || !entries.length) return baseOpacity;
    const meta = buildOpacityMeta(entries.map(([, total]) => total));
    if (!meta) return baseOpacity;
    const expression = ['case'];
    entries.forEach(([precinctNorm, totalVotes]) => {
      expression.push(['==', ['get', 'precinct_norm'], precinctNorm]);
      expression.push(opacityFromTotal(totalVotes, meta, baseOpacity));
    });
    expression.push(Math.max(0.20, baseOpacity - 0.18));
    return expression;
  }

  return {
    buildOpacityMeta,
    opacityFromTotal,
    buildCountyExpression,
    buildDistrictExpression,
    buildPrecinctExpression
  };
}));
