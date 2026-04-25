// Right panel: renders parcel detail sections from /api/parcels/<pin>.

(function () {
  let reqId = 0;

  window.addEventListener('parcelselect', async (e) => {
    const pin = e && e.detail ? e.detail.pin : null;

    if (!pin) {
      renderPlaceholder();
      return;
    }

    const myId = ++reqId;
    let resp, data;
    try {
      resp = await fetch(`/api/parcels/${encodeURIComponent(pin)}`);
    } catch (err) {
      if (myId !== reqId) return; // stale
      renderError("Couldn't load parcel — try again.");
      return;
    }

    if (myId !== reqId) return; // stale

    if (resp.status === 404) {
      renderError('Parcel not found.');
      return;
    }
    if (!resp.ok) {
      renderError("Couldn't load parcel — try again.");
      return;
    }

    try {
      data = await resp.json();
    } catch (err) {
      if (myId !== reqId) return;
      renderError("Couldn't load parcel — try again.");
      return;
    }

    if (myId !== reqId) return; // stale

    renderDetail(data);
  });

  function renderPlaceholder() {
    const panel = document.getElementById('detail-panel');
    panel.innerHTML = '';
    const el = document.createElement('div');
    el.className = 'detail-section';
    el.style.color = '#8b949e';
    el.style.fontSize = '12px';
    el.textContent = 'Select a parcel to see details.';
    panel.appendChild(el);
  }

  function renderError(msg) {
    const panel = document.getElementById('detail-panel');
    panel.innerHTML = '';
    const el = document.createElement('div');
    el.className = 'detail-section';
    el.style.color = '#8b949e';
    el.style.fontSize = '12px';
    el.textContent = msg;
    panel.appendChild(el);
  }

  function renderDetail(p) {
    const panel = document.getElementById('detail-panel');
    panel.innerHTML = '';
    panel.appendChild(sectionPropertyFacts(p));
    panel.appendChild(sectionOwner(p));
    panel.appendChild(sectionZoning(p));
    panel.appendChild(sectionScoreBreakdown(p));
    panel.appendChild(sectionFinancials(p));
    if (window.FEATURE_OUTREACH) {
      panel.appendChild(sectionOutreachStub());
    }
  }

  function sectionPropertyFacts(p) {
    const trailing = p.google_maps_url
      ? `<div style="margin-top:10px;"><a href="${escapeHtml(p.google_maps_url)}" target="_blank" rel="noopener">Open in Google Maps →</a></div>`
      : '';
    return renderSection('Property Facts', [
      ['PIN', p.pin],
      ['Address', p.address],
      ['Lot Size', p.lot_size_sf ? `${Math.round(p.lot_size_sf).toLocaleString()} SF` : null],
      ['Building SF', p.building_sf ? `${Math.round(p.building_sf).toLocaleString()} SF` : null],
      ['Year Built', p.year_built],
      ['Ward', p.ward_num],
      ['Class', p.property_class],
      ['Building Type', p.building_classification],
      ['Condition', p.condition],
      ['Open Violations', p.open_violations_count],
    ], trailing);
  }

  function sectionOwner(p) {
    const contact = (p.contacts && p.contacts[0]) || {};
    return renderSection('Owner', [
      ['Owner', p.owner_name],
      ['Type', ownerTypeLabel(p)],
      ['Mailing Address', p.mail_address],
      ['Hold Duration', p.hold_duration_years ? `${Math.round(p.hold_duration_years)} years` : null],
      ['Registered Agent', contact.role === 'registered_agent' ? contact.name : null],
      ['Phone', contact.phone],
      ['Email', contact.email],
      ['Listing Status', p.listing_status || 'Not listed'],
    ]);
  }

  function sectionZoning(p) {
    let allowsMf = null;
    if (p.allows_multifamily_by_right === 1) allowsMf = 'By right';
    else if (p.allows_multifamily_by_right === 0) allowsMf = 'Requires rezoning';

    return renderSection('Zoning Context', [
      ['Zone Class', p.zone_class],
      ['Allows Multifamily', allowsMf],
      ['Max FAR', p.max_far],
      ['Built FAR', p.built_far],
      ['FAR Gap', p.far_gap ? `${p.far_gap.toFixed(1)}x underbuilt` : null],
      ['TIF District', p.tif_district],
      ['Nearest CTA', p.cta_nearest_station],
      ['CTA Distance', p.cta_distance_ft ? `${Math.round(p.cta_distance_ft)} ft` : null],
    ]);
  }

  function sectionScoreBreakdown(p) {
    // STUB — scoring not yet implemented (see plan Risks section)
    const el = document.createElement('div');
    el.className = 'detail-section';
    el.innerHTML = `
      <h3>Score Breakdown</h3>
      <div style="font-size:12px; color:#8b949e; padding:8px 0;">
        Scoring not yet available — run <code style="color:#c9d1d9;">pipeline.score</code> to populate.
      </div>
    `;
    return el;
  }

  function sectionFinancials(p) {
    return renderSection('Financials', [
      ['Assessed Total', p.assessed_total ? `$${Math.round(p.assessed_total).toLocaleString()}` : null],
      ['Est. Annual Tax', p.estimated_annual_tax ? `$${Math.round(p.estimated_annual_tax).toLocaleString()}` : null],
      ['Tax Change (1yr)', p.tax_increase_pct_1yr != null ? `${p.tax_increase_pct_1yr.toFixed(1)}%` : null],
      ['Tax Change (5yr)', p.tax_increase_pct_5yr != null ? `${p.tax_increase_pct_5yr.toFixed(1)}%` : null],
      ['Last Sale Price', p.last_sale_price ? `$${Math.round(p.last_sale_price).toLocaleString()}` : null],
      ['Last Sale Date', p.last_sale_date],
    ]);
  }

  function sectionOutreachStub() {
    const el = document.createElement('div');
    el.className = 'detail-section';
    el.innerHTML = `
      <h3>Outreach</h3>
      <div style="font-size:12px; color:#8b949e;">Outreach UI is planned for a later implementation phase.</div>
    `;
    return el;
  }

  function ownerTypeLabel(p) {
    const parts = [];
    if (p.is_llc) parts.push('LLC');
    if (p.is_absentee) parts.push('Absentee');
    return parts.length ? parts.join(' · ') : null;
  }

  function renderSection(title, pairs, trailingHtml = '') {
    const el = document.createElement('div');
    el.className = 'detail-section';
    const rows = pairs.map(([label, value]) => `
      <div class="detail-item">
        <div class="label">${escapeHtml(label)}</div>
        <div class="value">${value == null || value === '' ? '—' : escapeHtml(String(value))}</div>
      </div>
    `).join('');
    el.innerHTML = `
      <h3>${escapeHtml(title)}</h3>
      <div class="detail-grid">${rows}</div>
      ${trailingHtml}
    `;
    return el;
  }

  function escapeHtml(s) {
    return (s == null ? '' : String(s)).replace(/[&<>"']/g, c => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }
})();
