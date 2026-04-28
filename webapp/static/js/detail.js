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
    if (p.consolidation_group) panel.appendChild(sectionConsolidationGroup(p));
    panel.appendChild(sectionScoreBreakdown(p));
    panel.appendChild(sectionFinancials(p));
    panel.appendChild(sectionDistress(p));
    if (p.bldg_sf_sources) panel.appendChild(sectionSFCompare(p));
    if (window.FEATURE_OUTREACH) {
      panel.appendChild(sectionOutreachStub());
    }
  }

  function sectionConsolidationGroup(p) {
    const g = p.consolidation_group || {};
    let pins = [];
    try { pins = JSON.parse(g.pins || '[]'); } catch (_) {}
    return renderSection('Consolidation Group', [
      ['Owner', g.owner_name],
      ['Parcels', pins.length || null],
      ['Combined Lot', g.combined_lot_size_sf != null
        ? `${Math.round(g.combined_lot_size_sf).toLocaleString()} SF` : null],
      ['Combined Building', g.combined_building_sf != null
        ? `${Math.round(g.combined_building_sf).toLocaleString()} SF` : null],
    ]);
  }

  function sectionPropertyFacts(p) {
    const trailing = p.google_maps_url
      ? `<div style="margin-top:10px;"><a href="${escapeHtml(p.google_maps_url)}" target="_blank" rel="noopener">Open in Google Maps →</a></div>`
      : '';

    // Building SF / condition annotated with source so 'footprint'-derived
    // values are visibly tagged (the footprint dataset is frozen at 2010-2011).
    const bldgSf = p.building_sf != null
      ? { html: `${Math.round(p.building_sf).toLocaleString()} SF${sourceTag(p.building_sf_source)}` }
      : null;
    const cond = p.condition != null
      ? { html: `${escapeHtml(p.condition)}${sourceTag(p.condition_source)}` }
      : null;

    return renderSection('Property Facts', [
      ['PIN', p.pin],
      ['Address', p.address],
      ['Lot Size', p.lot_size_sf != null ? `${Math.round(p.lot_size_sf).toLocaleString()} SF` : null],
      ['Building SF', bldgSf],
      ['Year Built', p.year_built],
      ['Units', p.unit_count],
      ['Ward', p.ward_num],
      ['Class', p.property_class],
      ['Building Type', p.building_classification],
      ['Condition', cond],
      ['Open Violations', p.open_violations_count],
    ], trailing);
  }

  function sourceTag(src) {
    if (!src) return '';
    return ` <span style="font-size:10px; color:#8b949e;">(${escapeHtml(src)})</span>`;
  }

  function sectionOwner(p) {
    const contact = (p.contacts && p.contacts[0]) || {};
    return renderSection('Owner', [
      ['Owner', p.owner_name],
      ['Type', ownerTypeLabel(p)],
      ['Mailing Address', p.mail_address],
      ['Hold Duration', p.hold_duration_years != null ? `${Math.round(p.hold_duration_years)} years` : null],
      ['Registered Agent', contact.role === 'registered_agent' ? contact.name : null],
      ['Phone', contact.phone],
      ['Email', contact.email],
    ]);
  }

  function sectionZoning(p) {
    let allowsMf = null;
    if (p.allows_multifamily_by_right === 1) allowsMf = 'By right';
    else if (p.allows_multifamily_by_right === 0) allowsMf = 'Requires rezoning';

    // Prefer the stored far_gap_delta (kept in sync when building_sf is
    // overwritten by the footprint merge). Fall back to recomputing on the
    // fly for older parcel rows that pre-date the column.
    let farGapDelta = null;
    const rawDelta = p.far_gap_delta != null
      ? p.far_gap_delta
      : (p.max_far != null && p.built_far != null ? p.max_far - p.built_far : null);
    if (rawDelta != null) {
      const sign = rawDelta > 0 ? '+' : '';
      const tail = rawDelta >= 0 ? 'FAR available' : 'FAR over by-right';
      farGapDelta = `${sign}${rawDelta.toFixed(2)} ${tail}`;
    }

    return renderSection('Zoning Context', [
      ['Zone Class', p.zone_class],
      ['Allows Multifamily', allowsMf],
      ['Max FAR', p.max_far],
      ['Built FAR', p.built_far],
      ['FAR Gap (×)', p.far_gap != null ? `${p.far_gap.toFixed(1)}x underbuilt` : null],
      ['FAR Gap (Δ)', farGapDelta],
      ['TIF District', p.tif_district],
      ['Nearest CTA', p.cta_nearest_station],
      ['CTA Distance', p.cta_distance_ft != null ? `${Math.round(p.cta_distance_ft)} ft` : null],
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
      ['Assessed Total', p.assessed_total != null ? `$${Math.round(p.assessed_total).toLocaleString()}` : null],
      ['Est. Annual Tax', p.estimated_annual_tax != null ? `$${Math.round(p.estimated_annual_tax).toLocaleString()}` : null],
      ['Tax Change (1yr)', p.tax_increase_pct_1yr != null ? `${p.tax_increase_pct_1yr.toFixed(1)}%` : null],
      ['Tax Change (5yr)', p.tax_increase_pct_5yr != null ? `${p.tax_increase_pct_5yr.toFixed(1)}%` : null],
      ['Last Sale Price', p.last_sale_price != null ? `$${Math.round(p.last_sale_price).toLocaleString()}` : null],
      ['Last Sale Date', p.last_sale_date],
    ]);
  }

  function sectionDistress(p) {
    // Renders only when at least one of the three distress signals fires —
    // most LP/Lakeview parcels won't have any of these.
    const has = (p.is_scofflaw && p.is_scofflaw === 1)
      || (p.vacant_violations_count != null && p.vacant_violations_count > 0)
      || (p.open_violations_count != null && p.open_violations_count > 0);
    if (!has) {
      const el = document.createElement('div');
      el.className = 'detail-section';
      el.innerHTML = `
        <h3>Distress Signals</h3>
        <div style="font-size:12px; color:#8b949e; padding:8px 0;">
          No active scofflaw, vacant-building, or open-violation flags.
        </div>
      `;
      return el;
    }

    const due = p.vacant_violations_amount_due != null
      ? `$${Math.round(p.vacant_violations_amount_due).toLocaleString()}`
      : null;

    return renderSection('Distress Signals', [
      ['On Scofflaw List', p.is_scofflaw === 1 ? 'Yes' : (p.is_scofflaw === 0 ? 'No' : null)],
      ['Scofflaw Appearances', p.scofflaw_appearances_count],
      ['Most Recent Scofflaw List', p.most_recent_scofflaw_list_date],
      ['Vacant-Bldg Violations', p.vacant_violations_count],
      ['Vacant-Bldg Fines Due', due],
      ['Most Recent Vacant Violation', p.most_recent_vacant_violation_date],
      ['Open Code Violations', p.open_violations_count],
      ['Oldest Violation Age', p.oldest_violation_age_days != null
        ? `${Math.round(p.oldest_violation_age_days)} days` : null],
      ['Years Since Last Permit', p.years_since_last_permit != null
        ? `${p.years_since_last_permit.toFixed(1)} years` : null],
    ]);
  }

  function sectionSFCompare(p) {
    // Side-by-side comparison of building_sf candidates from each source so
    // we can spot-check whether assessor-sum, assessor-largest, or footprint
    // is closest to ground truth on disputed parcels.
    const s = p.bldg_sf_sources || {};
    const fmt = v => v == null ? '—' : `${Math.round(v).toLocaleString()} SF`;
    const tag = name => s.current_source === name
      ? ` <span style="color:#238636; font-size:10px;">(in use)</span>` : '';
    const rows = [
      ['Assessor — sum of cards', { html: `${escapeHtml(fmt(s.assessor_sum))}` }],
      ['Assessor — largest card', { html: `${escapeHtml(fmt(s.assessor_largest))}${tag('assessor')}` }],
      ['Footprint (frozen 2010-11)', { html: `${escapeHtml(fmt(p.building_sf_source === 'footprint' ? s.current : null))}${tag('footprint')}` }],
    ];
    const el = renderSection('Building SF — source comparison', rows);
    // Trailing helper note explaining the merge rule.
    const note = document.createElement('div');
    note.style.cssText = 'font-size:11px; color:#8b949e; padding:6px 0 0;';
    note.textContent = 'Merge rule: assessor wins when non-null; footprint backstops where assessor is empty.';
    el.querySelector('.detail-grid').after(note);
    return el;
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
    const rows = pairs.map(([label, value]) => {
      let valHtml;
      if (value == null || value === '') {
        valHtml = '—';
      } else if (typeof value === 'object' && value.html != null) {
        // Caller passes pre-escaped HTML — used for inline markup like
        // the small "(footprint)" / "(assessor)" source tags. Caller is
        // responsible for escaping any user-provided text inside.
        valHtml = value.html;
      } else {
        valHtml = escapeHtml(String(value));
      }
      return `
        <div class="detail-item">
          <div class="label">${escapeHtml(label)}</div>
          <div class="value">${valHtml}</div>
        </div>
      `;
    }).join('');
    el.innerHTML = `
      <h3>${escapeHtml(title)}</h3>
      <div class="detail-grid">${rows}</div>
      ${trailingHtml}
    `;
    return el;
  }
})();
