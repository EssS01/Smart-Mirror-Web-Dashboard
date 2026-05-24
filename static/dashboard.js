const $ = (id) => document.getElementById(id);
const labForm = $('lab-form');
let selectedUser = '';
let dbUsersByName = {};
let lastStatus = null;
let currentTheme = 'dark';
let homeCalendarCursor = new Date();
let selectedHomeCalendarDate = null;
let currentHomeCalendarPayload = null;

const VIEW_MODE = document.body?.dataset.viewMode || (location.pathname.includes('/display') ? 'display' : 'controller');
const IS_DISPLAY = VIEW_MODE === 'display';
const CLIENT_ID = `${VIEW_MODE}-${Math.random().toString(36).slice(2)}-${Date.now()}`;
let applyingRemote = false;
let lastRemoteVersion = 0;
let remotePushTimer = null;
let remoteScrollTimer = null;
let pendingRemoteScroll = false;

function activeTabId() {
  return document.querySelector('.tab-content.active')?.id || 'home-tab';
}

function applyTheme(theme = 'dark', broadcast = false) {
  // Theme switching was removed by request. Keep the dashboard locked to black/white.
  currentTheme = 'dark';
  document.documentElement.dataset.theme = 'dark';
}

function toggleTheme() {
  applyTheme('dark', false);
}


function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value ?? '---';
}

function setValue(id, value) {
  const el = $(id);
  if (el) el.value = value ?? '';
}

function setCardState(id, state) {
  const el = $(id);
  if (!el) return;
  el.classList.remove('state-ready', 'state-processing', 'state-fail');
  if (state === 'ready' || state === 'normal') el.classList.add('state-ready');
  else if (state === 'fail' || state === 'critical' || state === 'high' || state === 'low') el.classList.add('state-fail');
  else el.classList.add('state-processing');
}

function message(text) {
  setText('face-message', text);
  if (!applyingRemote) scheduleRemoteMetaSync({message: text || ''});
}

function setPdfStatus(text, state = '') {
  const el = $('pdf-upload-status');
  if (!el) return;
  el.textContent = text || 'No PDF uploaded yet. This step is optional.';
  el.classList.remove('ok', 'warn', 'error');
  if (state) el.classList.add(state);
}

function applyPdfRemoteStatus(status = {}) {
  if (!status || typeof status !== 'object') return;
  if (status.selected_label) setText('pdf-selected-label', status.selected_label);
  if (status.message) setPdfStatus(status.message, status.state || '');
}

async function api(path, options = {}) {
  try {
    const res = await fetch(path, {
      headers: options.body instanceof FormData ? {} : {'Content-Type': 'application/json'},
      ...options,
    });
    const data = await res.json().catch(() => ({ok: false, message: 'Invalid backend response'}));
    if (!res.ok && !data.message) data.message = `HTTP ${res.status}`;
    if (!res.ok) data.ok = false;
    return data;
  } catch (err) {
    return {ok: false, message: `Upload/backend connection failed: ${err.message || err}`};
  }
}

function remoteFormSnapshot() {
  return {
    selected_user: $('user-select')?.value || '',
    name: $('name-input')?.value || '',
    dob: $('dob-input')?.value || '',
    age: $('age-output')?.value || '',
    gender: $('gender-input')?.value || '',
    symptoms: getSelectedSymptoms(),
    notes: $('notes-input')?.value || '',
    lab_values: labValuesFromFormSafe(),
  };
}

function remotePatientSnapshot() {
  return {
    selected_user: $('user-select')?.value || '',
    name: $('name-input')?.value || '',
    dob: $('dob-input')?.value || '',
    age: $('age-output')?.value || '',
    gender: $('gender-input')?.value || '',
    symptoms: getSelectedSymptoms(),
    notes: $('notes-input')?.value || '',
  };
}

function labValuesFromFormSafe() {
  try {
    return labValuesFromForm();
  } catch (_) {
    return {};
  }
}

function remoteCalendarSnapshot() {
  return {
    year: homeCalendarCursor.getFullYear(),
    month: homeCalendarCursor.getMonth() + 1,
    selected_date: selectedHomeCalendarDate,
    note_text: $('home-note-text')?.value || '',
  };
}

function remoteScrollSnapshot() {
  const doc = document.documentElement;
  const maxY = Math.max(0, doc.scrollHeight - window.innerHeight);
  const maxX = Math.max(0, doc.scrollWidth - window.innerWidth);
  return {
    active_tab: activeTabId(),
    x: Math.round(window.scrollX || 0),
    y: Math.round(window.scrollY || 0),
    ratio_x: maxX > 0 ? (window.scrollX || 0) / maxX : 0,
    ratio_y: maxY > 0 ? (window.scrollY || 0) / maxY : 0,
    max_y: Math.round(maxY),
  };
}

function scheduleRemoteScrollSync() {
  // Any active screen can drive the other screens.
  if (applyingRemote) return;
  pendingRemoteScroll = true;
  clearTimeout(remoteScrollTimer);
  remoteScrollTimer = setTimeout(() => {
    pendingRemoteScroll = false;
    pushRemoteState({active_tab: activeTabId(), scroll: remoteScrollSnapshot()}, false);
  }, 80);
}

function applyRemoteScroll(scroll) {
  if (!scroll || scroll.active_tab !== activeTabId()) return;
  const doc = document.documentElement;
  const maxY = Math.max(0, doc.scrollHeight - window.innerHeight);
  const maxX = Math.max(0, doc.scrollWidth - window.innerWidth);
  const targetY = Math.round(maxY * Number(scroll.ratio_y || 0));
  const targetX = Math.round(maxX * Number(scroll.ratio_x || 0));
  const previousApplying = applyingRemote;
  applyingRemote = true;
  window.scrollTo({left: targetX, top: targetY, behavior: 'auto'});
  setTimeout(() => { applyingRemote = previousApplying; }, 120);
}

function scheduleRemoteSync(extra = {}, delay = 120) {
  // Form/action sync. Includes the current form snapshot on purpose.
  if (applyingRemote) return;
  clearTimeout(remotePushTimer);
  remotePushTimer = setTimeout(() => pushRemoteState(extra, true), delay);
}

function scheduleRemoteMetaSync(extra = {}, delay = 80) {
  // Metadata-only sync for tab/scroll/message updates so stale forms do not overwrite another device.
  if (applyingRemote) return;
  clearTimeout(remotePushTimer);
  remotePushTimer = setTimeout(() => pushRemoteState(extra, false), delay);
}

async function pushRemoteState(extra = {}, includeSnapshot = true) {
  if (applyingRemote) return;
  const payload = {
    source: CLIENT_ID,
    view_mode: VIEW_MODE,
    ...extra,
  };
  if (!('active_tab' in payload)) payload.active_tab = activeTabId();
  if (includeSnapshot) {
    if (!('form' in payload)) payload.form = remoteFormSnapshot();
    if (!('calendar' in payload)) payload.calendar = remoteCalendarSnapshot();
  }
  try {
    const data = await api('/api/remote/state', {method: 'POST', body: JSON.stringify(payload)});
    if (data?.state?.updated_at) lastRemoteVersion = Math.max(lastRemoteVersion, data.state.updated_at);
    setText('remote-pill', 'Remote: controller');
  } catch (_) {
    setText('remote-pill', 'Remote: sync error');
  }
}

async function pollRemoteState() {
  try {
    const data = await api('/api/remote/state');
    const state = data?.state || {};
    const version = Number(state.updated_at || 0);
    const updatedBy = String(state.updated_by || '');
    if (!data.ok || version <= lastRemoteVersion || updatedBy === CLIENT_ID) {
      setText('remote-pill', `Remote: ${VIEW_MODE}`);
      return;
    }
    lastRemoteVersion = version;
    await applyRemoteState(state);
    setText('remote-pill', `Remote: synced from ${state.updated_view || 'other screen'}`);
  } catch (_) {
    setText('remote-pill', 'Remote: disconnected');
  }
}

async function applyRemoteState(state) {
  applyingRemote = true;
  try {
    if (state.theme) applyTheme(state.theme, false);
    if (state.active_tab) activateTab(state.active_tab, false);

    const form = state.form || {};
    if ('selected_user' in form) setValue('user-select', form.selected_user || '');
    if ('name' in form) setValue('name-input', form.name || '');
    if ('dob' in form) setValue('dob-input', form.dob || '');
    if ('gender' in form) setValue('gender-input', form.gender || '');
    if ('symptoms' in form) setSelectedSymptoms(form.symptoms || []);
    if ('notes' in form) setValue('notes-input', form.notes || '');
    if (form.lab_values) fillLabForm(form.lab_values);
    updateAge();

    const cal = state.calendar || {};
    if (cal.year && cal.month) {
      const requested = new Date(Number(cal.year), Number(cal.month) - 1, 1);
      const monthChanged = requested.getFullYear() !== homeCalendarCursor.getFullYear() || requested.getMonth() !== homeCalendarCursor.getMonth();
      homeCalendarCursor = requested;
      if (cal.selected_date) selectedHomeCalendarDate = cal.selected_date;
      if (monthChanged || !currentHomeCalendarPayload) {
        await loadHomeCalendar(homeCalendarCursor.getFullYear(), homeCalendarCursor.getMonth() + 1);
      } else {
        renderSelectedHomeDay();
      }
      if ('note_text' in cal) setValue('home-note-text', cal.note_text || '');
    }

    if (state.message) setText('face-message', state.message);
    if (state.capture) renderCapture(state.capture);
    if (state.lab_values) fillLabForm(state.lab_values);
    if (state.lab_analysis) showLabAnalysis(state.lab_analysis);
    if (state.pdf_status) applyPdfRemoteStatus(state.pdf_status);
    if (state.summary) renderSummary(state.summary);
    if (state.scroll) {
      requestAnimationFrame(() => applyRemoteScroll(state.scroll));
    }
    // Refresh backend-driven live data immediately after applying a remote action.
    pollStatus();
    pollDbSummary();
  } finally {
    applyingRemote = false;
  }
}

function calculateAgeFromDob(dobString) {
  if (!dobString) return '';
  const dob = new Date(`${dobString}T00:00:00`);
  if (Number.isNaN(dob.getTime())) return '';
  const today = new Date();
  let age = today.getFullYear() - dob.getFullYear();
  const m = today.getMonth() - dob.getMonth();
  if (m < 0 || (m === 0 && today.getDate() < dob.getDate())) age--;
  return age >= 0 ? String(age) : '';
}

function updateAge() {
  setValue('age-output', calculateAgeFromDob($('dob-input')?.value));
}

function getSelectedSymptoms() {
  const select = $('symptoms-select');
  if (!select) return [];
  return [...select.selectedOptions].map((opt) => opt.value).filter(Boolean);
}

function setSelectedSymptoms(symptoms = []) {
  const select = $('symptoms-select');
  if (!select) return;
  const wanted = new Set((symptoms || []).map((item) => String(item)));
  [...select.options].forEach((opt) => {
    opt.selected = wanted.has(opt.value);
  });
  renderSymptomTags();
}

function renderSymptomTags() {
  const wrap = $('symptom-tags');
  if (!wrap) return;
  const symptoms = getSelectedSymptoms();
  wrap.innerHTML = '';
  if (!symptoms.length) {
    const empty = document.createElement('span');
    empty.className = 'tag empty-tag';
    empty.textContent = 'No symptoms selected';
    wrap.appendChild(empty);
    return;
  }
  symptoms.forEach((symptom) => {
    const tag = document.createElement('span');
    tag.className = 'tag';
    tag.textContent = symptom;
    wrap.appendChild(tag);
  });
}

function formatSymptoms(symptoms = []) {
  return symptoms && symptoms.length ? symptoms.join(', ') : '---';
}

function weatherIcon(condition = '') {
  const text = String(condition || '').toLowerCase();
  if (text.includes('clear') || text.includes('sun')) return '☼';
  if (text.includes('cloud') || text.includes('overcast')) return '☁';
  if (text.includes('rain') || text.includes('drizzle')) return '☂';
  if (text.includes('storm') || text.includes('thunder')) return '⚡';
  if (text.includes('fog') || text.includes('mist')) return '≋';
  if (text.includes('snow')) return '❄';
  return '☁';
}

function pad2(value) {
  return String(value).padStart(2, '0');
}

function toLocalIsoDate(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

function parseIsoLocal(iso) {
  const [y, m, d] = String(iso || '').split('-').map(Number);
  if (!y || !m || !d) return new Date();
  return new Date(y, m - 1, d);
}

function greetingForNow() {
  const hour = new Date().getHours();
  if (hour >= 5 && hour < 12) return 'Good morning';
  if (hour >= 12 && hour < 17) return 'Good afternoon';
  if (hour >= 17 && hour < 21) return 'Good evening';
  return 'Good night';
}

function shortDateLabel(iso) {
  const d = parseIsoLocal(iso);
  return d.toLocaleDateString(undefined, {weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'});
}

function renderSelectedHomeDay() {
  if (!currentHomeCalendarPayload || !selectedHomeCalendarDate) return;
  const day = (currentHomeCalendarPayload.days || []).find((item) => item.date === selectedHomeCalendarDate);
  setText('home-note-date', shortDateLabel(selectedHomeCalendarDate));
  setValue('home-note-text', day?.note || '');

  const eventWrap = $('home-day-events');
  if (eventWrap) {
    eventWrap.innerHTML = '';
    const events = day?.events || [];
    if (!events.length) {
      eventWrap.textContent = 'No synced calendar events';
    } else {
      events.forEach((ev) => {
        const chip = document.createElement('span');
        chip.className = 'mirror-event-chip';
        chip.textContent = ev.title;
        eventWrap.appendChild(chip);
      });
    }
  }

  document.querySelectorAll('.mirror-day').forEach((btn) => {
    btn.classList.toggle('selected', btn.dataset.date === selectedHomeCalendarDate);
  });
}

function renderHomeCalendar(payload) {
  if (!payload?.ok) return;
  currentHomeCalendarPayload = payload;
  setText('home-calendar-month-label', payload.month_name || 'Calendar');

  const grid = $('home-calendar-grid');
  if (!grid) return;
  grid.innerHTML = '';

  const blanks = Number(payload.first_weekday || 0);
  for (let i = 0; i < blanks; i++) {
    const blank = document.createElement('div');
    blank.className = 'mirror-day blank';
    grid.appendChild(blank);
  }

  const todayIso = toLocalIsoDate(new Date());
  const days = payload.days || [];
  if (!selectedHomeCalendarDate || !days.some((day) => day.date === selectedHomeCalendarDate)) {
    selectedHomeCalendarDate = days.find((day) => day.date === todayIso)?.date || days[0]?.date || todayIso;
  }

  days.forEach((day) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'mirror-day';
    btn.dataset.date = day.date;
    if (day.is_today) btn.classList.add('today');
    if ((day.events || []).length) btn.classList.add('has-event');
    if (day.note) btn.classList.add('has-note');
    if (day.date === selectedHomeCalendarDate) btn.classList.add('selected');
    btn.innerHTML = `<span class="day-number">${day.day}</span><span class="day-dots"><i></i><b></b></span>`;
    btn.title = [day.date, ...(day.events || []).map((ev) => ev.title), day.note ? `Note: ${day.note}` : ''].filter(Boolean).join('\n');
    btn.addEventListener('click', () => {
      selectedHomeCalendarDate = day.date;
      renderSelectedHomeDay();
      scheduleRemoteMetaSync({calendar: remoteCalendarSnapshot()});
    });
    grid.appendChild(btn);
  });

  const upcoming = $('home-upcoming-events');
  if (upcoming) {
    const events = payload.upcoming_events || [];
    upcoming.innerHTML = '';
    if (events.length) {
      const title = document.createElement('div');
      title.className = 'mirror-upcoming-title';
      title.textContent = 'Upcoming synced events';
      upcoming.appendChild(title);
      events.slice(0, 4).forEach((ev) => {
        const row = document.createElement('div');
        row.className = 'mirror-upcoming-row';
        row.innerHTML = `<span>${shortDateLabel(ev.date)}</span><strong>${ev.title}</strong>`;
        upcoming.appendChild(row);
      });
    } else {
      upcoming.textContent = 'No upcoming synced events for this year';
    }
  }

  renderSelectedHomeDay();
}

async function loadHomeCalendar(year, month) {
  const data = await api(`/api/calendar/month?year=${year}&month=${month}`);
  if (data.ok) renderHomeCalendar(data);
  else setText('home-note-status', data.message || 'Calendar unavailable');
}

function changeHomeMonth(offset) {
  homeCalendarCursor = new Date(homeCalendarCursor.getFullYear(), homeCalendarCursor.getMonth() + offset, 1);
  selectedHomeCalendarDate = null;
  loadHomeCalendar(homeCalendarCursor.getFullYear(), homeCalendarCursor.getMonth() + 1);
  scheduleRemoteMetaSync({calendar: remoteCalendarSnapshot()});
}

async function saveHomeNote() {
  if (!selectedHomeCalendarDate) return;
  const data = await api('/api/calendar/note', {
    method: 'POST',
    body: JSON.stringify({date: selectedHomeCalendarDate, note: $('home-note-text')?.value || ''}),
  });
  setText('home-note-status', data.message || (data.ok ? 'Saved.' : 'Error.'));
  await loadHomeCalendar(homeCalendarCursor.getFullYear(), homeCalendarCursor.getMonth() + 1);
  scheduleRemoteMetaSync({calendar: remoteCalendarSnapshot(), message: data.message || 'Calendar note saved'});
}

function patientPayload() {
  updateAge();
  return {
    name: currentName(),
    dob: $('dob-input')?.value || null,
    gender: $('gender-input')?.value || null,
    symptoms: getSelectedSymptoms(),
  };
}

function normalizeUsers(users) {
  const select = $('user-select');
  if (!select) return;
  const current = select.value || selectedUser;
  const normalized = [];
  dbUsersByName = {};

  (users || []).forEach((item) => {
    const record = typeof item === 'string' ? {name: item} : item;
    if (!record || !record.name) return;
    normalized.push(record.name);
    dbUsersByName[record.name] = record;
  });

  const unique = [...new Set(normalized)].sort((a, b) => a.localeCompare(b));
  select.innerHTML = '';
  const empty = document.createElement('option');
  empty.value = '';
  empty.textContent = unique.length ? 'Select a user' : 'No saved users';
  select.appendChild(empty);
  unique.forEach((user) => {
    const opt = document.createElement('option');
    opt.value = user;
    opt.textContent = user;
    select.appendChild(opt);
  });
  if (current && unique.includes(current)) select.value = current;
}

function applySelectedUserRecord(record) {
  if (!record) return;
  setValue('name-input', record.name || '');
  if (record.dob) setValue('dob-input', record.dob);
  if (record.gender) setValue('gender-input', record.gender);
  setSelectedSymptoms(record.symptom_tags || []);
  updateAge();
}

function currentName() {
  return ($('name-input')?.value || $('user-select')?.value || '').trim();
}

function labValuesFromForm() {
  const values = {};
  const fd = new FormData(labForm);
  for (const [key, val] of fd.entries()) values[key] = val === '' ? null : val;
  values.age = calculateAgeFromDob($('dob-input')?.value) || null;
  values.gender = $('gender-input')?.value || null;
  return values;
}

function fillLabForm(values = {}) {
  for (const [key, val] of Object.entries(values || {})) {
    const input = labForm.elements[key];
    if (input) input.value = val ?? '';
  }
}

function showLabAnalysis(analysis) {
  if (!analysis) return;
  setText('lab-title', analysis.title || 'Lab Analysis');
  setText('lab-coverage', analysis.coverage_text || 'Coverage: ---');
  setText('lab-detail', analysis.detail_text || 'No abnormal entered values were flagged.');
  setText('lab-advice', analysis.advice_text || analysis.detail_text || '');
  setText('lab-note', analysis.note_text || 'Screening support only - not a diagnosis.');
  setText('lab-state', analysis.state || 'processing');
  setCardState('lab-card', analysis.state || 'processing');
}

async function loadLabForSelected() {
  const name = currentName();
  if (!name) return;
  const data = await api(`/api/lab/${encodeURIComponent(name)}`);
  if (data.ok) fillLabForm(data.values);
  const dbData = await api(`/api/db/user/${encodeURIComponent(name)}`);
  if (dbData.ok) {
    if (dbData.user) applySelectedUserRecord(dbData.user);
    if (dbData.lab?.values) fillLabForm(dbData.lab.values);
    if (dbData.lab?.analysis) showLabAnalysis(dbData.lab.analysis);
    renderCapture(dbData.capture);
  }
}

function renderCapture(capture) {
  const steps = capture?.steps || {};
  const camera = steps.camera;
  const temp = steps.temperature;
  const heart = steps.heart;
  setText('step-camera', camera ? `1. Camera: ${camera.status_text || 'captured'} | ${camera.identity_text || '---'} | pics: ${camera.pics_saved ?? '---'}` : '1. Camera: not captured');
  setText('step-temp', temp ? `2. Temperature: ${temp.text || '---'} | ${temp.range_text || '---'}` : '2. Temperature: not captured');
  setText('step-heart', heart ? `3. Heart: ${heart.bpm_text || '---'} | ${heart.spo2_text || 'SpO2 ---'} | ${heart.status || 'captured'}` : '3. Heart: not captured');

  if (heart) setText('workflow-state', 'Ready to save');
  else if (temp) setText('workflow-state', 'Heart next');
  else if (camera) setText('workflow-state', 'Temperature next');
  else if (steps.started) setText('workflow-state', 'Camera next');
  else setText('workflow-state', 'Not started');
}

function formatDemographics(user) {
  if (!user) return '---';
  const parts = [];
  if (user.dob) parts.push(user.dob);
  if (user.age !== null && user.age !== undefined) parts.push(`${user.age} yrs`);
  if (user.gender) parts.push(user.gender);
  return parts.length ? parts.join(' / ') : '---';
}

function renderSummary(summary) {
  if (!summary?.ok) return;
  normalizeUsers(summary.users || []);

  const totals = summary.totals || {};
  setText('db-total-users', totals.users ?? 0);
  setText('db-total-sessions', totals.sessions ?? 0);
  setText('db-total-labs', totals.with_lab_results ?? 0);

  const user = summary.selected_user || dbUsersByName[currentName()] || null;
  setText('summary-user', user?.name || currentName() || '---');
  setText('summary-demographics', formatDemographics(user));
  setText('summary-symptoms', formatSymptoms(user?.symptom_tags || []));
  setText('summary-pics', user ? `${user.pics_saved ?? 0} saved/taken` : '---');

  const latest = summary.latest_selected_session || summary.latest_session || null;
  setText('summary-temp', latest?.temperature_text ? `${latest.temperature_text} | ${latest.temperature_range || '---'}` : '---');
  setText('summary-heart', latest?.heart_bpm_text ? `${latest.heart_bpm_text} | SpO2 ${latest.spo2_text || '---'}` : '---');
  setText('summary-lab', latest ? (latest.lab_available ? 'Lab values saved' : 'No PDF/lab saved - allowed') : 'PDF optional / no lab required');
  setText('summary-saved-at', latest?.saved_at || '---');

  setText('record-name', latest?.name || '---');
  setText('record-camera', latest ? `${latest.camera_status || '---'} | ${latest.recognized_identity || '---'}` : '---');
  setText('record-temp', latest?.temperature_text || '---');
  setText('record-heart', latest ? `${latest.heart_bpm_text || '---'} / ${latest.spo2_text || '---'}` : '---');
  setText('record-symptoms', latest ? formatSymptoms(latest.symptom_tags || latest.user_symptom_tags || []) : '---');
  setText('record-lab', latest ? (latest.lab_available ? 'Available' : 'Not uploaded/entered') : '---');

  if (summary.selected_lab?.values) fillLabForm(summary.selected_lab.values);
  if (summary.selected_lab?.analysis) showLabAnalysis(summary.selected_lab.analysis);
  renderCapture(summary.active_capture);
}

async function pollDbSummary() {
  const name = currentName();
  const query = name ? `?name=${encodeURIComponent(name)}` : '';
  const summary = await api(`/api/db/summary${query}`);
  renderSummary(summary);
}

async function pollStatus() {
  try {
    const data = await api('/api/status');
    lastStatus = data;
    $('server-pill').textContent = data.ok ? 'Backend: connected' : 'Backend: error';

    setText('time-label', data.datetime?.time);
    setText('date-label', data.datetime?.date);
    setText('zone-label', data.datetime?.zone || 'Local time');
    setText('summary-time-label', data.datetime?.time);
    setText('summary-date-label', data.datetime?.date);
    setText('summary-zone-label', data.datetime?.zone || 'Local time');
    setText('home-greeting', greetingForNow());
    setText('home-time-label', data.datetime?.time_short || data.datetime?.time || '--:--');
    setText('home-date-label', data.datetime?.date);

    setText('weather-city', data.weather?.city);
    setText('weather-temp', data.weather?.temp);
    setText('weather-condition', data.weather?.condition);
    setText('weather-feels', `Feels like: ${data.weather?.feels || '---'}`);
    setText('summary-weather-city', data.weather?.city);
    setText('summary-weather-temp', data.weather?.temp);
    setText('summary-weather-condition', data.weather?.condition);
    setText('summary-weather-feels', `Feels like: ${data.weather?.feels || '---'}`);
    setText('home-weather-city', data.weather?.city);
    setText('home-weather-temp', data.weather?.temp);
    setText('home-weather-condition', data.weather?.condition);
    setText('home-weather-feels', `Feels like: ${data.weather?.feels || '---'}`);
    setText('home-weather-icon', weatherIcon(data.weather?.condition));

    const s = data.sensors || {};
    const temp = s.temperature || {};
    setText('temp-value', temp.text || '---');
    setText('temp-range', temp.range_text || 'Current Range: ---');
    setText('temp-status', temp.ok ? 'Reading stable' : (temp.text || 'Offline'));
    setCardState('temp-card', temp.ok ? temp.range_state : 'processing');

    const heart = s.heart || {};
    setText('heart-bpm', heart.bpm_text || '---');
    setText('heart-range', heart.range_text || 'Current Range: ---');
    setText('heart-spo2', `SpO2: ${heart.spo2_text || '---'}`);
    setText('spo2-range', heart.spo2_range_text || 'SpO2 Range: ---');
    setText('heart-status', heart.status || 'Waiting for data');
    setCardState('heart-card', heart.ok ? (heart.range_state === 'normal' && heart.spo2_range_state === 'normal' ? 'ready' : heart.range_state) : 'processing');

    const color = s.color || {};
    setText('color-dominant', color.dominant || '---');
    setText('color-rgb', `R: ${color.red ?? '---'}   G: ${color.green ?? '---'}   B: ${color.blue ?? '---'}`);
    setText('color-status', color.ok ? 'Reading stable' : (color.dominant || 'Offline'));
    setCardState('color-card', color.ok ? 'ready' : 'processing');

    const fusion = s.fusion || {};
    setText('fusion-title', fusion.title || 'Collecting Data');
    setText('fusion-score', fusion.score_text || 'Fusion Score: ---');
    setText('fusion-detail', fusion.detail_text || 'Waiting for data.');
    setText('fusion-note', fusion.note_text || 'Screening summary only - not a medical diagnosis');
    setCardState('fusion-card', fusion.state || 'processing');
    setCardState('summary-card', fusion.state || 'processing');

    const c = data.camera || {};
    setText('camera-state', c.camera_text || 'Camera');
    setText('face-identity', c.identity_text || '---');
    setText('face-status', c.status_text || 'Waiting for face');
    setText('face-distance', c.distance_text || '---');
    setText('camera-detail', c.detail_text || 'Vision stack not loaded');
  } catch (err) {
    $('server-pill').textContent = 'Backend: disconnected';
  }
}

async function workflowPost(path) {
  const payload = patientPayload();
  if (!payload.name) return message('Enter or select a user first.');
  const data = await api(path, {method: 'POST', body: JSON.stringify(payload)});
  message(data.message || (data.ok ? 'Done.' : 'Error.'));
  if (data.capture) renderCapture(data.capture);
  await pollDbSummary();
  scheduleRemoteSync({message: data.message || '', capture: data.capture || null, form: remoteFormSnapshot(), scroll: remoteScrollSnapshot()});
}

function activateTab(tabId, broadcast = true) {
  const isHome = tabId === 'home-tab';
  document.querySelectorAll('.tab-btn').forEach((btn) => btn.classList.toggle('active', btn.dataset.tab === tabId));
  document.querySelectorAll('.tab-content').forEach((tab) => tab.classList.toggle('active', tab.id === tabId));
  document.body.classList.toggle('home-mode', isHome);
  document.documentElement.classList.toggle('home-mode-root', isHome);
  if (broadcast && !applyingRemote) scheduleRemoteMetaSync({active_tab: tabId, scroll: remoteScrollSnapshot()});
}

document.querySelectorAll('.tab-btn').forEach((btn) => btn.addEventListener('click', () => activateTab(btn.dataset.tab)));
window.addEventListener('scroll', scheduleRemoteScrollSync, {passive: true});
$('home-calendar-prev')?.addEventListener('click', () => changeHomeMonth(-1));
$('home-calendar-next')?.addEventListener('click', () => changeHomeMonth(1));
$('home-save-note-btn')?.addEventListener('click', saveHomeNote);

$('dob-input')?.addEventListener('input', () => { updateAge(); scheduleRemoteSync({form: remotePatientSnapshot()}); });
$('gender-input')?.addEventListener('change', () => scheduleRemoteSync({form: remotePatientSnapshot()}));
$('notes-input')?.addEventListener('input', () => scheduleRemoteSync({form: remotePatientSnapshot()}, 250));
$('home-note-text')?.addEventListener('input', () => scheduleRemoteMetaSync({calendar: remoteCalendarSnapshot()}, 250));
$('symptoms-select')?.addEventListener('change', () => { renderSymptomTags(); scheduleRemoteSync({form: remotePatientSnapshot()}); });
labForm?.addEventListener('input', () => scheduleRemoteSync({}, 250));

$('user-select').addEventListener('change', () => {
  selectedUser = $('user-select').value;
  const record = dbUsersByName[selectedUser];
  if (record) applySelectedUserRecord(record);
  else setValue('name-input', selectedUser);
  loadLabForSelected();
  pollDbSummary();
  scheduleRemoteSync({form: remotePatientSnapshot()});
});

$('name-input')?.addEventListener('input', () => scheduleRemoteSync({form: remotePatientSnapshot()}, 250));
$('name-input')?.addEventListener('change', () => {
  selectedUser = currentName();
  loadLabForSelected();
  pollDbSummary();
  scheduleRemoteSync({form: remotePatientSnapshot()});
});

$('refresh-btn').addEventListener('click', async () => {
  const data = await api('/api/face/refresh', {method: 'POST', body: JSON.stringify({})});
  message(data.message || 'Done.');
  await pollDbSummary();
});

$('register-btn').addEventListener('click', async () => {
  const data = await api('/api/face/register', {method: 'POST', body: JSON.stringify({name: currentName()})});
  message(data.message || 'Done.');
});

$('update-btn').addEventListener('click', async () => {
  const data = await api('/api/face/update', {method: 'POST', body: JSON.stringify({name: currentName()})});
  message(data.message || 'Done.');
});

$('delete-btn').addEventListener('click', async () => {
  const name = currentName();
  if (!name) return message('Pick a user to delete.');
  if (!confirm(`Delete ${name} from FaceDB and local DB?`)) return;
  const data = await api('/api/face/delete', {method: 'POST', body: JSON.stringify({name})});
  message(data.message || 'Done.');
  await pollDbSummary();
});

$('start-flow-btn').addEventListener('click', () => workflowPost('/api/workflow/start'));
$('capture-camera-btn').addEventListener('click', () => workflowPost('/api/workflow/capture_camera'));
$('capture-temp-btn').addEventListener('click', () => workflowPost('/api/workflow/capture_temperature'));
$('capture-heart-btn').addEventListener('click', () => workflowPost('/api/workflow/capture_heart'));

$('save-db-btn').addEventListener('click', async () => {
  const payload = {...patientPayload(), lab_values: labValuesFromForm(), notes: $('notes-input')?.value || ''};
  if (!payload.name) return message('Enter or select a user first.');
  const data = await api('/api/workflow/save', {method: 'POST', body: JSON.stringify(payload)});
  message(data.message || (data.ok ? 'Saved.' : 'Error.'));
  if (data.summary) renderSummary(data.summary);
  await pollDbSummary();
  scheduleRemoteSync({message: data.message || '', summary: data.summary || null, form: remoteFormSnapshot(), scroll: remoteScrollSnapshot()});
});

$('save-lab-btn').addEventListener('click', async () => {
  const name = currentName();
  if (!name) return message('Pick or enter a user first.');
  const payload = {...patientPayload(), values: labValuesFromForm()};
  const data = await api(`/api/lab/${encodeURIComponent(name)}`, {method: 'POST', body: JSON.stringify(payload)});
  message(data.message || 'Lab results saved.');
  if (data.analysis) showLabAnalysis(data.analysis);
  await pollDbSummary();
  scheduleRemoteSync({message: data.message || 'Lab results saved.', lab_values: data.values || labValuesFromForm(), lab_analysis: data.analysis || null, form: remoteFormSnapshot()});
});

$('analyze-lab-btn').addEventListener('click', async () => {
  const name = currentName();
  if (!name) return message('Pick or enter a user first.');
  const payload = {...patientPayload(), values: labValuesFromForm()};
  await api(`/api/lab/${encodeURIComponent(name)}`, {method: 'POST', body: JSON.stringify(payload)});
  const data = await api(`/api/lab/${encodeURIComponent(name)}/analyze`, {method: 'POST', body: JSON.stringify({})});
  if (data.analysis) showLabAnalysis(data.analysis);
  message(data.ok ? 'Lab screening updated.' : data.message);
  await pollDbSummary();
  scheduleRemoteSync({message: data.ok ? 'Lab screening updated.' : data.message, lab_values: labValuesFromForm(), lab_analysis: data.analysis || null, form: remoteFormSnapshot()});
});

$('ai-lab-btn').addEventListener('click', async () => {
  const name = currentName();
  if (!name) return message('Pick or enter a user first.');
  const payload = {...patientPayload(), values: labValuesFromForm()};
  await api(`/api/lab/${encodeURIComponent(name)}`, {method: 'POST', body: JSON.stringify(payload)});
  const data = await api(`/api/lab/${encodeURIComponent(name)}/predict`, {method: 'POST', body: JSON.stringify({})});
  if (data.analysis) showLabAnalysis(data.analysis);
  message(data.ok ? 'AI lab prediction updated.' : data.message);
  scheduleRemoteSync({message: data.ok ? 'AI lab prediction updated.' : data.message, lab_values: labValuesFromForm(), lab_analysis: data.analysis || null, form: remoteFormSnapshot()});
});

let pdfPickerOpenedByUploadButton = false;

async function uploadSelectedPdf(file) {
  const name = currentName();
  if (!name) {
    setPdfStatus('Enter or select a user name before uploading the PDF.', 'warn');
    message('Pick or enter a user first.');
    return;
  }
  if (!file) {
    setPdfStatus('No PDF selected. Choose a PDF file first.', 'warn');
    return;
  }
  const btn = $('upload-pdf-btn');
  const fd = new FormData();
  fd.append('pdf', file, file.name || 'lab_report.pdf');
  if (btn) btn.disabled = true;
  const uploadMsg = `Uploading PDF: ${file.name || 'lab_report.pdf'} ...`;
  setPdfStatus(uploadMsg, 'warn');
  message(uploadMsg);
  scheduleRemoteMetaSync({pdf_status: {message: uploadMsg, state: 'warn', selected_label: `Selected PDF: ${file.name || 'lab_report.pdf'}.`}});
  const data = await api(`/api/lab/${encodeURIComponent(name)}/upload_pdf`, {method: 'POST', body: fd});
  if (btn) btn.disabled = false;

  if (!data.ok) {
    const err = data.message || 'PDF upload failed.';
    setPdfStatus(err, 'error');
    message(err);
    scheduleRemoteMetaSync({pdf_status: {message: err, state: 'error'}});
    return;
  }

  const msg = data.message || 'PDF processed.';
  setPdfStatus(msg, 'ok');
  message(msg);
  if (data.values) fillLabForm(data.values);
  if (data.analysis) showLabAnalysis(data.analysis);
  await pollDbSummary();
  scheduleRemoteSync({
    message: msg,
    lab_values: data.values || labValuesFromForm(),
    lab_analysis: data.analysis || null,
    pdf_status: {message: msg, state: 'ok', selected_label: `Selected PDF: ${file.name || 'lab_report.pdf'}.`},
    form: remoteFormSnapshot(),
  });
}

$('upload-pdf-btn').addEventListener('click', async () => {
  const input = $('pdf-input');
  const file = input?.files?.[0];
  if (!file) {
    pdfPickerOpenedByUploadButton = true;
    setPdfStatus('Choose a PDF from your phone or laptop.', 'warn');
    input?.click();
    return;
  }
  await uploadSelectedPdf(file);
});

$('pdf-input')?.addEventListener('change', async () => {
  const file = $('pdf-input')?.files?.[0];
  if (file) {
    const selectedMsg = `Selected PDF: ${file.name}`;
    setText('pdf-selected-label', `${selectedMsg}.`);
    setPdfStatus(selectedMsg, 'warn');
    message(selectedMsg);
    scheduleRemoteMetaSync({pdf_status: {message: selectedMsg, state: 'warn', selected_label: `${selectedMsg}.`}});
    if (pdfPickerOpenedByUploadButton) {
      pdfPickerOpenedByUploadButton = false;
      await uploadSelectedPdf(file);
    }
  } else {
    setText('pdf-selected-label', 'On phone: press Upload PDF, choose the file, and it will upload automatically.');
    setPdfStatus('No PDF uploaded yet. This step is optional.');
    pdfPickerOpenedByUploadButton = false;
  }
});

window.addEventListener('load', () => {
  applyTheme('dark', false);
  activateTab('home-tab');
  const today = new Date();
  homeCalendarCursor = new Date(today.getFullYear(), today.getMonth(), 1);
  selectedHomeCalendarDate = toLocalIsoDate(today);
  loadHomeCalendar(homeCalendarCursor.getFullYear(), homeCalendarCursor.getMonth() + 1);
  const todayText = toLocalIsoDate(today);
  if ($('calendar')) $('calendar').value = todayText;
  if ($('summary-calendar')) $('summary-calendar').value = todayText;
  updateAge();
  renderSymptomTags();
  pollStatus();
  pollDbSummary();
  pollRemoteState();
  setInterval(pollRemoteState, 300);
  pushRemoteState({message: `${VIEW_MODE} connected`, active_tab: activeTabId(), scroll: remoteScrollSnapshot()}, false);
  setInterval(pollStatus, 700);
  setInterval(pollDbSummary, 3000);
});
