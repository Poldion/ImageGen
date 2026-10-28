// Gemeinsame Bausteine fuer index.html und admin.html.

// Dialog mit Eingabefeldern statt prompt() - Passwoerter bleiben verdeckt.
// fields: [{name, label, type:'password'|'text', minlength}]; liefert {name: wert} oder null.
// check(values) darf einen Fehlertext zurueckgeben, dann bleibt der Dialog offen.
function askDialog({title, text = '', fields = [], ok = 'OK', danger = false, check = null}) {
  return new Promise(resolve => {
    const d = document.createElement('dialog');
    d.className = 'dlg';
    d.innerHTML = `<form method="dialog">
      <h3></h3><p class="dlg-text"></p>
      ${fields.map((f, i) => `<label class="dlg-l" for="dlg-f${i}"></label>
        <input id="dlg-f${i}" type="${f.type || 'text'}" autocomplete="${f.type === 'password' ? 'new-password' : 'off'}"
               autocapitalize="none" spellcheck="false">`).join('')}
      <div class="dlg-err"></div>
      <div class="dlg-btns"><button type="button" class="btn" value="cancel">${tr('Abbrechen')}</button>
        <button type="submit" class="btn ${danger ? 'danger' : 'primary-btn'}" value="ok"></button></div>
    </form>`;
    d.querySelector('h3').textContent = title;
    d.querySelector('.dlg-text').textContent = text;
    if (!text) d.querySelector('.dlg-text').remove();
    fields.forEach((f, i) => { d.querySelector(`label[for=dlg-f${i}]`).textContent = f.label; });
    d.querySelector('[value=ok]').textContent = ok;
    const finish = v => { d.close(); d.remove(); resolve(v); };
    d.querySelector('[value=cancel]').onclick = () => finish(null);
    d.addEventListener('cancel', e => { e.preventDefault(); finish(null); });
    d.querySelector('form').addEventListener('submit', e => {
      e.preventDefault();
      const vals = {};
      fields.forEach((f, i) => { vals[f.name] = d.querySelector(`#dlg-f${i}`).value; });
      for (const [i, f] of fields.entries()) {
        if (f.minlength && vals[f.name].length < f.minlength) {
          d.querySelector('.dlg-err').textContent = tr('{label}: mindestens {n} Zeichen', {label: f.label, n: f.minlength});
          d.querySelector(`#dlg-f${i}`).focus(); return;
        }
      }
      const err = check && check(vals);
      if (err) { d.querySelector('.dlg-err').textContent = err; return; }
      finish(vals);
    });
    document.body.appendChild(d);
    d.showModal();
    const first = d.querySelector('input'); if (first) first.focus();
  });
}

// Eigenes Passwort aendern (fuer alle Benutzer).
async function changeOwnPassword(api) {
  const v = await askDialog({
    title: tr('Passwort ändern'), ok: tr('Ändern'),
    text: tr('Andere angemeldete Geräte werden dabei abgemeldet.'),
    fields: [{name: 'old', label: tr('Aktuelles Passwort'), type: 'password'},
             {name: 'neu', label: tr('Neues Passwort'), type: 'password', minlength: 8},
             {name: 'neu2', label: tr('Neues Passwort wiederholen'), type: 'password'}],
    check: v => v.neu !== v.neu2 ? tr('Die neuen Passwörter stimmen nicht überein') : null,
  });
  if (!v) return;
  try {
    await api('/api/me/password', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                   body: JSON.stringify({old: v.old, new: v.neu})});
    toast(tr('Passwort geändert'));
  } catch (e) { toast(e.message, true); }
}

// API-Schluessel des eigenen Kontos (fuer andere Programme, z.B. Open WebUI oder Skripte)
async function manageApiKeys(api) {
  const d = document.createElement('dialog');
  d.className = 'dlg';
  const base = location.origin;
  d.innerHTML = `<form method="dialog">
    <h3>${tr('API-Schlüssel')}</h3>
    <p class="dlg-text">${tr('Damit nutzen andere Programme deine Workflows – mit denselben Stilsätzen, Optionen und „Ausformulieren“ wie hier. Rechte und Bilder wie dein Konto.')}</p>
    <div class="keys"></div>
    <div class="newkey" style="display:none"><label class="dlg-l">${tr('Neuer Schlüssel – nur jetzt sichtbar, gleich kopieren:')}</label>
      <input readonly class="keyval"></div>
    <details class="dlg-text"><summary>${tr('So benutzt du die API')}</summary>
      <pre class="api-help"></pre></details>
    <div class="dlg-err"></div>
    <div class="dlg-btns"><button type="button" class="btn" value="new">${tr('Neuer Schlüssel')}</button>
      <button type="button" class="btn primary-btn" value="close">${tr('Fertig')}</button></div>
  </form>`;
  d.querySelector('.api-help').textContent = LANG === 'en'
? `Workflows:  GET  ${base}/api/v1/workflows
Generate:   POST ${base}/api/v1/generate
  {"workflow": "gezeichnet_dnd", "prompt": "a dwarf cleric", "wait": 300}
  optional: enhance (default true), image (Base64), width, height,
            seconds, motion, action, seed, count, variables
Status:     GET  ${base}/api/v1/jobs/<id>
Image:      the "url" from the result, with the same key
Header:     Authorization: Bearer <key>

OpenAI-compatible (e.g. Open WebUI → Images → OpenAI):
  base URL ${base}/v1, model = workflow name (e.g. realistisch_qwen21)`
: `Workflows:  GET  ${base}/api/v1/workflows
Erzeugen:   POST ${base}/api/v1/generate
  {"workflow": "gezeichnet_dnd", "prompt": "ein Zwergenkleriker", "wait": 300}
  optional: enhance (Standard true), image (Base64), width, height,
            seconds, motion, action, seed, count, variables
Status:     GET  ${base}/api/v1/jobs/<id>
Bild:       die "url" aus dem Ergebnis, mit demselben Schlüssel
Header:     Authorization: Bearer <Schlüssel>

OpenAI-kompatibel (z. B. Open WebUI → Bilder → OpenAI):
  Basis-URL ${base}/v1, Modell = Workflow-Name (z. B. realistisch_qwen21)`;
  const err = m => { d.querySelector('.dlg-err').textContent = m || ''; };
  const fmt = t => t ? new Date(t * 1000).toLocaleDateString(LOCALE) : tr('nie');
  async function load() {
    const keys = await api('/api/me/keys');
    const box = d.querySelector('.keys');
    box.innerHTML = keys.length ? '' : '<p class="dlg-text">' + tr('Noch keine Schlüssel.') + '</p>';
    for (const k of keys) {
      const row = document.createElement('div');
      row.className = 'keyrow';
      row.innerHTML = '<span><b></b><small></small></span><button type="button" class="btn danger">' + tr('Löschen') + '</button>';
      row.querySelector('b').textContent = k.name;
      row.querySelector('small').textContent = ' ' + tr('{hint} · erstellt {created} · zuletzt {used}', {hint: k.hint, created: fmt(k.created), used: fmt(k.last_used)});
      row.querySelector('button').onclick = async () => {
        try { await api('/api/me/keys/' + k.id, {method: 'DELETE'}); load(); } catch (e) { err(e.message); }
      };
      box.appendChild(row);
    }
  }
  d.querySelector('[value=new]').onclick = async () => {
    const v = await askDialog({title: tr('Neuer API-Schlüssel'), ok: tr('Erzeugen'),
                               fields: [{name: 'name', label: tr('Wofür? (z. B. Open WebUI)')}]});
    if (!v) return;
    try {
      const r = await api('/api/me/keys', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                           body: JSON.stringify({name: v.name})});
      d.querySelector('.newkey').style.display = '';
      const inp = d.querySelector('.keyval'); inp.value = r.key; inp.select();
      if (navigator.clipboard) navigator.clipboard.writeText(r.key).then(() => toast(tr('Schlüssel kopiert')), () => {});
      load();
    } catch (e) { err(e.message); }
  };
  const close = () => { d.close(); d.remove(); };
  d.querySelector('[value=close]').onclick = close;
  d.addEventListener('cancel', e => { e.preventDefault(); close(); });
  document.body.appendChild(d);
  d.showModal();
  try { await load(); } catch (e) { err(e.message); }
}

// Kurze Meldung unten (statt alert)
function toast(msg, isErr = false) {
  let t = document.getElementById('toast');
  if (!t) { t = document.createElement('div'); t.id = 'toast'; document.body.appendChild(t); }
  t.textContent = msg; t.className = 'show' + (isErr ? ' err' : '');
  clearTimeout(t._h); t._h = setTimeout(() => { t.className = ''; }, isErr ? 6000 : 2500);
}

// Grossansicht ueber den Verlauf: Zurueck-Taste (Android) schliesst sie statt die App
function lightbox(el) {
  const open = () => {
    if (el.style.display !== 'flex') { el.style.display = 'flex'; history.pushState({lb: 1}, ''); }
  };
  const close = () => { if (el.style.display === 'flex') history.back(); };
  window.addEventListener('popstate', () => { el.style.display = 'none'; el.querySelectorAll('video').forEach(v => v.pause()); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
  return {open, close};
}

// ZIP herunterladen. Ohne body: GET, der Browser laedt direkt (auch grosse ZIPs, ohne
// alles im Speicher zu halten). Mit body (Auswahl): POST, Ergebnis als Datei speichern.
async function downloadZip(url, body = null, msg = tr('ZIP wird erstellt …')) {
  const save = (href, name) => {
    const a = document.createElement('a');
    a.href = href; if (name) a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
  };
  toast(msg);
  if (!body) { save(url); return; }
  try {
    const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    if (!r.ok) { const j = await r.json().catch(() => ({})); toast(j.detail || tr('Fehler {status}', {status: r.status}), true); return; }
    const name = ((r.headers.get('content-disposition') || '').match(/filename="?([^";]+)"?/) || [])[1] || 'bilder.zip';
    const href = URL.createObjectURL(await r.blob());
    save(href, name);
    setTimeout(() => URL.revokeObjectURL(href), 60000);
    toast(tr('{n} Bilder heruntergeladen', {n: r.headers.get('x-image-count') || ''}).trim());
  } catch (e) { toast(e.message, true); }
}

function fmtSize(b) { return b > 1e9 ? (b / 1e9).toFixed(1) + ' GB' : Math.max(1, Math.round(b / 1e6)) + ' MB'; }

// Videos erkennen und als Kachel/Player darstellen
const isVideo = i => /\.(mp4|webm)$/i.test(i && i.filename || '');

// Inhalt einer Galerie-Kachel: Bild (Vorschau) oder Video (erstes Bild, ▶)
function mediaThumb(container, i, url, previewUrl) {
  if (isVideo(i)) {
    const v = document.createElement('video');
    v.muted = true; v.playsInline = true; v.preload = 'metadata'; v.src = url + '#t=0.1';
    container.prepend(v);
    const p = document.createElement('div'); p.className = 'play'; p.textContent = '▶';
    container.appendChild(p);
  } else {
    const im = document.createElement('img');
    im.loading = 'lazy'; im.alt = ''; im.src = previewUrl;
    container.prepend(im);
  }
}

// Bild oder Video in einem Paar aus <img> und <video> anzeigen
function showMedia(imgEl, vidEl, i, url) {
  const vid = isVideo(i);
  imgEl.style.display = vid ? 'none' : '';
  vidEl.style.display = vid ? '' : 'none';
  if (vid) { vidEl.src = url; vidEl.play().catch(() => {}); }
  else { vidEl.pause(); vidEl.removeAttribute('src'); imgEl.src = url; }
}
