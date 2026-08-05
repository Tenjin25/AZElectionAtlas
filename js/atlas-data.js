(function initializeAtlasData(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AtlasData = Object.freeze(api);
}(typeof globalThis !== 'undefined' ? globalThis : this, function createAtlasData() {
  function detectBasePath(locationLike) {
    const hostname = String(locationLike?.hostname || '');
    if (!hostname.endsWith('github.io')) return '';
    const parts = String(locationLike?.pathname || '').split('/').filter(Boolean);
    return parts.length ? `/${parts[0]}` : '';
  }

  function withBase(path, locationLike) {
    if (!path) return path;
    if (/^https?:\/\//i.test(path)) return path;
    const base = detectBasePath(locationLike);
    const cleaned = String(path).replace(/^\.\//, '').replace(/^\/+/, '');
    return base ? `${base}/${cleaned}` : cleaned;
  }

  function toAbsoluteUrl(path, href) {
    const normalized = String(path || '').trim();
    if (!normalized) return normalized;
    try {
      return new URL(normalized, href).toString();
    } catch (_) {
      return normalized;
    }
  }

  function withCacheBuster(path, token) {
    const normalized = String(path || '').trim();
    if (!normalized) return normalized;
    if (/^mapbox:\/\//i.test(normalized)) return normalized;
    if (/[?&]v=/.test(normalized)) return normalized;
    const separator = normalized.includes('?') ? '&' : '?';
    return `${normalized}${separator}v=${encodeURIComponent(token)}`;
  }

  async function fetchWithRetry(input, init = {}, options = {}) {
    const fetcher = options.fetcher || globalThis.fetch;
    if (typeof fetcher !== 'function') throw new TypeError('fetchWithRetry requires fetch');

    const configuredAttempts = Number(options.attempts);
    const configuredDelay = Number(options.retryDelayMs);
    const attempts = Math.max(1, Math.floor(Number.isFinite(configuredAttempts) ? configuredAttempts : 3));
    const retryDelayMs = Math.max(0, Number.isFinite(configuredDelay) ? configuredDelay : 250);
    const retryStatuses = new Set(options.retryStatuses || [408, 429, 500, 502, 503, 504]);

    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        const requestInit = attempt === 0
          ? init
          : { ...init, cache: options.retryCache || 'reload' };
        const response = await fetcher(input, requestInit);
        if (!retryStatuses.has(Number(response?.status)) || attempt === attempts - 1) {
          return response;
        }
      } catch (error) {
        if (attempt === attempts - 1) throw error;
      }

      if (retryDelayMs > 0) {
        await new Promise(resolve => setTimeout(resolve, retryDelayMs * (attempt + 1)));
      }
    }

    throw new Error('fetchWithRetry exhausted without a response');
  }

  async function loadCached(key, options = {}) {
    const resolvedCache = options.resolvedCache;
    const inflightCache = options.inflightCache;
    if (resolvedCache?.has(key)) return resolvedCache.get(key);
    if (inflightCache?.has(key)) return inflightCache.get(key);
    if (typeof options.load !== 'function') throw new TypeError('loadCached requires a load function');

    const promise = Promise.resolve().then(options.load).then(value => {
      const shouldCache = typeof options.shouldCache === 'function'
        ? options.shouldCache(value)
        : true;
      if (shouldCache) resolvedCache?.set(key, value);
      return value;
    });
    inflightCache?.set(key, promise);
    try {
      return await promise;
    } finally {
      inflightCache?.delete(key);
    }
  }

  function mapContestPayloadRows(payload, contestType, year) {
    const sourceRows = Array.isArray(payload?.rows)
      ? payload.rows.map(row => ({ key: '', value: row }))
      : (payload?.precinct_results && typeof payload.precinct_results === 'object')
        ? Object.entries(payload.precinct_results).map(([key, value]) => ({ key, value }))
        : [];
    return sourceRows.map(({ key, value: row }) => ({
      year: Number(year),
      county: row.county || row.county_name || key,
      [`${contestType}_dem`]: Number(row.dem_votes) || 0,
      [`${contestType}_rep`]: Number(row.rep_votes) || 0,
      [`${contestType}_other`]: Number(row.other_votes) || 0,
      [`${contestType}_total`]: Number(row.total_votes) || 0,
      [`${contestType}_dem_candidate`]: row.dem_candidate || '',
      [`${contestType}_rep_candidate`]: row.rep_candidate || '',
      [`${contestType}_margin`]: Number(row.margin) || 0,
      [`${contestType}_margin_pct`]: Number(row.margin_pct) || 0,
      [`${contestType}_winner`]: row.winner || '',
      seats: Number(row.seats) || 0,
      [`${contestType}_color`]: row.color || ''
    }));
  }

  return {
    detectBasePath,
    withBase,
    toAbsoluteUrl,
    withCacheBuster,
    fetchWithRetry,
    loadCached,
    mapContestPayloadRows
  };
}));
