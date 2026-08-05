(function initializeAtlasCore(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AtlasCore = Object.freeze(api);
}(typeof globalThis !== 'undefined' ? globalThis : this, function createAtlasCore() {
  const PRECINCT_ALIAS_COMMON_WORDS = Object.freeze([
    'PRECINCT',
    'PCT',
    'WARD',
    'DISTRICT',
    'TOWNSHIP',
    'BOX',
    'VOTING',
    'LOCATION'
  ]);

  function normalizeCountyToken(name) {
    return (name || '')
      .toString()
      .replace(/[^a-z0-9 .\-]/gi, '')
      .replace(/\s+/g, ' ')
      .trim()
      .toUpperCase();
  }

  function normalizePrecinctAliasToken(value) {
    let token = (value || '').toString().trim().toUpperCase();
    if (!token) return '';
    PRECINCT_ALIAS_COMMON_WORDS.forEach(word => {
      token = token.replace(new RegExp(word, 'g'), ' ');
    });
    token = token.replace(/[-_.]/g, ' ');
    token = token.replace(/\s+/g, ' ').trim();
    return token;
  }

  function compactPrecinctAliasToken(value) {
    return (value || '').toString().trim().toUpperCase().replace(/[^A-Z0-9]/g, '');
  }

  function extractPrecinctAliasCandidates(rawPrecinctValue) {
    const aliases = new Set();
    const precinct = (rawPrecinctValue || '').toString().trim().toUpperCase();
    if (!precinct) return aliases;
    const normalized = normalizePrecinctAliasToken(precinct);

    aliases.add(precinct);
    const compact = compactPrecinctAliasToken(precinct);
    if (compact) aliases.add(compact);
    if (normalized) {
      aliases.add(normalized);
      const normalizedCompact = compactPrecinctAliasToken(normalized);
      if (normalizedCompact) aliases.add(normalizedCompact);
    }

    const noHash = precinct.replace(/#\s*\d+\b/g, ' ').replace(/\s+/g, ' ').trim();
    if (noHash && noHash !== precinct) {
      aliases.add(noHash);
      const noHashCompact = compactPrecinctAliasToken(noHash);
      if (noHashCompact) aliases.add(noHashCompact);
      const noHashNormalized = normalizePrecinctAliasToken(noHash);
      if (noHashNormalized) {
        aliases.add(noHashNormalized);
        const noHashNormalizedCompact = compactPrecinctAliasToken(noHashNormalized);
        if (noHashNormalizedCompact) aliases.add(noHashNormalizedCompact);
      }
    }

    if (precinct.includes('/')) {
      precinct.split('/').forEach(part => {
        const value = (part || '').toString().trim().toUpperCase();
        if (!value) return;
        aliases.add(value);
        const valueCompact = compactPrecinctAliasToken(value);
        if (valueCompact) aliases.add(valueCompact);
      });
    }

    if (precinct.includes('_')) {
      const [left, ...restParts] = precinct.split('_');
      const right = restParts.join('_').trim();
      if (left && left.trim()) {
        aliases.add(left.trim().toUpperCase());
        const leftCompact = compactPrecinctAliasToken(left);
        if (leftCompact) aliases.add(leftCompact);
      }
      if (right) {
        aliases.add(right.toUpperCase());
        const rightCompact = compactPrecinctAliasToken(right);
        if (rightCompact) aliases.add(rightCompact);
      }
    }

    const groupSuffixMatch = precinct.match(/^(.+?)(?:[-\s]+G\d+[A-Z]?)$/);
    if (groupSuffixMatch && /[A-Z]/.test(groupSuffixMatch[1] || '')) {
      const stripped = (groupSuffixMatch[1] || '').toString().trim().toUpperCase();
      if (stripped) {
        aliases.add(stripped);
        const strippedNormalized = normalizePrecinctAliasToken(stripped);
        if (strippedNormalized) aliases.add(strippedNormalized);
        const strippedCompact = compactPrecinctAliasToken(stripped);
        if (strippedCompact) aliases.add(strippedCompact);
        if (strippedNormalized) {
          const strippedNormalizedCompact = compactPrecinctAliasToken(strippedNormalized);
          if (strippedNormalizedCompact) aliases.add(strippedNormalizedCompact);
        }
      }
    }

    const parts = (normalized || '').split(' ').filter(Boolean);
    if (parts.length) {
      const first = parts[0];
      if (/[0-9]/.test(first)) {
        aliases.add(first);
        const firstCompact = compactPrecinctAliasToken(first);
        if (firstCompact) aliases.add(firstCompact);
        const rest = parts.slice(1).join(' ').trim().toUpperCase();
        if (rest) {
          aliases.add(rest);
          const restCompact = compactPrecinctAliasToken(rest);
          if (restCompact) aliases.add(restCompact);
        }
      }
    }

    if (parts.length >= 2 && parts.length % 2 === 0) {
      const midpoint = parts.length / 2;
      const left = parts.slice(0, midpoint);
      const right = parts.slice(midpoint);
      if (left.join('\u0000') === right.join('\u0000')) {
        const collapsed = left.join(' ').trim().toUpperCase();
        if (collapsed) {
          aliases.add(collapsed);
          aliases.add(collapsed.replace(/\s+/g, '-'));
          const collapsedCompact = compactPrecinctAliasToken(collapsed);
          if (collapsedCompact) aliases.add(collapsedCompact);
        }
      }
    }

    const dotVariant = precinct.replace(/-/g, '.');
    if (dotVariant.includes('.')) {
      const [aRaw, bRaw] = dotVariant.split('.', 2);
      if (/^\d+$/.test(aRaw || '') && /^\d+$/.test(bRaw || '')) {
        const a = Number(aRaw);
        const b = Number(bRaw);
        const pad2 = (value) => String(value).padStart(2, '0');
        aliases.add(`${a}.${b}`);
        aliases.add(`${pad2(a)}.${b}`);
        aliases.add(`${pad2(a)}${b}`);
        aliases.add(`${pad2(a)}${pad2(b)}`);
      }
    }

    if (/^\d+$/.test(precinct)) {
      aliases.add(String(Number(precinct)));
      aliases.add(precinct.padStart(4, '0'));
    }

    return aliases;
  }

  function addNumericBaseVariantsFromAlphaSuffix(token, addVariantFn) {
    if (typeof addVariantFn !== 'function') return;
    const value = (token || '').toString().trim().toUpperCase();
    const match = value.match(/^0*([0-9]{1,4})([A-Z]{1,2})$/);
    if (!match) return;
    const number = parseInt(match[1], 10);
    if (isNaN(number)) return;
    addVariantFn(String(number));
    addVariantFn(String(number).padStart(2, '0'));
    addVariantFn(String(number).padStart(3, '0'));
    addVariantFn(String(number).padStart(4, '0'));
    addVariantFn(`${String(number)}-1`);
    addVariantFn(`${String(number).padStart(2, '0')}-1`);
    addVariantFn(`${String(number).padStart(3, '0')}-1`);
    addVariantFn(`${String(number).padStart(4, '0')}-1`);
  }

  function addEmbeddedPrecinctCodeVariants(rawToken, addVariantFn) {
    const token = (rawToken || '').toString().trim().toUpperCase();
    if (!token || typeof addVariantFn !== 'function') return;
    const addSafe = (value) => {
      const normalized = (value || '').toString().trim().toUpperCase();
      if (normalized) addVariantFn(normalized);
    };

    const codeLike = token.match(/\b(\d{2}-\d{2}[A-Z]{0,2})\b/);
    if (codeLike) {
      const code = codeLike[1];
      addSafe(code);
      const base = code.match(/^(\d{2}-\d{2})[A-Z]{1,2}$/);
      if (base) addSafe(base[1]);
    }

    const prLike = token.match(/\bPR\s*0*([0-9]{1,3})([A-Z]{0,2})\b/);
    if (prLike) {
      const number = parseInt(prLike[1], 10);
      const suffix = (prLike[2] || '').toUpperCase();
      if (!isNaN(number)) {
        addSafe(`PR${String(number)}${suffix}`);
        addSafe(`PR${String(number).padStart(2, '0')}${suffix}`);
        addSafe(`${String(number)}${suffix}`);
        addSafe(`${String(number).padStart(2, '0')}${suffix}`);
        addSafe(`${String(number).padStart(3, '0')}${suffix}`);
        addSafe(`${String(number).padStart(4, '0')}${suffix}`);
      }
    }
  }

  function addClosestHyphenCodeVariants(rawToken, countyCodes, addVariantFn) {
    if (!countyCodes || typeof addVariantFn !== 'function') return;
    const token = (rawToken || '').toString().trim().toUpperCase();
    const match = token.match(/\b(\d{2})-(\d{2})([A-Z]{0,2})\b/);
    if (!match) return;
    const exactCode = `${match[1]}-${match[2]}${match[3] || ''}`;
    if (countyCodes.has(exactCode)) return;

    const prefix = match[1];
    const target = parseInt(match[2], 10);
    if (isNaN(target)) return;

    const candidates = [];
    countyCodes.forEach(code => {
      const value = (code || '').toString().trim().toUpperCase();
      const candidateMatch = value.match(/^(\d{2})-(\d{2})([A-Z]{0,2})$/);
      if (!candidateMatch || candidateMatch[1] !== prefix) return;
      const number = parseInt(candidateMatch[2], 10);
      if (isNaN(number)) return;
      candidates.push({ code: value, number, difference: Math.abs(number - target) });
    });
    if (!candidates.length) return;

    const sameNumber = candidates.filter(candidate => candidate.number === target);
    if (sameNumber.length && sameNumber.length <= 3) {
      sameNumber.forEach(candidate => addVariantFn(candidate.code));
      return;
    }

    candidates.sort((a, b) => (a.difference - b.difference) || a.code.localeCompare(b.code));
    const bestDifference = candidates[0].difference;
    candidates
      .filter(candidate => candidate.difference === bestDifference)
      .slice(0, 2)
      .forEach(candidate => addVariantFn(candidate.code));
  }

  function addCompactCodeVariants(rawToken, countyCodes, addVariantFn) {
    if (!countyCodes || typeof addVariantFn !== 'function') return;
    const tokenCompact = compactPrecinctAliasToken(rawToken);
    if (!tokenCompact) return;
    const hits = [];
    countyCodes.forEach(code => {
      const value = (code || '').toString().trim().toUpperCase();
      if (value && compactPrecinctAliasToken(value) === tokenCompact) hits.push(value);
    });
    if (hits.length === 1) addVariantFn(hits[0]);
  }

  function addPrefixStrippedNumericVariants(rawToken, countyCodes, addVariantFn) {
    if (!countyCodes || typeof addVariantFn !== 'function') return;
    const token = (rawToken || '').toString().trim().toUpperCase();
    if (!token) return;
    const match = token.match(/^[A-Z]{1,3}0*([0-9]{1,4})([A-Z]{0,2})$/);
    if (!match) return;
    const number = parseInt(match[1], 10);
    if (isNaN(number)) return;
    const suffix = (match[2] || '').toUpperCase();
    const candidates = Array.from(new Set([
      `${number}${suffix}`,
      `${String(number).padStart(2, '0')}${suffix}`,
      `${String(number).padStart(3, '0')}${suffix}`,
      `${String(number).padStart(4, '0')}${suffix}`
    ])).filter(Boolean);
    const hits = candidates.filter(code => countyCodes.has(code));
    if (hits.length === 1) addVariantFn(hits[0]);
  }

  function normalizeRowKey(value) {
    return (value || '').toString().trim().toUpperCase().replace(/\s+/g, ' ');
  }

  function normalizeOpenElectionsPrecinctLabel(value) {
    return (value || '').toString().trim().toUpperCase().replace(/\s+/g, ' ');
  }

  function buildCountyPrecinctWeightIndex(rows, options = {}) {
    const sourceField = String(options.sourceField || 'source_precinct_id');
    const targetField = String(options.targetField || 'target_precinct_id');
    const weightField = String(options.weightField || 'share');
    const accum = new Map();

    (rows || []).forEach(row => {
      const source = normalizeRowKey(row?.[sourceField]);
      const target = normalizeRowKey(row?.[targetField]);
      const weight = Number(row?.[weightField]);
      if (!source.includes(' - ') || !target.includes(' - ') || !Number.isFinite(weight) || weight <= 0) return;
      const [sourceCounty, sourceCode] = source.split(' - ', 2);
      const [targetCounty, targetCode] = target.split(' - ', 2);
      if (!sourceCounty || !sourceCode || sourceCounty !== targetCounty || !targetCode) return;

      if (!accum.has(sourceCounty)) accum.set(sourceCounty, new Map());
      const countyMap = accum.get(sourceCounty);
      [source, sourceCode].forEach(alias => {
        if (!countyMap.has(alias)) countyMap.set(alias, new Map());
        const byCode = countyMap.get(alias);
        byCode.set(targetCode, (byCode.get(targetCode) || 0) + weight);
      });
    });

    const out = new Map();
    accum.forEach((countyMap, county) => {
      const aliases = new Map();
      countyMap.forEach((byCode, alias) => {
        const total = Array.from(byCode.values()).reduce((sum, weight) => sum + Number(weight || 0), 0);
        if (!Number.isFinite(total) || total <= 0) return;
        aliases.set(alias, Array.from(byCode.entries()).map(([code, weight]) => ({
          code,
          weight: Number(weight || 0) / total
        })));
      });
      if (aliases.size) out.set(county, aliases);
    });
    return out;
  }

  function signedMarginPctFromVotes(demVotes, repVotes, totalVotes) {
    const total = Number(totalVotes) || 0;
    if (total <= 0) return 0;
    return ((Number(repVotes) - Number(demVotes)) / total) * 100;
  }

  function rescaleVoteSetToTargetTotal(voteSet, targetTotal) {
    const desired = Math.max(0, Math.round(Number(targetTotal) || 0));
    const baseTotal = Math.max(0, Math.round(Number(voteSet?.total) || 0));
    if (!Number.isFinite(desired) || desired <= 0 || baseTotal <= 0) return voteSet;

    const scale = desired / baseTotal;
    let dem = Math.max(0, Math.round((Number(voteSet?.dem) || 0) * scale));
    let rep = Math.max(0, Math.round((Number(voteSet?.rep) || 0) * scale));
    const twoParty = dem + rep;
    if (twoParty > desired && twoParty > 0) {
      const demShare = dem / twoParty;
      dem = Math.max(0, Math.round(demShare * desired));
      rep = Math.max(0, desired - dem);
      return { dem, rep, other: 0, total: desired };
    }
    const other = Math.max(0, desired - twoParty);
    return { dem, rep, other, total: desired };
  }

  return {
    PRECINCT_ALIAS_COMMON_WORDS,
    normalizeCountyToken,
    normalizePrecinctAliasToken,
    compactPrecinctAliasToken,
    extractPrecinctAliasCandidates,
    addNumericBaseVariantsFromAlphaSuffix,
    addEmbeddedPrecinctCodeVariants,
    addClosestHyphenCodeVariants,
    addCompactCodeVariants,
    addPrefixStrippedNumericVariants,
    normalizeRowKey,
    normalizeOpenElectionsPrecinctLabel,
    buildCountyPrecinctWeightIndex,
    signedMarginPctFromVotes,
    rescaleVoteSetToTargetTotal
  };
}));
