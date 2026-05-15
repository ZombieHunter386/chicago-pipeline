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
            <span class="outreach-email-status" id="outreach-email-status"></span>
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
        <div class="detail-item" style="display:flex; gap:8px; align-items:center;">
          <button type="button" class="btn btn-primary" id="outreach-compose-btn"
                  ${email ? '' : 'disabled'}
                  title="${email ? '' : 'Add an email above first'}">
            Compose email…
          </button>
          <span class="outreach-gmail-status" style="font-size:11px; color:#8b949e;">
            ${data.gmail_connected
              ? 'Gmail connected'
              : '<a href="/api/oauth/start">Connect Gmail</a>'}
          </span>
        </div>
      </div>
    `;

    // Wire the email input — save on blur if changed.
    const input = el.querySelector('#outreach-email-input');
    const status = el.querySelector('#outreach-email-status');
    let original = email;
    input.addEventListener('blur', async () => {
      const v = input.value.trim();
      if (v === original) return;
      if (v && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) {
        status.textContent = 'invalid email';
        status.style.color = '#f85149';
        return;
      }
      status.textContent = 'saving…';
      status.style.color = '#8b949e';
      try {
        await upsertContact(parcel.pin, { email: v || null });
        status.textContent = 'saved';
        status.style.color = '#3fb950';
        original = v;
        // Re-render the panel to re-enable the Compose button.
        window.dispatchEvent(new CustomEvent('outreach:refresh',
                                              { detail: { pin: parcel.pin } }));
      } catch (e) {
        status.textContent = 'error';
        status.style.color = '#f85149';
      }
    });

    // Compose button → open modal (wired up in Task 6).
    const btn = el.querySelector('#outreach-compose-btn');
    btn.addEventListener('click', () => {
      if (typeof window.__outreachOpenCompose === 'function') {
        window.__outreachOpenCompose(parcel, data.contact, data.sender_address);
      }
    });
    return el;
  }

  // ---------- History section ----------

  function renderHistorySection(parcel, data) {
    const el = document.createElement('div');
    el.className = 'detail-section';
    const rows = data.outreach || [];
    if (rows.length === 0) {
      el.innerHTML = '<h3>Outreach History</h3><div style="font-size:12px; color:#8b949e;">No outreach yet.</div>';
      return el;
    }
    const html = rows.map(r => {
      const replied = r.response_date
        ? `<span style="color:#3fb950; margin-left:8px;">✓ replied ${escapeHtml(fmtDate(r.response_date))}</span>`
        : `<button type="button" class="btn btn-sm" data-mark-replied="${escapeHtml(String(r.outreach_id))}" style="margin-left:8px;">Mark replied</button>`;
      const body = (r.final_body || r.draft_body || '').trim();
      return `
        <div class="outreach-item">
          <div class="outreach-item-head">
            <strong>${escapeHtml(r.draft_subject || '(no subject)')}</strong>
            <span style="color:#8b949e; font-size:11px; margin-left:8px;">
              ${escapeHtml(r.channel || 'email')} · ${escapeHtml(fmtDate(r.sent_date))}
            </span>
            ${replied}
          </div>
          <details>
            <summary style="font-size:11px; color:#8b949e; cursor:pointer;">Show body</summary>
            <pre style="white-space:pre-wrap; font-size:12px; padding:6px 0; color:#c9d1d9;">${escapeHtml(body)}</pre>
          </details>
        </div>
      `;
    }).join('');
    el.innerHTML = `<h3>Outreach History</h3>${html}`;
    // Wire mark-replied buttons.
    el.querySelectorAll('[data-mark-replied]').forEach(btn => {
      btn.addEventListener('click', async () => {
        btn.disabled = true; btn.textContent = '…';
        try {
          await markReplied(parseInt(btn.dataset.markReplied, 10));
          window.dispatchEvent(new CustomEvent('outreach:refresh',
                                                { detail: { pin: parcel.pin } }));
        } catch (e) {
          btn.disabled = false; btn.textContent = 'Mark replied';
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
        <div class="detail-item" style="display:flex; gap:8px; align-items:center;">
          <select id="outreach-stage-select" class="outreach-input">
            ${stages.map(s => `<option value="${s}"${s === cur ? ' selected' : ''}>${s}</option>`).join('')}
          </select>
          <span id="outreach-stage-status" style="font-size:11px; color:#8b949e;"></span>
        </div>
      </div>
    `;
    const sel = el.querySelector('#outreach-stage-select');
    const status = el.querySelector('#outreach-stage-status');
    sel.addEventListener('change', async () => {
      status.textContent = 'saving…'; status.style.color = '#8b949e';
      try {
        await setStage(parcel.pin, sel.value);
        status.textContent = 'saved'; status.style.color = '#3fb950';
      } catch (e) {
        status.textContent = 'error'; status.style.color = '#f85149';
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
          <div>
            <label for="cm-template">Template</label><br>
            <select id="cm-template">
              ${templates.map((t, i) => `<option value="${i}">${escapeHtml(t.label || t.name)}</option>`).join('')}
            </select>
          </div>
          <div>
            <label for="cm-from">From</label><br>
            <input type="text" id="cm-from" value="${escapeHtml(senderAddress || '')}" disabled />
          </div>
          <div>
            <label for="cm-to">To</label><br>
            <input type="email" id="cm-to" value="${escapeHtml(contact && contact.email || '')}" />
          </div>
          <div>
            <label for="cm-subject">Subject</label><br>
            <input type="text" id="cm-subject" value="" />
          </div>
          <div>
            <label for="cm-body">Body</label><br>
            <textarea id="cm-body"></textarea>
          </div>
        </div>
        <div class="outreach-modal-foot">
          <span class="outreach-modal-error" id="cm-error"></span>
          <button type="button" class="btn" id="cm-cancel">Cancel</button>
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
        window.dispatchEvent(new CustomEvent('outreach:refresh',
                                              { detail: { pin: parcel.pin } }));
      } catch (e) {
        errSpan.textContent = e.message || 'send failed';
        sendBtn.disabled = false; sendBtn.textContent = 'Send';
      }
    });
  }

  // Wire the compose button trigger from renderContactSection.
  window.__outreachOpenCompose = openComposeModal;
})();
