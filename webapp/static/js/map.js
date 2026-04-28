// Leaflet map with category-colored pins. Re-renders on filterchange,
// syncs with list/detail selection via the parcelselect CustomEvent.

(function () {
  const CATEGORY_COLORS = {
    top: '#238636',
    consolidated: '#a855f7',
    outreach: '#58a6ff',
    other: '#f85149',
  };

  const BASEMAPS = {
    dark: {
      url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
      attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
      maxZoom: 19,
    },
    satellite: {
      url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      attribution: 'Tiles &copy; Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community',
      maxZoom: 19,
    },
  };

  let map = null;
  let markerLayer = null;
  let basemapLayer = null;
  let markersByPin = {};
  let selectionRing = null;
  let selectedPin = null;
  let reqId = 0;
  const layerEnabled = {
    top: true, consolidated: true, outreach: true, other: true,
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
    if (e && e.detail && e.detail.pin != null) {
      highlightSelection(e.detail.pin);
    }
  });

  function initMap() {
    map = L.map('map', { zoomControl: false }).setView([41.9395, -87.6535], 14);
    setBasemap('dark');
    L.control.zoom({ position: 'bottomright' }).addTo(map);
    markerLayer = L.layerGroup().addTo(map);

    document.querySelectorAll('.layer-toggle input[data-layer]').forEach(cb => {
      cb.addEventListener('change', (e) => {
        const layer = e.target.dataset.layer;
        if (layer in layerEnabled) {
          layerEnabled[layer] = e.target.checked;
          applyLayerVisibility();
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
    const cfg = BASEMAPS[name] || BASEMAPS.dark;
    if (basemapLayer) map.removeLayer(basemapLayer);
    basemapLayer = L.tileLayer(cfg.url, {
      attribution: cfg.attribution,
      maxZoom: cfg.maxZoom,
    }).addTo(map);
    basemapLayer.bringToBack();
  }

  async function loadMap() {
    const myId = ++reqId;
    const qs = window.filterStateToQuery ? window.filterStateToQuery() : '';
    const sortQs = mapSortBy ? `&sort=${encodeURIComponent(mapSortBy)}&dir=${mapSortDir}` : '';
    let geo;
    try {
      const r = await fetch(`/api/map-data?${qs}${sortQs}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      geo = await r.json();
    } catch (err) {
      // Bail on error: keep existing markers in place. List module
      // already surfaces the user-facing error message; don't double up.
      console.error('map: failed to load /api/map-data', err);
      return;
    }

    // Stale-fetch guard: a newer request has already started.
    if (myId !== reqId) return;
    if (!geo || !Array.isArray(geo.features)) return;

    markerLayer.clearLayers();
    markersByPin = {};

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
        (props.score != null ? ` (${Math.round(props.score)})` : '');
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

    applyLayerVisibility();

    // Re-apply selection ring if the previously selected pin survived
    // the filter change.
    if (selectedPin != null && markersByPin[selectedPin]) {
      drawSelectionRing(markersByPin[selectedPin].getLatLng(), false);
    } else if (selectionRing) {
      map.removeLayer(selectionRing);
      selectionRing = null;
    }
  }

  function applyLayerVisibility() {
    markerLayer.eachLayer(m => {
      const cat = m._category || 'other';
      const visible = !!layerEnabled[cat];
      m.setStyle({ opacity: visible ? 1 : 0, fillOpacity: visible ? 0.8 : 0 });
    });
    syncSelectionRingVisibility();
  }

  function syncSelectionRingVisibility() {
    if (!selectionRing || selectedPin == null) return;
    const marker = markersByPin[selectedPin];
    if (!marker) return;
    const cat = marker._category || 'other';
    const visible = !!layerEnabled[cat];
    selectionRing.setStyle({
      opacity: visible ? 1 : 0,
      fillOpacity: 0,
    });
  }

  function highlightSelection(pin) {
    selectedPin = pin;
    const marker = markersByPin[pin];
    if (!marker) {
      // Marker may not be loaded yet (e.g. selection arrived from list
      // before the map fetch resolved). Clear any stale ring.
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
    syncSelectionRingVisibility();
  }
})();
