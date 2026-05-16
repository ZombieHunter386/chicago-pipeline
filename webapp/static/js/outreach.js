// Outreach detail-panel sections + compose modal logic.
// Loaded only when FEATURE_OUTREACH is true.

(function () {
  'use strict';

  // Helpers borrowed from detail.js — keep a small inline copy to avoid
  // making detail.js export them. If detail.js ever exposes a real namespace
  // we should reuse from there.
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function fmtDate(iso) {
    if (!iso) return '—';
    // Sent dates are ISO 8601 UTC. Show local-time short form.
    try {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      return d.toLocaleString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
        hour: 'numeric', minute: '2-digit',
      });
    } catch (_) { return iso; }
  }

  // ---------- Toast utility ----------
  // Top-right slide-in notifications. Auto-dismiss after `durationMs` or
  // click to dismiss. Stacks vertically when multiple show at once.
  function showToast(message, kind, durationMs) {
    kind = kind || 'info';
    durationMs = durationMs == null ? 3000 : durationMs;
    let container = document.getElementById('outreach-toast-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'outreach-toast-container';
      document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    toast.className = 'outreach-toast outreach-toast-' + kind;
    toast.textContent = message;
    toast.addEventListener('click', () => toast.remove());
    container.appendChild(toast);
    // Trigger entrance on the next frame so the transition runs.
    requestAnimationFrame(() => toast.classList.add('show'));
    setTimeout(() => {
      toast.classList.remove('show');
      // Remove from DOM after transition; safety timeout in case transitionend
      // doesn't fire (e.g., element was already detached by click-to-dismiss).
      setTimeout(() => toast.remove(), 280);
    }, durationMs);
  }

  async function fetchOutreach(pin) {
    const resp = await fetch(`/api/parcels/${encodeURIComponent(pin)}/outreach`);
    if (!resp.ok) throw new Error(`fetch outreach failed: ${resp.status}`);
    return resp.json();
  }

  async function upsertContact(pin, fields) {
    const resp = await fetch('/api/contacts/upsert', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin, ...fields }),
    });
    if (!resp.ok) throw new Error(await resp.text() || `HTTP ${resp.status}`);
    return resp.json();
  }

  async function markReplied(outreachId, responseType = 'responded') {
    const resp = await fetch(`/api/outreach/${outreachId}/mark-replied`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ response_type: responseType }),
    });
    if (!resp.ok) throw new Error(await resp.text() || `HTTP ${resp.status}`);
    return resp.json();
  }

  async function setStage(pin, stage) {
    const resp = await fetch(`/api/parcels/${encodeURIComponent(pin)}/stage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stage }),
    });
    if (!resp.ok) throw new Error(await resp.text() || `HTTP ${resp.status}`);
    return resp.json();
  }

  // ---------- Contact section ----------

  function renderContactSection(parcel, data) {
    const el = document.createElement('div');
    el.className = 'detail-section';
    const contact = data.contact || {};
    const email = contact.email || '';

    const gmailStatus = data.gmail_connected
      ? '<span class="outreach-gmail-status-connected">✓ Gmail connected</span>'
      : '<a href="/api/oauth/start" class="outreach-connect-link">Connect Gmail</a>';

    el.innerHTML = `
      <h3>Contact</h3>
      <div class="detail-grid" style="grid-template-columns: 1fr;">
        <div class="detail-item">
          <div class="label">Email</div>
          <div class="value">
            <input type="email" id="outreach-email-input"
                   class="outreach-input"
                   placeholder="owner@example.com"
                   value="${escapeHtml(email)}" />
          </div>
        </div>
        <div class="detail-item">
          <div class="label">Owner (Assessor)</div>
          <div class="value">${escapeHtml(parcel.owner_name || '—')}</div>
        </div>
        <div class="detail-item">
          <div class="label">Mail address</div>
          <div class="value">${escapeHtml(parcel.mail_address || '—')}</div>
        </div>
        <div class="detail-item">
          <div class="value outreach-compose-row">
            <button type="button" class="btn btn-primary" id="outreach-compose-btn"
                    ${email ? '' : 'disabled'}
                    title="${email ? '' : 'Add an email above first'}">
              Compose email…
            </button>
            ${gmailStatus}
          </div>
        </div>
      </div>
    `;

    const input = el.querySelector('#outreach-email-input');
    let original = email;
    input.addEventListener('blur', async () => {
      const v = input.value.trim();
      if (v === original) return;
      if (v && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) {
        showToast('Invalid email', 'error');
        return;
      }
      try {
        await upsertContact(parcel.pin, { email: v || null });
        original = v;
        showToast(v ? 'Email saved' : 'Email cleared', 'success');
        window.dispatchEvent(new CustomEvent('outreach:refresh',
                                              { detail: { pin: parcel.pin } }));
      } catch (e) {
        showToast("Couldn't save email", 'error');
      }
    });

    const btn = el.querySelector('#outreach-compose-btn');
    btn.addEventListener('click', () => {
      if (typeof window.__outreachOpenCompose === 'function') {
        const liveEmail = input.value.trim();
        const liveContact = liveEmail
          ? Object.assign({}, data.contact || {}, { email: liveEmail })
          : data.contact;
        window.__outreachOpenCompose(parcel, liveContact, data.sender_address);
      }
    });

    // Enable Compose live as the user types, so the first click after entering
    // an email doesn't land on a still-disabled button (the blur-save round
    // trip is async and was racing the click).
    input.addEventListener('input', () => {
      const hasEmail = !!input.value.trim();
      btn.disabled = !hasEmail;
      btn.title = hasEmail ? '' : 'Add an email above first';
    });
    return el;
  }

  // ---------- History section ----------

  function renderHistorySection(parcel, data) {
    const el = document.createElement('div');
    el.className = 'detail-section';
    const rows = data.outreach || [];
    if (rows.length === 0) {
      el.innerHTML = '<h3>Outreach History</h3>'
        + '<div style="font-size:12px; color:#8b949e;">No outreach yet.</div>';
      return el;
    }
    const html = rows.map(r => {
      const replied = r.response_date
        ? `<span class="outreach-item-replied">✓ replied ${escapeHtml(fmtDate(r.response_date))}</span>`
        : `<button type="button" class="btn btn-sm" data-mark-replied="${escapeHtml(String(r.outreach_id))}">Mark replied</button>`;
      const body = (r.final_body || r.draft_body || '').trim();
      return `
        <div class="outreach-item">
          <div class="outreach-item-head">
            <strong class="outreach-item-subject">${escapeHtml(r.draft_subject || '(no subject)')}</strong>
            <span class="outreach-item-meta">${escapeHtml(r.channel || 'email')} · ${escapeHtml(fmtDate(r.sent_date))}</span>
            ${replied}
          </div>
          <details>
            <summary>Show body</summary>
            <pre>${escapeHtml(body)}</pre>
          </details>
        </div>
      `;
    }).join('');
    el.innerHTML = `<h3>Outreach History</h3>${html}`;
    el.querySelectorAll('[data-mark-replied]').forEach(btn => {
      btn.addEventListener('click', async () => {
        btn.disabled = true; btn.textContent = '…';
        try {
          await markReplied(parseInt(btn.dataset.markReplied, 10));
          showToast('Marked replied', 'success');
          window.dispatchEvent(new CustomEvent('outreach:refresh',
                                                { detail: { pin: parcel.pin } }));
        } catch (e) {
          btn.disabled = false; btn.textContent = 'Mark replied';
          showToast("Couldn't mark replied", 'error');
        }
      });
    });
    return el;
  }

  // ---------- Stage section ----------

  function renderStageSection(parcel) {
    const el = document.createElement('div');
    el.className = 'detail-section';
    const stages = ['scored', 'outreach', 'responded', 'introduced', 'dead'];
    const cur = parcel.stage || 'scored';
    el.innerHTML = `
      <h3>Stage</h3>
      <div class="detail-grid" style="grid-template-columns: 1fr;">
        <div class="detail-item">
          <select id="outreach-stage-select" class="outreach-input outreach-stage-select">
            ${stages.map(s => `<option value="${s}"${s === cur ? ' selected' : ''}>${s}</option>`).join('')}
          </select>
        </div>
      </div>
    `;
    const sel = el.querySelector('#outreach-stage-select');
    sel.addEventListener('change', async () => {
      try {
        await setStage(parcel.pin, sel.value);
        showToast('Stage updated to "' + sel.value + '"', 'success');
      } catch (e) {
        showToast("Couldn't update stage", 'error');
      }
    });
    return el;
  }

  // ---------- Public API ----------

  async function renderOutreachSections(parcel, panel) {
    // Snapshot the panel's render serial at call time. If a newer render
    // bumps it before our fetch returns, we're stale — bail without appending.
    const serial = panel.dataset.renderSerial;
    let data;
    try {
      data = await fetchOutreach(parcel.pin);
    } catch (_) {
      if (panel.dataset.renderSerial !== serial) return;
      const err = document.createElement('div');
      err.className = 'detail-section';
      err.innerHTML = '<h3>Outreach</h3><div style="font-size:12px; color:#f85149;">Couldn\'t load outreach data.</div>';
      panel.appendChild(err);
      return;
    }
    if (panel.dataset.renderSerial !== serial) return;
    panel.appendChild(renderStageSection(parcel));
    panel.appendChild(renderContactSection(parcel, data));
    panel.appendChild(renderHistorySection(parcel, data));
  }

  // Re-render on the "outreach:refresh" custom event by re-selecting the
  // parcel. detail.js owns reloadDetail; we cooperate via the existing
  // parcelselect event.
  window.addEventListener('outreach:refresh', (e) => {
    const pin = e.detail && e.detail.pin;
    if (pin) {
      window.dispatchEvent(new CustomEvent('parcelselect', { detail: { pin } }));
    }
  });

  // Expose to detail.js
  window.__outreachRenderSections = renderOutreachSections;

  // ---------- Due Today banner ----------

  async function fetchDue() {
    const resp = await fetch('/api/outreach/due');
    if (!resp.ok) throw new Error(`fetch due failed: ${resp.status}`);
    return resp.json();
  }

  function channelEmoji(channel) {
    return {email: '✉', phone: '☎', mail: '✉', end_of_sequence: '✓'}[channel] || '';
  }

  function channelLabel(channel, count) {
    const noun = {
      email: count === 1 ? 'email' : 'emails',
      phone: count === 1 ? 'phone call' : 'phone calls',
      mail:  count === 1 ? 'letter' : 'letters',
      end_of_sequence: count === 1 ? 'ready to retire' : 'ready to retire',
    }[channel] || channel;
    return `${count} ${noun}`;
  }

  async function renderDueToday() {
    const bar = document.getElementById('due-today-bar');
    if (!bar) return;
    let data;
    try { data = await fetchDue(); } catch (_) { return; }
    const groups = data.groups || [];
    if (groups.length === 0) {
      bar.hidden = true;
      bar.innerHTML = '<span class="due-today-label">DUE TODAY</span>'
        + '<span id="due-today-chips" class="due-today-chips"></span>';
      return;
    }
    bar.hidden = false;
    const chipsEl = bar.querySelector('#due-today-chips');
    chipsEl.innerHTML = '';
    groups.forEach(g => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'due-today-chip due-today-chip-' + g.channel;
      chip.dataset.channel = g.channel;
      chip.textContent = `${channelEmoji(g.channel)} ${channelLabel(g.channel, g.count)}`;
      chip.addEventListener('click', () => toggleChipDropdown(chip, g));
      chipsEl.appendChild(chip);
    });
  }

  function toggleChipDropdown(chip, group) {
    // Close any existing dropdown
    const existing = document.getElementById('due-today-dropdown');
    if (existing) {
      existing.remove();
      if (existing.dataset.forChannel === group.channel) return; // re-click = close
    }
    const dropdown = document.createElement('div');
    dropdown.id = 'due-today-dropdown';
    dropdown.className = 'due-today-dropdown';
    dropdown.dataset.forChannel = group.channel;
    dropdown.innerHTML = group.items.map(it => {
      const overdue = it.days_overdue > 0
        ? `<span class="due-today-overdue">+${it.days_overdue}d</span>` : '';
      const subline = group.channel === 'end_of_sequence'
        ? `Sequence complete · ${it.days_since_last}d since last touch`
        : `Touch ${it.touch} · ${it.target_date}`;
      return `
        <button type="button" class="due-today-row" data-pin="${escapeHtml(it.pin)}">
          <div class="due-today-row-main">
            <strong>${escapeHtml(it.address || it.pin)}</strong>
            <span class="due-today-row-owner">${escapeHtml(it.owner_first_name || '')}</span>
          </div>
          <div class="due-today-row-sub">${escapeHtml(subline)} ${overdue}</div>
        </button>
      `;
    }).join('');
    dropdown.querySelectorAll('[data-pin]').forEach(btn => {
      btn.addEventListener('click', () => {
        const pin = btn.dataset.pin;
        dropdown.remove();
        window.dispatchEvent(new CustomEvent('parcelselect', { detail: { pin } }));
      });
    });
    // Position below the chip
    const rect = chip.getBoundingClientRect();
    dropdown.style.position = 'fixed';
    dropdown.style.top = (rect.bottom + 4) + 'px';
    dropdown.style.left = rect.left + 'px';
    document.body.appendChild(dropdown);
    // Click-outside to close
    setTimeout(() => {
      document.addEventListener('click', function onDoc(e) {
        if (!dropdown.contains(e.target) && e.target !== chip) {
          dropdown.remove();
          document.removeEventListener('click', onDoc);
        }
      });
    }, 0);
  }

  // Render on page load + whenever an outreach refresh fires
  window.addEventListener('DOMContentLoaded', () => { renderDueToday(); });
  window.addEventListener('outreach:refresh', () => { renderDueToday(); });

  window.__outreachRenderDueToday = renderDueToday;

  // ---------- Compose modal ----------

  async function fetchTemplates(pin) {
    const resp = await fetch(`/api/outreach/templates?pin=${encodeURIComponent(pin)}`);
    if (!resp.ok) throw new Error(`fetch templates failed: ${resp.status}`);
    return resp.json();
  }

  async function sendOutreach(payload) {
    const resp = await fetch('/api/outreach/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(text || `HTTP ${resp.status}`);
    }
    return resp.json();
  }

  function closeModal() {
    const m = document.getElementById('outreach-modal-root');
    if (m) m.remove();
  }

  async function openComposeModal(parcel, contact, senderAddress) {
    // Fetch templates with rendered preview for this pin.
    let tplResp;
    try { tplResp = await fetchTemplates(parcel.pin); }
    catch (_) {
      alert("Couldn't load outreach templates.");
      return;
    }
    const templates = tplResp.templates || [];
    if (templates.length === 0) {
      alert('No outreach templates configured. Edit config/outreach_templates.yaml.');
      return;
    }

    // Build modal DOM.
    const root = document.createElement('div');
    root.id = 'outreach-modal-root';
    root.className = 'outreach-modal-backdrop';
    root.innerHTML = `
      <div class="outreach-modal" role="dialog" aria-modal="true" aria-label="Compose email">
        <div class="outreach-modal-head">
          <h3>Compose email — ${escapeHtml(parcel.address || parcel.pin)}</h3>
          <button type="button" class="btn btn-sm" id="outreach-modal-close">Close</button>
        </div>
        <div class="outreach-modal-body">
          <div class="cm-row">
            <label class="cm-label" for="cm-template">Template</label>
            <select id="cm-template">
              ${templates.map((t, i) => `<option value="${i}">${escapeHtml(t.label || t.name)}</option>`).join('')}
            </select>
          </div>
          <div class="cm-row">
            <label class="cm-label" for="cm-from">From</label>
            <input type="text" id="cm-from" value="${escapeHtml(senderAddress || '')}" disabled />
          </div>
          <div class="cm-row">
            <label class="cm-label" for="cm-to">To</label>
            <input type="email" id="cm-to" value="${escapeHtml(contact && contact.email || '')}" />
          </div>
          <div class="cm-row">
            <label class="cm-label" for="cm-subject">Subject</label>
            <input type="text" id="cm-subject" value="" />
          </div>
          <div class="cm-body-row">
            <label class="cm-label" for="cm-body">Body</label>
            <textarea id="cm-body"></textarea>
          </div>
        </div>
        <div class="outreach-modal-foot">
          <span class="outreach-modal-error" id="cm-error"></span>
          <button type="button" class="btn" id="cm-cancel">Cancel</button>
          <button type="button" class="btn" id="cm-save-template" title="Save this draft as a template">Save template</button>
          <button type="button" class="btn btn-primary" id="cm-send">Send</button>
        </div>
      </div>
    `;
    document.body.appendChild(root);

    const subjectInput = root.querySelector('#cm-subject');
    const bodyInput = root.querySelector('#cm-body');
    const tplSelect = root.querySelector('#cm-template');
    const errSpan = root.querySelector('#cm-error');
    const sendBtn = root.querySelector('#cm-send');

    function applyTemplate(idx) {
      const t = templates[idx];
      if (!t) return;
      subjectInput.value = t.rendered_subject || t.subject || '';
      bodyInput.value = t.rendered_body || t.body || '';
    }
    applyTemplate(0);
    tplSelect.addEventListener('change', () => applyTemplate(parseInt(tplSelect.value, 10)));

    function currentTemplateName() {
      const idx = parseInt(tplSelect.value, 10);
      const t = templates[idx];
      return t ? t.name : '';
    }

    // Escape closes the modal. Defined first so onClose can remove it.
    function onKey(ev) {
      if (ev.key === 'Escape') onClose();
    }
    function onClose() {
      document.removeEventListener('keydown', onKey);
      closeModal();
    }
    root.querySelector('#outreach-modal-close').addEventListener('click', onClose);
    root.querySelector('#cm-cancel').addEventListener('click', onClose);
    // Click outside dialog to close.
    root.addEventListener('click', (e) => { if (e.target === root) onClose(); });
    document.addEventListener('keydown', onKey);

    sendBtn.addEventListener('click', async () => {
      const to = root.querySelector('#cm-to').value.trim();
      const subject = subjectInput.value.trim();
      const body = bodyInput.value;
      errSpan.textContent = '';
      if (!to || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(to)) {
        errSpan.textContent = 'invalid recipient email'; return;
      }
      if (!subject) { errSpan.textContent = 'subject required'; return; }
      sendBtn.disabled = true; sendBtn.textContent = 'Sending…';
      try {
        await sendOutreach({ pin: parcel.pin, to, subject, body });
        onClose();
        showToast('Email sent', 'success');
        window.dispatchEvent(new CustomEvent('outreach:refresh',
                                              { detail: { pin: parcel.pin } }));
      } catch (e) {
        errSpan.textContent = e.message || 'send failed';
        sendBtn.disabled = false; sendBtn.textContent = 'Send';
      }
    });

    const saveBtn = root.querySelector('#cm-save-template');
    saveBtn.addEventListener('click', async () => {
      const defaultName = currentTemplateName() || '';
      const name = (window.prompt('Save template as (use existing name to overwrite):', defaultName) || '').trim();
      if (!name) return;
      const existing = templates.find(t => t.name === name);
      if (existing && name === defaultName) {
        if (!window.confirm(`Overwrite template "${name}"?`)) return;
      } else if (existing) {
        if (!window.confirm(`"${name}" already exists. Overwrite?`)) return;
      }
      const subject = subjectInput.value.trim();
      const body = bodyInput.value;
      if (!subject) { errSpan.textContent = 'subject required'; return; }
      errSpan.textContent = '';
      saveBtn.disabled = true; saveBtn.textContent = 'Saving…';
      try {
        const resp = await fetch('/api/outreach/templates/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, subject, body }),
        });
        if (!resp.ok) {
          const text = await resp.text();
          throw new Error(text || `HTTP ${resp.status}`);
        }
        const result = await resp.json();
        // Insert or update the local templates list.
        const idx = templates.findIndex(t => t.name === name);
        const merged = {
          name: result.template.name,
          label: result.template.label || name,
          subject, body,
          rendered_subject: subject, rendered_body: body,
        };
        if (idx >= 0) {
          templates[idx] = merged;
        } else {
          templates.push(merged);
          const opt = document.createElement('option');
          opt.value = String(templates.length - 1);
          opt.textContent = merged.label;
          tplSelect.appendChild(opt);
          tplSelect.value = opt.value;
        }
        showToast('Template saved', 'success');
      } catch (e) {
        errSpan.textContent = e.message || 'save failed';
        errSpan.style.color = '';
      } finally {
        saveBtn.disabled = false; saveBtn.textContent = 'Save template';
      }
    });
  }

  // Wire the compose button trigger from renderContactSection.
  window.__outreachOpenCompose = openComposeModal;
})();
