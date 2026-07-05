/* Columbia-1 front-end. Talks to the FastAPI backend:
   GET  /api/faculties            -> status chips + enable/disable panels
   POST /api/{faculty}/generate   -> { job_id }
   GET  /api/job/{id}             -> live status (polled)
   POST /api/job/{id}/cancel
   /api/library …                 -> list / stream / delete outputs
   The two generate panels share one job-runner; the library is independent. */

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

/* ── theme ─────────────────────────────────────────────────────────────── */
const root = document.documentElement;
$('#theme').addEventListener('click', () => {
  const cur = root.getAttribute('data-theme') || 'dark';
  root.setAttribute('data-theme', cur === 'dark' ? 'light' : 'dark');
});

/* ── tabs ──────────────────────────────────────────────────────────────── */
$$('.tab').forEach(t => t.addEventListener('click', () => {
  $$('.tab').forEach(x => x.classList.remove('active'));
  $$('.panel').forEach(x => x.classList.remove('active'));
  t.classList.add('active');
  $('#panel-' + t.dataset.tab).classList.add('active');
  if (t.dataset.tab === 'library') loadLibrary();
}));

/* ── helpers ───────────────────────────────────────────────────────────── */
function fmtDur(s) {
  if (s == null) return '';
  s = Math.round(s);
  const m = Math.floor(s / 60), sec = String(s % 60).padStart(2, '0');
  return `${m}:${sec}`;
}
function fmtSize(b) {
  if (!b) return '';
  const mb = b / 1048576;
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.round(b / 1024)} KB`;
}
function fmtWhen(iso) {
  if (!iso) return '';
  try { return new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }); }
  catch { return iso; }
}

/* ── faculty status ────────────────────────────────────────────────────── */
async function loadFaculties() {
  let data;
  try { data = await (await fetch('/api/faculties')).json(); }
  catch { return; }
  $('#ver').textContent = 'v' + (data.version || '');
  const row = $('#statusRow');
  row.innerHTML = '';
  for (const f of data.faculties) {
    const on = f.status.available;
    const chip = document.createElement('span');
    chip.className = 'chip ' + (on ? 'on' : 'off');
    chip.title = f.status.reason + (f.status.detail ? ' — ' + f.status.detail : '');
    chip.innerHTML = `<span class="dot"></span>${f.name}` +
      (on && f.status.device ? `<span class="dev">${f.status.device}</span>` : '');
    row.appendChild(chip);
    applyAvailability(f);
  }
}

function applyAvailability(f) {
  const on = f.status.available;
  const tabBadge = $(`.tab[data-tab="${f.id}"] .badge`);
  $(`.tab[data-tab="${f.id}"]`).innerHTML =
    f.name + (on ? '' : ' <span class="badge">OFFLINE</span>');
  const box = $('#' + f.id + '-offline');
  const goBtn = $('#' + (f.id === 'speech' ? 'sp' : 'dub') + '-go');
  if (on) { if (box) box.innerHTML = ''; if (goBtn) goBtn.disabled = false; return; }
  if (goBtn) goBtn.disabled = true;
  if (box) box.innerHTML =
    `<div class="offline-note"><b>${f.name} engine offline.</b> ${f.status.reason}.` +
    (f.status.detail ? ` <code>${f.status.detail}</code>` : '') +
    ` Point Columbia-1 at the repo in <code>columbia.yaml</code> and restart.</div>`;
}

/* ── generic job runner (shared by speech + dubbing) ───────────────────── */
function makeRunner(prefix, faculty, buildBody, renderResult) {
  const els = {
    job: $('#' + prefix + '-job'), stage: $('#' + prefix + '-job-stage'),
    count: $('#' + prefix + '-job-count'), bar: $('#' + prefix + '-bar'),
    barFill: $('#' + prefix + '-bar span'), err: $('#' + prefix + '-job-error'),
    result: $('#' + prefix + '-result'), go: $('#' + prefix + '-go'),
    cancel: $('#' + prefix + '-cancel'),
  };
  let jobId = null, timer = null;

  function reset() {
    els.job.classList.add('show');
    els.err.hidden = true; els.err.textContent = '';
    els.result.hidden = true; els.result.innerHTML = '';
    els.bar.classList.add('indet'); els.barFill.style.right = '100%';
    els.count.textContent = '';
    els.go.disabled = true; els.cancel.hidden = false;
  }
  function finish() {
    els.go.disabled = false; els.cancel.hidden = true;
    if (timer) { clearTimeout(timer); timer = null; }
  }

  async function poll() {
    let job;
    try { job = await (await fetch('/api/job/' + jobId)).json(); }
    catch { timer = setTimeout(poll, 1200); return; }
    els.stage.textContent = job.stage || job.status;
    if (job.total > 0) {
      els.bar.classList.remove('indet');
      const pct = Math.min(100, Math.round(job.done / job.total * 100));
      els.barFill.style.right = (100 - pct) + '%';
      els.count.textContent = `${job.done} / ${job.total}`;
    }
    if (job.status === 'done') {
      els.bar.classList.remove('indet'); els.barFill.style.right = '0%';
      els.count.textContent = 'Complete';
      els.result.hidden = false;
      renderResult(els.result, job.result);
      finish();
    } else if (job.status === 'failed') {
      els.bar.classList.remove('indet'); els.barFill.style.right = '100%';
      els.err.hidden = false;
      els.err.textContent = (job.error === 'Cancelled.' ? 'Cancelled.' : 'Failed: ' + (job.error || 'unknown error'));
      els.stage.textContent = job.error === 'Cancelled.' ? 'Cancelled' : 'Failed';
      finish();
    } else {
      timer = setTimeout(poll, 800);
    }
  }

  async function submit(body) {
    reset();
    try {
      const res = await fetch('/api/' + faculty + '/generate', { method: 'POST', body });
      if (!res.ok) {
        const msg = await res.text();
        throw new Error(res.status === 503 ? 'engine offline' : msg || res.statusText);
      }
      jobId = (await res.json()).job_id;
      poll();
    } catch (e) {
      els.err.hidden = false; els.err.textContent = 'Could not start: ' + e.message;
      els.stage.textContent = 'Failed'; els.bar.classList.remove('indet');
      finish();
    }
  }

  els.cancel.addEventListener('click', () => {
    if (jobId) fetch('/api/job/' + jobId + '/cancel', { method: 'POST' });
  });

  return { submit, els, buildBody };
}

/* ── SPEECH panel ──────────────────────────────────────────────────────── */
(function speechPanel() {
  let source = 'text';
  $$('#speech-seg button').forEach(b => b.addEventListener('click', () => {
    source = b.dataset.v;
    $$('#speech-seg button').forEach(x => x.classList.toggle('on', x === b));
    $$('.src').forEach(el => el.hidden = !el.classList.contains('src-' + source));
  }));

  const speed = $('#sp-speed');
  speed.addEventListener('input', () => $('#sp-speed-val').textContent = (+speed.value).toFixed(2) + '×');

  wireDrop($('#sp-drop'), $('#sp-file'), $('#sp-file-name'));

  const runner = makeRunner('sp', 'speech', null, (box, r) => {
    box.innerHTML =
      `<audio controls autoplay src="${r.stream}"></audio>
       <div class="meta"><span class="title">${escapeHtml(r.title || 'Narration')}</span>
       <span class="tel">${fmtDur(r.seconds)}</span>
       <a class="btn btn--ghost" href="${r.download}" download>Download</a></div>`;
  });

  $('#speech-form').addEventListener('submit', e => {
    e.preventDefault();
    const fd = new FormData();
    fd.append('input_type', source === 'file' ? 'file' : source);
    fd.append('text', $('#sp-text').value);
    fd.append('url', $('#sp-url').value);
    fd.append('engine', $('#sp-engine').value);
    fd.append('voice', $('#sp-voice').value || 'en-US-AriaNeural');
    fd.append('speed', speed.value);
    const f = $('#sp-file').files[0];
    if (source === 'file' && f) fd.append('file', f);
    runner.submit(fd);
  });
})();

/* ── DUBBING panel ─────────────────────────────────────────────────────── */
(function dubbingPanel() {
  wireDrop($('#dub-vdrop'), $('#dub-video'), $('#dub-vname'));
  wireDrop($('#dub-sdrop'), $('#dub-srt'), $('#dub-sname'));
  wireDrop($('#dub-rdrop'), $('#dub-ref'), $('#dub-rname'));

  const atempo = $('#dub-atempo');
  atempo.addEventListener('input', () => $('#dub-atempo-val').textContent = (+atempo.value).toFixed(2) + '×');

  const runner = makeRunner('dub', 'dubbing', null, (box, r) => {
    box.innerHTML =
      `<video controls src="${r.stream}"></video>
       <div class="meta"><span class="title">${escapeHtml(r.title || 'Dub')}</span>
       <span class="tel">${fmtDur(r.seconds)} · ${r.cues ?? 0} cues${r.capped ? ' · ' + r.capped + ' flagged' : ''}</span>
       <a class="btn btn--ghost" href="${r.download}" download>Download</a></div>`;
  });

  $('#dub-form').addEventListener('submit', e => {
    e.preventDefault();
    const v = $('#dub-video').files[0], s = $('#dub-srt').files[0];
    const err = $('#dub-job-error');
    if (!v || !s) {
      $('#dub-job').classList.add('show');
      $('#dub-bar').classList.remove('indet');
      err.hidden = false; err.textContent = 'Choose a video and its .srt subtitle first.';
      $('#dub-job-stage').textContent = 'Waiting';
      $('#dub-result').hidden = true;
      return;
    }
    const fd = new FormData();
    fd.append('video', v);
    fd.append('srt', s);
    const ref = $('#dub-ref').files[0];
    if (ref) fd.append('reference', ref);
    fd.append('translate', $('#dub-translate').checked);
    fd.append('preview', $('#dub-preview').checked);
    fd.append('max_atempo', atempo.value);
    runner.submit(fd);
  });
})();

/* ── LIBRARY panel ─────────────────────────────────────────────────────── */
async function loadLibrary() {
  const list = $('#lib-list'), count = $('#lib-count');
  list.innerHTML = '<div class="empty"><div class="big">Loading…</div></div>';
  let data;
  try { data = await (await fetch('/api/library')).json(); }
  catch { list.innerHTML = '<div class="empty"><div class="big">Could not load the library.</div></div>'; return; }
  const items = data.items || [];
  count.textContent = items.length ? `${items.length} item${items.length > 1 ? 's' : ''}` : '';
  if (!items.length) {
    list.innerHTML = '<div class="empty"><div class="big">Nothing here yet.</div>Generate a narration or dub and it will appear here.</div>';
    return;
  }
  list.innerHTML = '';
  for (const it of items) {
    const el = document.createElement('div');
    el.className = 'lib-item';
    const sub = [fmtDur(it.seconds), fmtSize(it.size), fmtWhen(it.created)].filter(Boolean).join('  ·  ');
    el.innerHTML =
      `<div class="lib-row">
        <span class="kind-badge ${it.kind}">${it.kind}</span>
        <span class="name" title="${escapeHtml(it.title)}">${escapeHtml(it.title)}</span>
        <span class="sub">${sub}</span>
        <span class="lib-actions">
          <button class="play">Play</button>
          <a class="btn-a" href="${it.url}" download><button>Download</button></a>
          <button class="del">Delete</button>
        </span>
      </div>
      <div class="lib-player" hidden></div>`;
    const player = $('.lib-player', el);
    $('.play', el).addEventListener('click', () => {
      if (player.hidden) {
        player.hidden = false;
        player.innerHTML = it.kind === 'video'
          ? `<video controls autoplay src="${it.url}"></video>`
          : `<audio controls autoplay src="${it.url}"></audio>`;
        $('.play', el).textContent = 'Hide';
      } else {
        player.hidden = true; player.innerHTML = ''; $('.play', el).textContent = 'Play';
      }
    });
    $('.del', el).addEventListener('click', async () => {
      if (!confirm(`Delete "${it.title}"? This removes the file from disk.`)) return;
      await fetch('/api/library/' + encodeURIComponent(it.name), { method: 'DELETE' });
      loadLibrary();
    });
    list.appendChild(el);
  }
}
$('#lib-refresh').addEventListener('click', loadLibrary);

/* ── shared UI bits ────────────────────────────────────────────────────── */
function wireDrop(drop, input, nameEl) {
  const show = () => {
    const f = input.files[0];
    if (f) { nameEl.hidden = false; nameEl.textContent = f.name; }
    else { nameEl.hidden = true; nameEl.textContent = ''; }
  };
  input.addEventListener('change', show);
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.remove('over');
  }));
  drop.addEventListener('drop', e => {
    if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; show(); }
  });
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ── boot ──────────────────────────────────────────────────────────────── */
loadFaculties();
