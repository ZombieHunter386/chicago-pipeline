// Right panel: renders parcel detail sections from /api/parcels/<pin>.

(function () {
  let reqId = 0;

  window.addEventListener('parcelselect', async (e) => {
    const detail = e && e.detail ? e.detail : null;
    if (!detail) { renderPlaceholder(); return; }
    if (detail.groupId != null) {
      await loadGroupDetail(detail.groupId);
      return;
    }
    const pin = detail.pin;
    if (!pin) { renderPlaceholder(); return; }

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

  async function loadGroupDetail(groupId) {
    const myId = ++reqId;
    let resp, data;
    try {
      resp = await fetch(`/api/consolidation-groups/${encodeURIComponent(groupId)}`);
    } catch (_) {
      if (myId === reqId) renderError("Couldn't load consolidation group — try again.");
      return;
    }
    if (myId !== reqId) return;
    if (!resp.ok) {
      renderError(resp.status === 404 ? 'Consolidation group not found.' : "Couldn't load group.");
      return;
    }
    try { data = await resp.json(); }
    catch (_) { if (myId === reqId) renderError("Couldn't load consolidation group."); return; }
    if (myId !== reqId) return;
    renderGroupView(data);
  }

  function renderGroupView(g) {
    const panel = document.getElementById('detail-panel');
    panel.innerHTML = '';

    // Header section: aggregate stats + member list with click-through.
    panel.appendChild(renderSection('Consolidation Group', [
      ['Owner', g.owner_name],
      ['Group ID', g.group_id],
      ['Detected', g.detected_date],
      ['Parcels in group', g.parcel_count],
      ['Combined Lot', g.combined_lot_size_sf != null
        ? `${Math.round(g.combined_lot_size_sf).toLocaleString()} SF` : null],
      ['Combined Building', g.combined_building_sf != null
        ? `${Math.round(g.combined_building_sf).toLocaleString()} SF` : null],
      ['Combined Assessed', g.sum_assessed_total != null
        ? `$${Math.round(g.sum_assessed_total).toLocaleString()}` : null],
      ['Sum Annual Tax (est.)', g.sum_estimated_annual_tax != null
        ? `$${Math.round(g.sum_estimated_annual_tax).toLocaleString()}` : null],
      ['Oldest Building', g.oldest_year_built],
      ['Longest Hold', g.longest_hold_years != null
        ? `${Math.round(g.longest_hold_years)} years` : null],
    ]));

    if (g.zoning_summary) panel.appendChild(sectionGroupZoning(g));

    // Member parcel list with click-through to the individual parcel view.
    const members = g.members || [];
    const sec = document.createElement('div');
    sec.className = 'detail-section';
    const h = document.createElement('h3');
    h.textContent = `Member Parcels (${members.length})`;
    sec.appendChild(h);
    const ul = document.createElement('div');
    ul.className = 'detail-grid';
    ul.style.gridTemplateColumns = '1fr';
    members.forEach(m => {
      const row = document.createElement('div');
      row.className = 'detail-item';
      row.style.cursor = 'pointer';
      const sub = [
        m.lot_size_sf ? `${Math.round(m.lot_size_sf).toLocaleString()} SF lot` : null,
        m.building_sf ? `${Math.round(m.building_sf).toLocaleString()} SF bldg` : null,
        m.year_built ? `Built ${m.year_built}` : null,
      ].filter(Boolean).join(' · ');
      row.innerHTML = `
        <div class="label" style="cursor:pointer;">${escapeHtml(m.address || m.pin)}</div>
        <div class="value" style="font-size:11px; color:#8b949e;">${escapeHtml(sub || '—')}</div>
      `;
      row.addEventListener('click', () => {
        window.dispatchEvent(new CustomEvent('parcelselect', { detail: { pin: m.pin } }));
      });
      ul.appendChild(row);
    });
    sec.appendChild(ul);
    panel.appendChild(sec);
  }

  function sectionGroupZoning(g) {
    const z = g.zoning_summary || {};
    // Zone display: single value if all members share a zone, otherwise
    // a "Various: <zone> (n) · <zone> (n)" string preserving counts.
    let zoneDisplay;
    if (z.is_uniform_zone) {
      zoneDisplay = z.dominant_zone || '—';
    } else {
      const parts = (z.breakdown || []).map(
        b => `${b.zone_class} (${b.parcel_count})`
      );
      zoneDisplay = parts.length ? `Various: ${parts.join(' · ')}` : '—';
    }

    // Multifamily-by-right summary
    const mfMap = {
      all: 'Yes — all members',
      none: 'No — none of the members',
      mixed: 'Mixed across members',
      unknown: '—',
    };

    // FAR Gap (Δ) phrasing matches the per-parcel formatter for consistency.
    let farGapDelta = null;
    if (z.combined_far_gap_delta != null) {
      const v = z.combined_far_gap_delta;
      const sign = v > 0 ? '+' : '';
      const tail = v >= 0 ? 'FAR available' : 'FAR over by-right';
      farGapDelta = `${sign}${v.toFixed(2)} ${tail}`;
    }

    const dz = z.dominant_zone || '—';
    const buildable = z.combined_max_buildable_sf != null
      ? `${Math.round(z.combined_max_buildable_sf).toLocaleString()} SF`
      : null;
    const subtitleNote = z.is_uniform_zone
      ? `All members share zone ${dz}.`
      : `Combined development potential below assumes the dominant zone (${dz}) governs the consolidated lot. Where zones differ, the actual entitlement may require rezoning.`;

    const pairs = [
      ['Zone class', zoneDisplay],
      ['Allows multifamily', mfMap[z.allows_multifamily_status] || '—'],
      ['Built FAR (combined)', z.combined_built_far],
      ['Max FAR (dominant zone)',
        (z.breakdown && z.breakdown[0] && z.breakdown[0].max_far) || null],
      ['FAR Gap (Δ)', farGapDelta],
      ['Max buildable SF (combined)', buildable],
      ['Max units (dominant zone)', z.combined_max_units_dominant_zone],
    ];

    // If zones differ, append a per-zone breakdown so the user sees where
    // the constraints come from.
    if (!z.is_uniform_zone && z.breakdown && z.breakdown.length > 1) {
      z.breakdown.forEach(b => {
        const lot = b.lot_sf
          ? `${Math.round(b.lot_sf).toLocaleString()} SF lot`
          : null;
        const far = b.max_far != null ? `max FAR ${b.max_far}` : null;
        const mlu = b.min_lot_area_per_unit != null
          ? `min ${b.min_lot_area_per_unit} sf/unit` : null;
        const detail = [b.parcel_count + ' parcels', lot, far, mlu]
          .filter(Boolean).join(' · ');
        pairs.push([`  ${b.zone_class}`, detail]);
      });
    }

    const el = renderSection('Zoning (combined)', pairs);
    const note = document.createElement('div');
    note.style.cssText = 'font-size:11px; color:#8b949e; padding:6px 0 0; line-height:1.4;';
    note.textContent = subtitleNote;
    el.querySelector('.detail-grid').after(note);
    return el;
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

  // Cache the scoring config across renders so we don't refetch per parcel.
  let _scoringConfig = null;
  let _scoringConfigPromise = null;

  function loadScoringConfig() {
    if (_scoringConfig != null) return Promise.resolve(_scoringConfig);
    if (_scoringConfigPromise) return _scoringConfigPromise;
    _scoringConfigPromise = fetch('/api/scoring-config')
      .then(r => r.ok ? r.json() : null)
      .then(d => { _scoringConfig = d; return d; })
      .catch(() => null);
    return _scoringConfigPromise;
  }

  // Mirror pipeline.score.normalize_signal — keep these in lock-step. If the
  // Python rules change, update this too.
  function normalizeSignal(rawValue, sigCfg) {
    if (sigCfg.kind === 'binary') {
      if (rawValue == null) return 0.0;
      return rawValue ? 1.0 : 0.0;
    }
    if (rawValue == null) return 0.5;
    const lo = sigCfg.normalization.min;
    const hi = sigCfg.normalization.max;
    if (hi === lo) return 0.5;
    if (rawValue <= lo) return 0.0;
    if (rawValue >= hi) return 1.0;
    return (rawValue - lo) / (hi - lo);
  }

  function sectionScoreBreakdown(p) {
    const el = document.createElement('div');
    el.className = 'detail-section';
    el.innerHTML = '<h3>Score Breakdown</h3><div class="score-breakdown-body" style="font-size:12px; color:#8b949e; padding:8px 0;">Loading…</div>';
    const body = el.querySelector('.score-breakdown-body');

    if (p.score == null) {
      body.innerHTML = `
        Score not populated for this parcel. Individual condo units are
        skipped at score time — see Score plan §3 for the rationale. Run
        <code style="color:#c9d1d9;">.venv/bin/python -m pipeline.score</code>
        to refresh after methodology changes.
      `;
      return el;
    }

    loadScoringConfig().then(cfg => {
      if (!cfg || !cfg.signals) {
        body.textContent = "Couldn't load scoring config.";
        return;
      }
      renderScoreBreakdown(body, p, cfg);
    });

    return el;
  }

  function renderScoreBreakdown(host, p, cfg) {
    // Compute per-signal contribution with the same math as pipeline.score
    // so the breakdown matches the score column exactly.
    const rows = [];
    let total = 0;
    for (const [name, sig] of Object.entries(cfg.signals)) {
      const raw = p[name];
      const normalized = normalizeSignal(raw, sig);
      const flipped = sig.direction === 'negative'
        ? (1.0 - normalized) : normalized;
      const contribution = flipped * sig.weight;
      total += contribution;
      rows.push({
        name, raw, normalized, flipped, sig, contribution,
        is_imputed: raw == null,
      });
    }

    // Sort by absolute contribution descending — biggest movers first.
    rows.sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution));

    // The recomputed total should match p.score within rounding.
    const recomputed = Math.round(total * 100 * 10000) / 10000;

    let html = `
      <div style="font-size:12px; color:#c9d1d9; padding:6px 0;">
        <span style="font-size:18px; color:#58a6ff; font-weight:600;">${p.score.toFixed(2)}</span>
        <span style="color:#8b949e; font-size:11px; margin-left:6px;">
          / 100 · version ${escapeHtml(cfg.version || '?')}
        </span>
      </div>
      <table style="width:100%; font-size:11px; border-collapse:collapse; margin-top:6px;">
        <thead>
          <tr style="color:#8b949e; text-align:left;">
            <th style="padding:4px 6px; border-bottom:1px solid #30363d;">Signal</th>
            <th style="padding:4px 6px; border-bottom:1px solid #30363d;">Raw</th>
            <th style="padding:4px 6px; border-bottom:1px solid #30363d;">Norm</th>
            <th style="padding:4px 6px; border-bottom:1px solid #30363d;">Dir</th>
            <th style="padding:4px 6px; border-bottom:1px solid #30363d;">Weight</th>
            <th style="padding:4px 6px; border-bottom:1px solid #30363d; text-align:right;">Contrib</th>
          </tr>
        </thead>
        <tbody>
    `;

    for (const r of rows) {
      const insig = r.sig.insignificant;
      const rowStyle = insig ? 'opacity:0.45;' : '';
      const rawDisplay = r.raw == null
        ? '<span style="color:#8b949e; font-style:italic;">null</span>'
        : (r.sig.kind === 'binary'
            ? (r.raw ? 'yes' : 'no')
            : (typeof r.raw === 'number'
                ? (Math.abs(r.raw) >= 1000 ? Math.round(r.raw).toLocaleString() : r.raw.toFixed(2))
                : escapeHtml(String(r.raw))));
      const dirArrow = r.sig.direction === 'negative' ? '↓' : '↑';
      const dirColor = r.sig.direction === 'negative' ? '#f85149' : '#3fb950';
      const contribPct = (r.contribution * 100).toFixed(2);
      const contribColor = r.contribution > 0 ? '#3fb950' : (r.contribution < 0 ? '#f85149' : '#8b949e');
      html += `
        <tr style="${rowStyle}">
          <td style="padding:3px 6px; color:#c9d1d9;">${escapeHtml(r.name)}${insig ? ' <span style="font-size:9px; color:#8b949e;">(insig)</span>' : ''}</td>
          <td style="padding:3px 6px; color:#c9d1d9;">${rawDisplay}</td>
          <td style="padding:3px 6px; color:#c9d1d9;">${r.normalized.toFixed(2)}${r.is_imputed ? ' <span style="font-size:9px; color:#8b949e;">imp</span>' : ''}</td>
          <td style="padding:3px 6px; color:${dirColor};">${dirArrow}</td>
          <td style="padding:3px 6px; color:#c9d1d9;">${r.sig.weight.toFixed(3)}</td>
          <td style="padding:3px 6px; text-align:right; color:${contribColor};">${contribPct}</td>
        </tr>
      `;
    }

    html += `
        </tbody>
      </table>
      <div style="font-size:10px; color:#8b949e; padding:6px 0 0; line-height:1.4;">
        <strong>Norm</strong> is the signal value rescaled to [0,1] (5th–95th
        percentile range, clipped). <strong>Dir</strong> ↓ means the signal is
        flipped (high raw → low contribution). <strong>Contrib</strong> is the
        signal's contribution to the final score in points out of 100 (sums
        to ≈ ${p.score.toFixed(2)}). Values marked <em>imp</em> were null;
        treated as 0.5 (continuous) or 0 (binary). Greyed-out rows were
        statistically insignificant in the regression — they don't contribute.
      </div>
    `;
    host.innerHTML = html;

    // Sanity check: the recomputed total should match the stored score
    // within rounding. Surface a warning in dev console if it drifts.
    if (Math.abs(recomputed - p.score) > 0.05) {
      console.warn(`Score breakdown drift: stored=${p.score} recomputed=${recomputed}`);
    }
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
