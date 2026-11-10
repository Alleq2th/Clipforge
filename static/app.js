/* ============================================================================
   ClipForge UI
   ----------------------------------------------------------------------------
   No framework, no build step. State lives in one object; every render is a
   pure function of it. That keeps the flow debuggable and the bundle tiny.
   ========================================================================== */

const $ = (id) => document.getElementById(id);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
};

const state = {
  options: null,
  sources: [],
  config: {
    aspect_ratio: '9:16',
    reframe_mode: 'track',
    duration: '30to60',
    count: 5,
    caption_style: 'bold_yellow',
    focus: 'viral',
  },
  jobId: null,
  poll: null,
  clips: [],
  restyleStyle: null,
};

/* ------------------------------------------------------------------ helpers */
function show(view) {
  ['view-hero', 'view-builder', 'view-running', 'view-results'].forEach((id) => {
    $(id).hidden = id !== view;
  });
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function fmtDuration(sec) {
  const s = Math.round(sec || 0);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m}m ${r}s` : `${m}m`;
}

function scoreClass(v) {
  if (v >= 85) return 'high';
  if (v >= 70) return 'mid';
  return 'low';
}

/* --------------------------------------------------------------- bootstrap */
async function boot() {
  try {
    const [optsRes, healthRes] = await Promise.all([
      fetch('/api/options'),
      fetch('/api/health'),
    ]);
    state.options = await optsRes.json();
    const health = await healthRes.json();
    renderEnginePill(health);
    buildControls();
  } catch (e) {
    $('engine-text').textContent = 'API unreachable';
    $('engine-dot').classList.add('off');
  }
}

function renderEnginePill(health) {
  const mr = health.engines?.moment_rank || {};
  const llm = Object.entries(mr).filter(([, v]) => v).map(([k]) => k);
  const asr = health.engines?.asr || 'unknown';

  if (llm.length) {
    $('engine-text').textContent = `MomentRank · ${llm[0]}`;
    $('engine-dot').classList.remove('off');
  } else {
    $('engine-text').textContent = 'MomentRank · no key';
    $('engine-dot').classList.add('off');
  }
  $('footer-engines').textContent = `ASR ${asr} · reframe ${(health.engines?.reframe || []).join(' / ')}`;
}

/* ---------------------------------------------------------------- controls */
function buildControls() {
  const o = state.options;

  // aspect
  $('seg-aspect').innerHTML = '';
  o.aspects.forEach((a) => {
    const b = el('button', 'seg-item', `${a.label}<span class="sub">${a.note}</span>`);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(state.config.aspect_ratio === a.id));
    b.onclick = () => {
      state.config.aspect_ratio = a.id;
      buildControls();
    };
    $('seg-aspect').appendChild(b);
  });

  // reframe
  $('seg-reframe').innerHTML = '';
  o.reframe_modes.forEach((m) => {
    const [name, note] = m.label.split(' - ');
    const b = el('button', 'seg-item', `${name}<span class="sub">${note || ''}</span>`);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(state.config.reframe_mode === m.id));
    b.onclick = () => {
      state.config.reframe_mode = m.id;
      buildControls();
    };
    $('seg-reframe').appendChild(b);
  });

  // duration
  $('seg-duration').innerHTML = '';
  o.durations.forEach((d) => {
    const b = el('button', 'seg-item', d.label);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(state.config.duration === d.id));
    b.onclick = () => {
      state.config.duration = d.id;
      buildControls();
    };
    $('seg-duration').appendChild(b);
  });

  // count
  $('seg-count').innerHTML = '';
  [3, 5, 8, 10, 15, 20].forEach((n) => {
    const b = el('button', 'seg-item', String(n));
    b.type = 'button';
    b.setAttribute('aria-pressed', String(state.config.count === n));
    b.onclick = () => {
      state.config.count = n;
      buildControls();
    };
    $('seg-count').appendChild(b);
  });
  $('count-hint').textContent = `${state.config.count} per video`;

  // captions
  $('seg-captions').innerHTML = '';
  o.caption_styles.forEach((s) => {
    const b = el('button', 'swatch', `<i style="background:${s.swatch}"></i>${s.label}`);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(state.config.caption_style === s.id));
    b.onclick = () => {
      state.config.caption_style = s.id;
      buildControls();
    };
    $('seg-captions').appendChild(b);
  });

  // focus
  $('seg-focus').innerHTML = '';
  o.focus.forEach((f) => {
    const b = el('button', 'seg-item', f.label);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(state.config.focus === f.id));
    b.onclick = () => {
      state.config.focus = f.id;
      buildControls();
    };
    $('seg-focus').appendChild(b);
  });

  updateEstimate();
}

function updateEstimate() {
  const n = state.sources.length;
  const c = state.config.count;
  if (!n) {
    $('estimate').textContent = 'Add at least one source to continue.';
    $('btn-generate').disabled = true;
    return;
  }
  $('btn-generate').disabled = false;
  $('estimate').textContent =
    `${n} source${n > 1 ? 's' : ''} · up to ${n * c} clips · ${state.config.aspect_ratio} · ${state.config.reframe_mode.toUpperCase()}`;
}

/* ----------------------------------------------------------------- sources */
function renderSources() {
  const list = $('source-list');
  list.innerHTML = '';
  state.sources.forEach((s, i) => {
    const row = el('div', 'source-item');
    row.innerHTML = `
      <span class="idx">${String(i + 1).padStart(2, '0')}</span>
      <span class="url" title="${s}">${s}</span>
      <button class="rm" aria-label="Remove">&times;</button>`;
    row.querySelector('.rm').onclick = () => {
      state.sources.splice(i, 1);
      renderSources();
    };
    list.appendChild(row);
  });
  $('source-hint').textContent = `${state.sources.length} / ${state.options?.max_sources || 5}`;
  updateEstimate();
}

function addSource() {
  const input = $('url-input');
  const v = input.value.trim();
  if (!v) return;
  const max = state.options?.max_sources || 5;
  if (state.sources.length >= max) {
    alert(`Maximum ${max} sources per job.`);
    return;
  }
  if (state.sources.includes(v)) {
    input.value = '';
    return;
  }
  state.sources.push(v);
  input.value = '';
  renderSources();
}

/* -------------------------------------------------------------------- run */
const STAGES = [
  { id: 'ingest', label: 'Ingest' },
  { id: 'asr', label: 'Transcribe' },
  { id: 'scenes', label: 'Scenes' },
  { id: 'moments', label: 'MomentRank' },
  { id: 'render', label: 'Render' },
];

function renderStageRail(currentStage, stageProgress) {
  const rail = $('stage-rail');
  rail.innerHTML = '';
  const order = STAGES.map((s) => s.id);
  const curIdx = order.indexOf(currentStage);

  STAGES.forEach((s, i) => {
    let st = 'pending';
    if (curIdx === -1) st = 'pending';
    else if (i < curIdx) st = 'done';
    else if (i === curIdx) st = 'active';
    if (stageProgress?.[s.id] === 100) st = 'done';

    const row = el('div', 'stage-row');
    row.dataset.state = st;
    row.innerHTML = `
      <span class="stage-icon">${st === 'done' ? '✓' : i + 1}</span>
      <span class="stage-name">${s.label}</span>
      <span class="stage-msg">${st === 'done' ? 'done' : st === 'active' ? 'running' : ''}</span>`;
    rail.appendChild(row);
  });
}

async function startJob() {
  if (!state.sources.length) return;
  $('btn-generate').disabled = true;

  try {
    const res = await fetch('/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sources: state.sources, ...state.config }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not start the job.');

    state.jobId = data.id;
    state.clips = [];
    $('run-title').textContent = `${state.sources.length} source${state.sources.length > 1 ? 's' : ''}`;
    $('run-clips').innerHTML = '';
    $('run-bar').style.width = '0%';
    $('run-pct').textContent = '0%';
    $('run-message').textContent = 'Starting';
    renderStageRail('ingest', {});
    show('view-running');
    pollJob();
  } catch (e) {
    $('btn-generate').disabled = false;
    alert(e.message);
  }
}

function pollJob() {
  clearInterval(state.poll);
  state.poll = setInterval(async () => {
    if (!state.jobId) return;
    try {
      const res = await fetch(`/api/jobs/${state.jobId}`);
      if (!res.ok) return;
      const j = await res.json();

      $('run-bar').style.width = `${j.progress || 0}%`;
      $('run-pct').textContent = `${j.progress || 0}%`;
      $('run-message').textContent = j.message || j.stage || '';
      renderStageRail(j.stage, j.stage_progress);

      if (j.clips?.length) renderRunClips(j.clips);

      if (j.done) {
        clearInterval(state.poll);
        state.clips = j.clips || [];
        if (j.status === 'cancelled') {
          show('view-builder');
          $('btn-generate').disabled = false;
          return;
        }
        renderResults(j);
      }
    } catch (e) {
      /* transient network error - keep polling */
    }
  }, 1200);
}

function renderRunClips(clips) {
  const wrap = $('run-clips');
  const ok = clips.filter((c) => c.ok);
  if (!ok.length) return;
  wrap.innerHTML = `<p class="eyebrow mb-3">Ready so far (${ok.length})</p>`;
  const grid = el('div', 'clip-grid');
  ok.forEach((c, i) => grid.appendChild(clipCard(c, i)));
  wrap.appendChild(grid);
}

/* ------------------------------------------------------------------ clips */
function clipCard(c, i) {
  const card = el('div', 'clip');
  const ratioClass =
    c.aspect_ratio === '1:1' ? 'sq' : c.aspect_ratio === '16:9' ? 'wide' : c.aspect_ratio === '4:5' ? 'feed' : '';

  const hook = Number(c.hook_score) || 0;
  const viral = Number(c.virality_score) || 0;
  const scored = c.scored !== false && (hook > 0 || viral > 0);

  card.innerHTML = `
    <div class="clip-media ${ratioClass}">
      <video src="${c.stream_url}" controls preload="metadata" playsinline></video>
      <span class="clip-rank">#${String(i + 1).padStart(2, '0')}</span>
      <span class="clip-mode">${(c.reframe_mode || 'crop').toUpperCase()}</span>
    </div>
    <div class="clip-body">
      <div class="clip-title">${escapeHtml(c.title || 'Untitled clip')}</div>
      ${
        scored
          ? `<div class="scores">
              <div class="score">
                <div class="score-top"><span class="score-label">Hook</span><span class="score-val">${hook}</span></div>
                <div class="score-bar"><i style="width:${hook}%"></i></div>
              </div>
              <div class="score">
                <div class="score-top"><span class="score-label">Viral</span><span class="score-val">${viral}</span></div>
                <div class="score-bar"><i style="width:${viral}%"></i></div>
              </div>
            </div>`
          : `<div class="alert alert-warn tiny">Not AI-scored — no MomentRank key configured. Clips are evenly spaced.</div>`
      }
      ${c.reason ? `<div class="clip-reason">${escapeHtml(c.reason)}</div>` : ''}
      <div class="clip-meta">
        <span class="chip-mini">${fmtDuration(c.duration)}</span>
        <span class="chip-mini">${escapeHtml(c.category || 'clip')}</span>
        <span class="chip-mini">${escapeHtml(c.caption_style || '')}</span>
      </div>
      <div class="clip-actions">
        <a class="btn btn-primary btn-sm" href="${c.download_url}" download>Download</a>
        <button class="btn btn-quiet btn-sm copy-btn">Copy caption</button>
      </div>
    </div>`;

  card.querySelector('.copy-btn').onclick = (e) => {
    const text = [c.title, c.caption, (c.hashtags || []).join(' ')].filter(Boolean).join('\n\n');
    navigator.clipboard.writeText(text).then(() => {
      e.target.textContent = 'Copied';
      setTimeout(() => (e.target.textContent = 'Copy caption'), 1600);
    });
  };
  return card;
}

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderResults(job) {
  const ok = (job.clips || []).filter((c) => c.ok);
  const blocked = (job.clips || []).filter((c) => c.render_unavailable);
  const failed = (job.clips || []).filter((c) => !c.ok && !c.render_unavailable);

  if (ok.length) {
    $('results-title').textContent = `${ok.length} clip${ok.length === 1 ? '' : 's'} ready`;
  } else if (blocked.length) {
    $('results-title').textContent = 'Clips selected — rendering unavailable here';
  } else {
    $('results-title').textContent = 'No clips ready';
  }

  $('results-sub').textContent = failed.length
    ? `${failed.length} clip${failed.length === 1 ? '' : 's'} failed to render — see below.`
    : ok.length
      ? 'Download any clip, or restyle the captions and re-render.'
      : '';

  const grid = $('clip-grid');
  grid.innerHTML = '';
  ok.forEach((c, i) => grid.appendChild(clipCard(c, i)));

  const empty = $('results-empty');
  if (ok.length) {
    empty.hidden = true;
  } else if (blocked.length) {
    empty.hidden = false;
    empty.innerHTML = `<div class="empty">
      <h3>The pipeline worked — the encoder is the blocker</h3>
      <p>${escapeHtml(blocked[0].error)}</p>
      <p class="tiny muted mt-4">${blocked.length} clip${blocked.length === 1 ? '' : 's'} selected and validated:
        ${blocked.map((c) => `${Math.round(c.duration)}s`).join(', ')}.</p>
    </div>`;
  } else {
    empty.hidden = false;
    empty.innerHTML = `<div class="empty">
      <h3>No clips came out</h3>
      <p>${escapeHtml(job.message || 'The pipeline finished without producing a clip.')}</p>
    </div>`;
  }

  show('view-results');
}

/* ---------------------------------------------------------------- restyle */
function openRestyle() {
  const panel = $('restyle-panel');
  panel.classList.toggle('hidden');
  if (panel.classList.contains('hidden')) return;

  const wrap = $('restyle-swatches');
  wrap.innerHTML = '';
  (state.options?.caption_styles || []).forEach((s) => {
    const b = el('button', 'swatch', `<i style="background:${s.swatch}"></i>${s.label}`);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(state.restyleStyle === s.id));
    b.onclick = () => {
      state.restyleStyle = s.id;
      openRestyle();
      panel.classList.remove('hidden');
    };
    wrap.appendChild(b);
  });
}

async function applyRestyle() {
  if (!state.restyleStyle || !state.jobId) return;
  const btn = $('btn-restyle-apply');
  btn.disabled = true;
  btn.textContent = 'Re-rendering…';
  try {
    const res = await fetch(`/api/jobs/${state.jobId}/restyle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ caption_style: state.restyleStyle }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Re-render failed.');
    $('restyle-panel').classList.add('hidden');
    state.clips = [];
    $('run-clips').innerHTML = '';
    show('view-running');
    pollJob();
  } catch (e) {
    alert(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Re-render';
  }
}

/* ---------------------------------------------------------------- history */
async function showHistory() {
  try {
    const res = await fetch('/api/jobs');
    const { jobs } = await res.json();
    if (!jobs?.length) {
      alert('No jobs yet.');
      return;
    }
    const lines = jobs
      .slice(0, 10)
      .map((j) => `${j.id}  ${j.status.padEnd(10)}  ${(j.clips || []).filter((c) => c.ok).length} clips  ${j.message || ''}`);
    alert('Recent jobs\n\n' + lines.join('\n'));
  } catch (e) {
    alert('Could not load history.');
  }
}

/* ------------------------------------------------------------------ wiring */
$('btn-start').onclick = () => {
  show('view-builder');
  renderSources();
};
$('btn-back-hero').onclick = () => show('view-hero');
$('btn-add-url').onclick = addSource;
$('url-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    addSource();
  }
});
$('btn-generate').onclick = startJob;
$('btn-cancel').onclick = async () => {
  if (!state.jobId) return;
  await fetch(`/api/jobs/${state.jobId}/cancel`, { method: 'POST' });
  clearInterval(state.poll);
  show('view-builder');
  $('btn-generate').disabled = false;
};
$('btn-new').onclick = () => {
  state.sources = [];
  state.jobId = null;
  state.clips = [];
  renderSources();
  show('view-builder');
};
$('btn-restyle').onclick = openRestyle;
$('btn-restyle-close').onclick = () => $('restyle-panel').classList.add('hidden');
$('btn-restyle-apply').onclick = applyRestyle;
$('btn-history').onclick = showHistory;

boot();
