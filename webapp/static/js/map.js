// Leaflet map with category-colored pins. Re-renders on filterchange,
// syncs with list/detail selection via the parcelselect CustomEvent.

(function () {
  const CATEGORY_COLORS = {
    top: '#238636',
    consolidated: '#a855f7',
    outreach: '#58a6ff',
    group: '#a855f7',                 // same hue as consolidated members
    other: '#f85149',
  };

  // Each basemap is a list of tile layers stacked back-to-front. With an
  // Esri API key (window.ESRI_API_KEY, injected by the index template from
  // the ESRI_API_KEY env var), the satellite layer uses Esri's API-keyed
  // ibasemaps endpoint — no anonymous-quota wall. Without a key it falls
  // back to the anonymous server.arcgisonline.com URL, which works for
  // local hobby use but hits "Account Limit Exceeded" on deployed traffic.
  // Stamen Toner labels (added in setBasemap when satellite is selected)
  // overlay street/place names, so no separate reference layer is needed.
  const esriKey = window.ESRI_API_KEY || '';
  const satelliteUrl = esriKey
    ? `https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}?token=${esriKey}`
    : 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
  const BASEMAPS = {
    dark: [
      {
        url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
        attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
        maxZoom: 19,
      },
    ],
    satellite: [
      {
        url: satelliteUrl,
        attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics',
        maxZoom: 19,
      },
    ],
  };

  // Optional overlay layers that the user toggles in the Layers panel.
  // Loaded lazily on first toggle-on so we don't hammer external services
  // when not requested.
  let osmLabelsLayer = null;     // OSM tiles for building/unit numbers
  let parcelLayer = null;        // Cook County parcel polygons (esri-leaflet)

  let map = null;
  let markerLayer = null;
  let groupLayer = null;          // separate layer group for consolidation-group pins
  let basemapLayers = [];         // current basemap's tile-layer stack
  let markersByPin = {};
  let markersByGroupId = {};
  let selectionRing = null;
  let selectedPin = null;
  let selectedGroupId = null;
  let reqId = 0;
  // 'group' and 'parcel_outlines' don't have server-side category filters
  // — they're map-only visual layers, so we still toggle them client-side.
  // The four parcel-category toggles (top/consolidated/outreach/other) are
  // wired in filters.js to dispatch filterchange and re-fetch from the
  // server. We don't double-hide them client-side any more.
  const visualOnlyLayerEnabled = {
    group: true,
    parcel_outlines: false,
  };

  let mapSortBy = '';
  let mapSortDir = 'desc';

  initMap();
  window.addEventListener('filterchange', loadMap);
  window.addEventListener('sortchange', (e) => {
    if (e && e.detail) {
      mapSortBy = e.detail.sort || '';
      mapSortDir = e.detail.dir || 'desc';
    }
    loadMap();
  });
  window.addEventListener('parcelselect', (e) => {
    if (!e || !e.detail) return;
    if (e.detail.groupId != null) {
      highlightGroupSelection(e.detail.groupId);
    } else if (e.detail.pin != null) {
      highlightSelection(e.detail.pin);
    }
  });

  function initMap() {
    // preferCanvas: tells Leaflet to render circleMarkers on a single canvas
    // instead of as individual SVG nodes. With MAP_MAX_PINS bumped to 80k,
    // SVG would create 80k DOM nodes — canvas keeps the page responsive.
    map = L.map('map', { zoomControl: false, preferCanvas: true })
      .setView([41.9395, -87.6535], 14);
    setBasemap('dark');
    L.control.zoom({ position: 'bottomright' }).addTo(map);
    markerLayer = L.layerGroup().addTo(map);
    groupLayer = L.layerGroup().addTo(map);

    document.querySelectorAll('.layer-toggle input[data-layer]').forEach(cb => {
      cb.addEventListener('change', (e) => {
        const layer = e.target.dataset.layer;
        // Only handle the visual-only layers here (group ring + parcel outlines).
        // The four category toggles (top/consolidated/outreach/other) are
        // wired in filters.js → they dispatch filterchange so both list and
        // map re-fetch, and the server filters by category. Doing both client
        // and server hiding caused 'All others' to vanish on toggle-off-then-on.
        if (!(layer in visualOnlyLayerEnabled)) return;
        visualOnlyLayerEnabled[layer] = e.target.checked;
        if (layer === 'parcel_outlines') {
          const l = ensureParcelLayer();
          if (l) e.target.checked ? l.addTo(map) : map.removeLayer(l);
        } else if (layer === 'group') {
          // Show/hide the consolidation-group ring markers without re-fetching
          // — they're a separate layer that doesn't filter the parcel list.
          applyVisualOnlyVisibility();
        }
      });
    });

    document.querySelectorAll('input[name="basemap"]').forEach(r => {
      r.addEventListener('change', (e) => {
        if (e.target.checked) setBasemap(e.target.value);
      });
    });
  }

  function setBasemap(name) {
    const stack = BASEMAPS[name] || BASEMAPS.dark;
    basemapLayers.forEach(l => map.removeLayer(l));
    basemapLayers = stack.map(cfg => {
      const opts = { attribution: cfg.attribution, maxZoom: cfg.maxZoom };
      if (cfg.opacity != null) opts.opacity = cfg.opacity;
      const layer = L.tileLayer(cfg.url, opts).addTo(map);
      // Surface tile load failures (expired/invalid API key, quota exhausted,
      // CORS blocked) instead of silently rendering grey. The error event
      // includes the tile coords + the underlying <img> so the URL is
      // recoverable from devtools network panel.
      layer.on('tileerror', (e) => {
        console.warn('basemap tile failed', name, e.coords, e.tile && e.tile.src);
      });
      return layer;
    });
    basemapLayers.forEach(l => l.bringToBack());
    // Auto-toggle the OSM/Stadia address-label overlay: dark basemap (CARTO)
    // already labels streets and building numbers, so the overlay would
    // double-render text. Satellite has no labels in the imagery itself,
    // so we layer Stadia toner labels on top.
    const addressLabels = ensureOsmLabelsLayer();
    if (addressLabels) {
      const want = (name === 'satellite');
      const isOnMap = map.hasLayer(addressLabels);
      if (want && !isOnMap) addressLabels.addTo(map);
      else if (!want && isOnMap) map.removeLayer(addressLabels);
    }
  }

  function ensureOsmLabelsLayer() {
    if (osmLabelsLayer) return osmLabelsLayer;
    // Stamen Toner Labels is a transparent label-only OSM tileset that
    // shows building/street numbers at high zoom — ideal for overlaying
    // on satellite imagery. Hosted free by Stadia Maps with attribution.
    osmLabelsLayer = L.tileLayer(
      'https://tiles.stadiamaps.com/tiles/stamen_toner_labels/{z}/{x}/{y}{r}.png',
      {
        attribution: '&copy; Stadia Maps &copy; Stamen Design &copy; OpenStreetMap',
        maxZoom: 19,
        opacity: 0.95,
      }
    );
    return osmLabelsLayer;
  }

  function ensureParcelLayer() {
    if (parcelLayer) return parcelLayer;
    // Cook County's public ArcGIS feature server for 2025 parcels.
    // esri-leaflet loads features dynamically per viewport so we don't
    // need to pull all 1.8M county-wide parcels at once.
    if (typeof L.esri === 'undefined' || !L.esri.featureLayer) {
      console.warn('esri-leaflet not loaded — parcel outlines unavailable');
      return null;
    }
    parcelLayer = L.esri.featureLayer({
      url: 'https://gis.cookcountyil.gov/traditional/rest/services/parcelHistorical/MapServer/2025',
      // Only fetch features starting at zoom 16 (~1 city block) — at lower
      // zooms the feature count would be huge and the outlines would be
      // visual noise anyway.
      minZoom: 16,
      style: () => ({
        color: '#7dd3fc',     // light cyan, visible on both dark and satellite
        weight: 1,
        fillOpacity: 0,
        opacity: 0.7,
      }),
      // Don't render labels or popups — outlines only, the parcel info
      // comes from our own DB via the existing detail panel.
      interactive: false,
    });
    return parcelLayer;
  }

  async function loadMap() {
    const myId = ++reqId;
    const qs = window.filterStateToQuery ? window.filterStateToQuery() : '';
    const sortQs = mapSortBy ? `&sort=${encodeURIComponent(mapSortBy)}&dir=${mapSortDir}` : '';
    let geo, groups;
    try {
      const [r, rg] = await Promise.all([
        fetch(`/api/map-data?${qs}${sortQs}`),
        // Pass the same filter query so groups whose members don't match the
        // active filters drop off the map alongside the parcel pins.
        fetch(`/api/consolidation-groups?${qs}`),
      ]);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      geo = await r.json();
      // Group fetch is best-effort — if it fails, we still render parcels.
      groups = rg.ok ? await rg.json() : { groups: [] };
    } catch (err) {
      console.error('map: failed to load /api/map-data', err);
      return;
    }

    // Stale-fetch guard: a newer request has already started.
    if (myId !== reqId) return;
    if (!geo || !Array.isArray(geo.features)) return;

    markerLayer.clearLayers();
    groupLayer.clearLayers();
    markersByPin = {};
    markersByGroupId = {};

    // Render group markers first so individual-parcel pins draw on top
    // when they overlap (groups are reference markers; per-parcel pins
    // are the click target for the underlying data).
    (groups.groups || []).forEach(g => {
      if (g.centroid_lat == null || g.centroid_lng == null) return;
      // Distinct visual: ring (transparent fill, thick purple border) at
      // double the radius of regular pins so it reads as "this is the
      // group container" not a regular pin.
      const m = L.circleMarker([g.centroid_lat, g.centroid_lng], {
        radius: 14,
        color: CATEGORY_COLORS.group,
        fillColor: CATEGORY_COLORS.group,
        fillOpacity: 0.15,
        weight: 3,
      });
      m._category = 'group';
      m._groupId = g.group_id;
      m.bindTooltip(
        escapeHtml(`Group · ${g.parcel_count} parcels · ${g.owner_name || ''}`),
        { direction: 'top', offset: [0, -16] }
      );
      m.on('click', () => {
        if (selectedGroupId === g.group_id) return;
        window.dispatchEvent(new CustomEvent('parcelselect', {
          detail: { groupId: g.group_id },
        }));
      });
      markersByGroupId[g.group_id] = m;
      groupLayer.addLayer(m);
    });

    geo.features.forEach(f => {
      const coords = f && f.geometry && f.geometry.coordinates;
      if (!coords || coords.length < 2) return;
      const [lng, lat] = coords;
      if (typeof lat !== 'number' || typeof lng !== 'number') return;

      const props = f.properties || {};
      const cat = CATEGORY_COLORS[props.category] ? props.category : 'other';
      const color = CATEGORY_COLORS[cat];

      const marker = L.circleMarker([lat, lng], {
        radius: 7,
        color,
        fillColor: color,
        fillOpacity: 0.8,
        weight: 2,
      });
      marker.feature = f;
      marker._category = cat;

      const labelText =
        (props.address ? String(props.address) : (props.pin ? String(props.pin) : '')) +
        (props.score != null ? ` · score ${props.score.toFixed(2)} / 100` : '');
      // bindTooltip with a string is treated as content; Leaflet renders
      // it as text — but to be defensive, escape and pass via {} options.
      marker.bindTooltip(escapeHtml(labelText), {
        direction: 'top',
        offset: [0, -10],
      });

      marker.on('click', () => {
        if (props.pin == null) return;
        // Avoid re-dispatching for the currently-selected pin to short-
        // circuit any potential feedback loop with list/detail listeners.
        if (selectedPin === props.pin) return;
        window.dispatchEvent(new CustomEvent('parcelselect', {
          detail: { pin: props.pin },
        }));
      });

      if (props.pin != null) markersByPin[props.pin] = marker;
      markerLayer.addLayer(marker);
    });

    applyVisualOnlyVisibility();

    // Re-apply selection ring if the previously selected pin survived
    // the filter change.
    if (selectedPin != null && markersByPin[selectedPin]) {
      drawSelectionRing(markersByPin[selectedPin].getLatLng(), false);
    } else if (selectionRing) {
      map.removeLayer(selectionRing);
      selectionRing = null;
    }
  }

  function applyVisualOnlyVisibility() {
    // Only the group-ring layer needs client-side visibility toggling.
    // Parcel pins are filtered by the server based on visibleCategories
    // — when a category is unchecked, the markers don't get rendered at
    // all, so there's nothing to hide here.
    groupLayer.eachLayer(m => {
      const visible = !!visualOnlyLayerEnabled.group;
      m.setStyle({ opacity: visible ? 1 : 0, fillOpacity: visible ? 0.15 : 0 });
    });
  }

  function highlightSelection(pin) {
    selectedPin = pin;
    selectedGroupId = null;
    const marker = markersByPin[pin];
    if (!marker) {
      if (selectionRing) {
        map.removeLayer(selectionRing);
        selectionRing = null;
      }
      return;
    }
    drawSelectionRing(marker.getLatLng(), true);
  }

  function highlightGroupSelection(groupId) {
    selectedGroupId = groupId;
    selectedPin = null;
    const marker = markersByGroupId[groupId];
    if (!marker) {
      if (selectionRing) {
        map.removeLayer(selectionRing);
        selectionRing = null;
      }
      return;
    }
    drawSelectionRing(marker.getLatLng(), true);
  }

  function drawSelectionRing(latlng, pan) {
    if (selectionRing) {
      map.removeLayer(selectionRing);
      selectionRing = null;
    }
    selectionRing = L.circleMarker(latlng, {
      radius: 14,
      color: '#f0f6fc',
      fillColor: 'transparent',
      fillOpacity: 0,
      weight: 2,
      dashArray: '4 4',
      interactive: false,
    }).addTo(map);
    if (pan) map.panTo(latlng);
  }
})();
