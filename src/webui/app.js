'use strict';

const $ = id => document.getElementById(id);
const session = document.querySelector('meta[name="mrc-session"]').content;
const state = { devices: [], groupMembers: null, groupError: '', groupFilter: { type: 'all' }, selected: null,
  deployments: [], deploymentOffset: 0, deploymentHasMore: false, deploymentStatus: 'active', selectedDeployment: null, deploymentVersion: 0,
  deploymentListVersion: 0, deploymentLogVersion: 0, version: 0, logVersion: 0 };
let noticeTimer;
let terminalId = null;
let terminalTimer = null;
let forwardTimer = null;

function setTheme(theme) {
  const dark = theme === 'dark';
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  $('theme-toggle').textContent = dark ? 'Light mode' : 'Dark mode';
  $('theme-toggle').setAttribute('aria-pressed', String(dark));
  try { localStorage.setItem('mrc-theme', dark ? 'dark' : 'light'); } catch (_) {}
}

let savedTheme = 'light';
try { savedTheme = localStorage.getItem('mrc-theme') || 'light'; } catch (_) {}
document.documentElement.dataset.theme = savedTheme === 'dark' ? 'dark' : 'light';

function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = String(text);
  if (className) result.className = className;
  return result;
}

function notice(message, error = false) {
  clearTimeout(noticeTimer);
  const box = node('div', undefined, error ? 'notice error' : 'notice');
  box.setAttribute('role', error ? 'alert' : 'status');
  box.append(node('p', message), node('div', undefined, 'notice-bar'));
  $('notices').replaceChildren(box);
  noticeTimer = setTimeout(() => $('notices').replaceChildren(), 5000);
}

async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'X-MRC-Session': session, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'omit', cache: 'no-store',
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status}).`);
  return result;
}

function table(headers, rows) {
  const result = node('table');
  const heading = node('tr');
  for (const label of headers) {
    const cell = node('th', label);
    cell.scope = 'col';
    heading.append(cell);
  }
  const head = node('thead');
  head.append(heading);
  const body = node('tbody');
  for (const values of rows) {
    const row = node('tr');
    for (const value of values) {
      const cell = node('td');
      cell.append(value instanceof Node ? value : document.createTextNode(String(value ?? '—')));
      row.append(cell);
    }
    body.append(row);
  }
  result.append(head, body);
  return result;
}

function renderDevices() {
  const query = $('search').value.trim().toLocaleLowerCase();
  const memberIds = new Set(state.groupMembers?.[state.groupFilter.name] || []);
  const groupedIds = new Set(Object.values(state.groupMembers || {}).flat());
  const now = Date.now();
  const matches = state.devices.filter(device => {
    const inGroup = state.groupFilter.type === 'all' ||
      (state.groupFilter.type === 'group' && memberIds.has(device.id)) ||
      (state.groupFilter.type === 'ungrouped' && !groupedIds.has(device.id));
    const artifact = $('artifact-filter').value;
    const hasArtifact = Boolean(device.artifact);
    const matchesArtifact = artifact === 'all' ||
      (artifact === 'none' ? !hasArtifact : device.artifact === artifact.slice(9));
    const updated = timestamp(device.updated);
    const age = updated === null ? null : now - updated;
    const lastSeen = $('last-seen-filter').value;
    const matchesLastSeen = lastSeen === 'all' ||
      (lastSeen === 'unknown' ? age === null : age !== null &&
        (lastSeen === 'older-30d' ? age >= 30 * 86400000 : age >= 0 && age <= Number(lastSeen) * 86400000));
    return inGroup && matchesArtifact && matchesLastSeen &&
      `${device.name} ${device.id}`.toLocaleLowerCase().includes(query);
  });
  $('count').textContent = `${matches.length} / ${state.devices.length}`;
  $('list-state').hidden = matches.length > 0;
  $('list-state').textContent = state.devices.length ? 'No devices match these filters.' : 'No accepted devices found.';
  $('clear-filters').disabled = !$('search').value.trim() && $('artifact-filter').value === 'all' &&
    $('last-seen-filter').value === 'all' && state.groupFilter.type === 'all';
  $('devices').replaceChildren(...matches.map(device => {
    const item = node('li');
    const button = node('button', undefined, 'device-button');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(state.selected === device.id));
    button.append(node('strong', device.name), node('span', device.id));
    button.addEventListener('click', () => selectDevice(device.id));
    item.append(button);
    return item;
  }));
}

function timestamp(value) {
  const numeric = Number(value);
  if (Number.isFinite(numeric) && value !== '' && value !== null) return numeric < 1e12 ? numeric * 1000 : numeric;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

function renderArtifactFilter() {
  const values = [...new Set(state.devices.map(device => device.artifact).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  const previous = $('artifact-filter').value;
  const options = [node('option', 'All artifacts'), node('option', 'No artifact reported')];
  options[0].value = 'all';
  options[1].value = 'none';
  for (const value of values) {
    const option = node('option', value);
    option.value = `artifact:${value}`;
    options.push(option);
  }
  $('artifact-filter').replaceChildren(...options);
  $('artifact-filter').value = options.some(option => option.value === previous) ? previous : 'all';
}

function renderGroupFilters() {
  $('group-state').textContent = state.groupError || (state.groupMembers === null ? 'Loading groups…' : '');
  const acceptedIds = new Set(state.devices.map(device => device.id));
  const groupedIds = new Set(Object.values(state.groupMembers || {}).flat());
  const filters = [
    { type: 'all', label: 'All devices', count: state.devices.length },
    ...(state.groupMembers === null ? [] : [{
      type: 'ungrouped', label: 'Ungrouped', count: state.devices.filter(device => !groupedIds.has(device.id)).length,
    }]),
    ...Object.entries(state.groupMembers || {}).map(([name, ids]) => ({
      type: 'group', name, label: name, count: new Set(ids.filter(id => acceptedIds.has(id))).size,
    })),
  ];
  $('group-filters').replaceChildren(...filters.map(filter => {
    const button = node('button', undefined, 'group-filter');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(state.groupFilter.type === filter.type &&
      (filter.type !== 'group' || state.groupFilter.name === filter.name)));
    button.append(node('span', filter.label), node('span', filter.count, 'group-count'));
    button.addEventListener('click', () => {
      state.groupFilter = filter.type === 'group' ? { type: 'group', name: filter.name } : { type: filter.type };
      if (state.selected) {
        stopTerminal();
        clearInterval(forwardTimer);
        forwardTimer = null;
        state.selected = null;
        state.version++;
        state.logVersion++;
        $('device').hidden = true;
        $('welcome').hidden = false;
      }
      renderGroupFilters();
      renderDevices();
      renderGroupDeployment();
    });
    return button;
  }));
}

function renderGroupDeployment() {
  const active = state.groupFilter.type === 'group' && state.groupMembers !== null;
  $('group-deploy-card').hidden = !active;
  $('welcome-message').hidden = active;
  if (!active) return;
  const name = state.groupFilter.name;
  const acceptedIds = new Set(state.devices.map(device => device.id));
  const count = new Set((state.groupMembers[name] || []).filter(id => acceptedIds.has(id))).size;
  $('group-deploy-title').textContent = `Deploy to ${name}`;
  $('group-deploy-count').textContent = `${count} accepted device${count === 1 ? '' : 's'} in this group.`;
  $('group-deploy-form').querySelector('button[type="submit"]').disabled = count === 0;
}

async function loadDevices() {
  const result = await api('/api/devices');
  state.devices = result.devices;
  $('server').textContent = [result.account?.organization, result.account?.user, result.server]
    .filter(Boolean).join(' · ');
  renderArtifactFilter();
  if (state.selected && !state.devices.some(device => device.id === state.selected)) {
    stopTerminal();
    clearInterval(forwardTimer);
    forwardTimer = null;
    state.selected = null;
    state.version++;
    state.logVersion++;
    $('device').hidden = true;
    $('welcome').hidden = false;
  }
  renderGroupFilters();
  renderDevices();
  renderGroupDeployment();
}

async function loadOptions() {
  const options = await api('/api/options');
  $('groups').replaceChildren(...options.groups.map(group => {
    const option = node('option');
    option.value = group;
    return option;
  }));
  const previous = $('artifact').value;
  const placeholder = node('option', options.artifacts.length ? 'Choose an artifact…' : 'No artifacts available');
  placeholder.value = '';
  $('artifact').replaceChildren(placeholder, ...options.artifacts.map(artifact => {
    const option = node('option', `${artifact.name} · ${artifact.type}`);
    option.value = artifact.name;
    return option;
  }));
  $('artifact').value = previous;
  const groupPrevious = $('group-deploy-artifact').value;
  const groupPlaceholder = node('option', options.artifacts.length ? 'Choose an artifact…' : 'No artifacts available');
  groupPlaceholder.value = '';
  $('group-deploy-artifact').replaceChildren(groupPlaceholder, ...options.artifacts.map(artifact => {
    const option = node('option', `${artifact.name} · ${artifact.type}`);
    option.value = artifact.name;
    return option;
  }));
  $('group-deploy-artifact').value = groupPrevious;
}

async function loadGroupMembers() {
  state.groupError = '';
  try {
    const result = await api('/api/group-members');
    state.groupMembers = result.group_members;
  } catch (error) {
    state.groupMembers = null;
    state.groupError = `Group filters unavailable: ${error.message}`;
    state.groupFilter = { type: 'all' };
    notice(state.groupError, true);
  }
  renderGroupFilters();
  renderDevices();
  renderGroupDeployment();
}

async function selectDevice(id) {
  stopTerminal();
  clearInterval(forwardTimer);
  forwardTimer = null;
  $('terminal-output').textContent = '';
  $('terminal-status').textContent = 'Not connected';
  $('terminal-command').disabled = true;
  $('terminal-send').disabled = true;
  $('terminal-start').disabled = false;
  $('terminal-stop').disabled = true;
  $('terminal-interrupt').disabled = true;
  state.selected = id;
  const version = ++state.version;
  state.logVersion++;
  const device = state.devices.find(item => item.id === id);
  if (!device) return;
  renderDevices();
  $('welcome').hidden = true;
  $('device').hidden = false;
  $('controls').hidden = true;
  $('log-panel').hidden = true;
  $('detail-state').textContent = 'Loading device details…';
  $('detail-state').className = 'muted';
  $('device-name').textContent = device.name;
  $('device-id').textContent = device.id;
  $('last-contact').textContent = `Last contact: ${device.updated || 'Unknown'}`;
  try {
    const details = await api(`/api/device?id=${encodeURIComponent(id)}`);
    if (version !== state.version) return;
    $('name').value = device.name;
    $('group').value = details.group || '';
    $('tag-form').reset();
    $('detail-state').textContent = '';
    $('controls').hidden = false;
    renderTags(details.tags, id);
    $('inventory').replaceChildren(table(['Attribute', 'Value'], details.inventory));
    renderHistory(details.history, id);
    await refreshForwards();
    forwardTimer = setInterval(refreshForwards, 1000);
  } catch (error) {
    if (version !== state.version) return;
    $('detail-state').textContent = error.message;
    $('detail-state').className = 'error-text';
    notice(error.message, true);
  }
}

function renderTags(tags, id) {
  $('tags').replaceChildren(...tags.map(tag => {
    const row = node('div', undefined, 'tag-row');
    const remove = node('button', 'Remove');
    remove.type = 'button';
    remove.setAttribute('aria-label', `Remove tag ${tag.name}`);
    remove.addEventListener('click', () => {
      if (confirm(`Remove tag "${tag.name}" from this device?`)) {
        mutate(remove, { action: 'tag-remove', device_id: id, name: tag.name });
      }
    });
    row.append(node('code', tag.name), node('span', tag.value), remove);
    return row;
  }));
  if (!tags.length) $('tags').append(node('p', 'No tags yet.', 'muted'));
}

function renderHistory(history, deviceId) {
  if (!history.length) {
    $('history').replaceChildren(node('p', 'No deployments for this device.', 'muted'));
    return;
  }
  const rows = history.map(entry => {
    const deployment = entry.deployment || entry;
    const log = node('button', 'Read log');
    log.type = 'button';
    log.addEventListener('click', async () => {
      const version = ++state.logVersion;
      $('log-panel').hidden = false;
      $('log-title').textContent = `Log · ${deployment.name || deployment.id}`;
      $('log').textContent = 'Loading log…';
      try {
        const result = await api(`/api/log?device_id=${encodeURIComponent(deviceId)}&deployment_id=${encodeURIComponent(deployment.id)}`);
        if (version === state.logVersion) $('log').textContent = result.text;
      } catch (error) {
        if (version === state.logVersion) {
          $('log').textContent = error.message;
          notice(error.message, true);
        }
      }
    });
    return [deployment.name || deployment.id, entry.device?.status || deployment.status || 'Unknown', deployment.created || '—', log];
  });
  $('history').replaceChildren(table(['Deployment', 'Status', 'Created', 'Log'], rows));
}

async function loadDeployments(append = false) {
  const version = ++state.deploymentListVersion;
  const status = state.deploymentStatus;
  const offset = append ? state.deploymentOffset : 0;
  if (!append) {
    state.deploymentOffset = 0;
    state.deploymentHasMore = false;
    $('deployment-list').replaceChildren();
  }
  if (!append || !state.deployments.length) {
    $('deployment-list-state').textContent = 'Loading deployments…';
    $('deployment-list-state').hidden = false;
  }
  try {
    const result = await api(`/api/deployments?status=${status}&offset=${offset}&limit=50`);
    if (version !== state.deploymentListVersion) return;
    state.deployments = append ? [...state.deployments, ...result.deployments] : result.deployments;
    state.deploymentOffset = state.deployments.length;
    state.deploymentHasMore = result.has_more;
    $('deployment-list-state').hidden = state.deployments.length > 0;
    $('deployment-list-state').textContent = status === 'active' ?
      'No active deployments.' : 'No finished deployments.';
    const items = result.deployments.map(deployment => {
      const item = node('li');
      item.dataset.id = String(deployment.id);
      const button = node('button', undefined, 'deployment-item');
      button.type = 'button';
      button.setAttribute('aria-pressed', String(state.selectedDeployment === String(deployment.id)));
      button.append(node('strong', deployment.name || deployment.id),
        node('span', `${deployment.status || 'Unknown'} · ${deployment.device_count ?? 0} devices`),
        node('span', `${deployment.artifact_name || 'Unknown artifact'} · ${deployment.created || 'Unknown date'}`));
      button.addEventListener('click', () => selectDeployment(String(deployment.id)));
      item.append(button);
      return item;
    });
    if (append) $('deployment-list').append(...items);
    else $('deployment-list').replaceChildren(...items);
    $('load-more-deployments').hidden = !state.deploymentHasMore;
    if (state.selectedDeployment && !state.deployments.some(item => String(item.id) === state.selectedDeployment)) {
      state.selectedDeployment = null;
      state.deploymentVersion++;
      $('deployment-detail').hidden = true;
      $('deployment-welcome').hidden = false;
    }
  } catch (error) {
    if (version !== state.deploymentListVersion) return;
    if (!append) {
      $('deployment-list').replaceChildren();
      $('deployment-list-state').hidden = false;
      $('deployment-list-state').textContent = error.message;
    }
    notice(error.message, true);
  }
}

$('load-more-deployments').addEventListener('click', async () => {
  const button = $('load-more-deployments');
  button.disabled = true;
  try { await loadDeployments(true); }
  finally { button.disabled = false; }
});

async function selectDeployment(id) {
  state.selectedDeployment = id;
  const version = ++state.deploymentVersion;
  state.deploymentLogVersion++;
  renderDeploymentsListSelection();
  $('deployment-welcome').hidden = true;
  $('deployment-detail').hidden = false;
  $('deployment-name').textContent = 'Loading deployment…';
  $('deployment-id').textContent = id;
  $('deployment-progress').textContent = '';
  $('deployment-summary').replaceChildren();
  $('deployment-statistics').replaceChildren();
  $('deployment-device-results').replaceChildren(node('p', 'Loading device results…', 'muted'));
  $('deployment-log-panel').hidden = true;
  try {
    const result = await api(`/api/deployment?id=${encodeURIComponent(id)}`);
    if (version !== state.deploymentVersion) return;
    renderDeploymentDetails(result);
  } catch (error) {
    if (version !== state.deploymentVersion) return;
    $('deployment-name').textContent = 'Could not load deployment';
    $('deployment-device-results').replaceChildren(node('p', error.message, 'error-text'));
    notice(error.message, true);
  }
  renderDeploymentsListSelection();
}

function renderDeploymentsListSelection() {
  for (const button of $('deployment-list').querySelectorAll('.deployment-item')) {
    button.setAttribute('aria-pressed', String(button.parentElement.dataset.id === state.selectedDeployment));
  }
}

function renderDeploymentDetails(result) {
  const deployment = result.deployment;
  const statistics = result.statistics || {};
  const total = Number(deployment.device_count || 0);
  const terminal = ['success', 'failure', 'noartifact', 'already-installed', 'already_installed', 'aborted']
    .reduce((sum, key) => sum + Number(statistics[key] || 0), 0);
  const active = ['downloading', 'installing', 'rebooting', 'pause_before_installing',
    'pause_before_rebooting', 'pause_before_committing'].reduce((sum, key) => sum + Number(statistics[key] || 0), 0);
  const percent = total ? Math.min(100, Math.round(terminal * 100 / total)) : 0;
  $('deployment-name').textContent = deployment.name || deployment.id;
  $('deployment-id').textContent = deployment.id;
  $('deployment-progress').textContent = `${terminal}/${total} complete · ${percent}% · ${active} active`;
  $('deployment-summary').replaceChildren(table(['Field', 'Value'], [
    ['Artifact', deployment.artifact_name], ['Status', deployment.status],
    ['Created', deployment.created], ['Finished', deployment.finished], ['Target devices', total],
  ]));
  const stats = Object.entries(statistics).sort(([a], [b]) => a.localeCompare(b));
  $('deployment-statistics').replaceChildren(stats.length ? table(['Device state', 'Count'], stats) : node('p', 'No device statistics available.', 'muted'));
  const rows = (result.devices || []).map(device => {
    const target = state.devices.find(item => item.id === String(device.id));
    let log = '—';
    if (device.log) {
      log = node('button', 'Read log', 'result-log-button');
      log.type = 'button';
      log.addEventListener('click', async () => {
        const logVersion = ++state.deploymentLogVersion;
        $('deployment-log-panel').hidden = false;
        $('deployment-log-title').textContent = `Log · ${target?.name || device.id}`;
        $('deployment-log').textContent = 'Loading log…';
        try {
          const response = await api(`/api/log?device_id=${encodeURIComponent(device.id)}&deployment_id=${encodeURIComponent(deployment.id)}`);
          if (logVersion === state.deploymentLogVersion) $('deployment-log').textContent = response.text;
        } catch (error) {
          if (logVersion === state.deploymentLogVersion) $('deployment-log').textContent = error.message;
        }
      });
    }
    return [target?.name || device.id, device.status || 'Unknown', device.state || '—',
      device.substate || '—', device.finished || '—', log];
  });
  $('deployment-device-results').replaceChildren(rows.length ?
    table(['Device', 'Status', 'State', 'Substate', 'Finished', 'Log'], rows) :
    node('p', 'No device results have been reported yet.', 'muted'));
}

async function mutate(button, body) {
  if (button.disabled) return;
  button.disabled = true;
  try {
    const result = await api('/api/action', body);
    notice(result.message);
    await loadDevices();
    if (state.selected === body.device_id) await selectDevice(body.device_id);
  } catch (error) {
    notice(error.message, true);
  } finally {
    button.disabled = false;
  }
}

for (const [formId, action] of [['rename-form', 'rename'], ['group-form', 'group'], ['tag-form', 'tag-set'], ['deploy-form', 'deploy']]) {
  $(formId).addEventListener('submit', event => {
    event.preventDefault();
    if (!state.selected) return;
    const body = { ...Object.fromEntries(new FormData(event.target)), action, device_id: state.selected };
    if (action === 'deploy' && !confirm(`Deploy "${body.artifact}" to ${$('device-name').textContent}?`)) return;
    if (action === 'group' && !confirm('Change this device’s static group membership?')) return;
    mutate(event.target.querySelector('button[type="submit"]'), body);
  });
}
$('group-deploy-form').addEventListener('submit', event => {
  event.preventDefault();
  const group = state.groupFilter.name;
  const artifact = $('group-deploy-artifact').value;
  const count = new Set((state.groupMembers?.[group] || []).filter(id => state.devices.some(device => device.id === id))).size;
  if (!group || !artifact || !count) return;
  if (!confirm(`Deploy "${artifact}" to ${count} devices in group "${group}"?`)) return;
  mutate(event.target.querySelector('button[type="submit"]'), { action: 'group-deploy', group, artifact, expected_count: count });
});
$('search').addEventListener('input', renderDevices);
$('theme-toggle').addEventListener('click', () => {
  setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
});
$('artifact-filter').addEventListener('change', () => {
  renderDevices();
});
$('last-seen-filter').addEventListener('change', () => {
  renderDevices();
});
$('clear-filters').addEventListener('click', () => {
  $('search').value = '';
  $('artifact-filter').value = 'all';
  $('last-seen-filter').value = 'all';
  state.groupFilter = { type: 'all' };
  renderGroupFilters();
  renderDevices();
  renderGroupDeployment();
});
$('close-log').addEventListener('click', () => { state.logVersion++; $('log-panel').hidden = true; });
$('refresh').addEventListener('click', async () => {
  $('refresh').disabled = true;
  try {
    if (!$('deployments-view').hidden) {
      const deploymentId = state.selectedDeployment;
      const loadedCount = state.deployments.length;
      await loadDeployments();
      while (state.deployments.length < loadedCount && state.deploymentHasMore) await loadDeployments(true);
      if (deploymentId && state.selectedDeployment) await selectDeployment(deploymentId);
    } else {
      await loadDevices();
      await loadOptions();
      await loadGroupMembers();
      if (state.selected) await selectDevice(state.selected);
    }
  } catch (error) { notice(error.message, true); }
  finally { $('refresh').disabled = false; }
});
$('devices-tab').addEventListener('click', () => {
  $('devices-view').hidden = false;
  $('deployments-view').hidden = true;
  $('devices-tab').setAttribute('aria-pressed', 'true');
  $('deployments-tab').setAttribute('aria-pressed', 'false');
});
$('deployments-tab').addEventListener('click', () => {
  $('devices-view').hidden = true;
  $('deployments-view').hidden = false;
  $('devices-tab').setAttribute('aria-pressed', 'false');
  $('deployments-tab').setAttribute('aria-pressed', 'true');
  loadDeployments();
});
$('active-deployments-tab').addEventListener('click', () => {
  state.deploymentStatus = 'active';
  state.selectedDeployment = null;
  state.deploymentVersion++;
  $('active-deployments-tab').setAttribute('aria-pressed', 'true');
  $('finished-deployments-tab').setAttribute('aria-pressed', 'false');
  $('deployment-detail').hidden = true;
  $('deployment-welcome').hidden = false;
  loadDeployments();
});
$('finished-deployments-tab').addEventListener('click', () => {
  state.deploymentStatus = 'finished';
  state.selectedDeployment = null;
  state.deploymentVersion++;
  $('active-deployments-tab').setAttribute('aria-pressed', 'false');
  $('finished-deployments-tab').setAttribute('aria-pressed', 'true');
  $('deployment-detail').hidden = true;
  $('deployment-welcome').hidden = false;
  loadDeployments();
});
$('close-deployment-log').addEventListener('click', () => {
  state.deploymentLogVersion++;
  $('deployment-log-panel').hidden = true;
});

function stopTerminal(notifyServer = true) {
  clearInterval(terminalTimer);
  terminalTimer = null;
  if (terminalId && notifyServer) api('/api/action', { action: 'terminal-stop', device_id: state.selected, id: terminalId }).catch(() => {});
  terminalId = null;
}

$('terminal-start').addEventListener('click', async () => {
  if (!state.selected) return;
  $('terminal-start').disabled = true;
  try {
    const result = await api('/api/action', { action: 'terminal-start', device_id: state.selected });
    terminalId = result.id;
    $('terminal-status').textContent = 'Connecting…';
    $('terminal-stop').disabled = false;
    terminalTimer = setInterval(pollTerminal, 250);
  } catch (error) {
    $('terminal-start').disabled = false;
    $('terminal-status').textContent = error.message;
    notice(error.message, true);
  }
});

async function pollTerminal() {
  if (!terminalId) return;
  try {
    const result = await api(`/api/terminal?id=${encodeURIComponent(terminalId)}`);
    if (result.output) {
      const output = $('terminal-output');
      output.textContent = (output.textContent + result.output.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '').replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g, '')).slice(-32000);
      output.scrollTop = output.scrollHeight;
    }
    $('terminal-status').textContent = result.status === 'connected' ? 'Connected' : result.error || 'Disconnected';
    if (result.status === 'connected') {
      $('terminal-command').disabled = false;
      $('terminal-send').disabled = false;
      $('terminal-interrupt').disabled = false;
    } else if (result.status === 'closed') {
      api('/api/action', { action: 'terminal-stop', device_id: state.selected, id: terminalId }).catch(() => {});
      stopTerminal(false);
      $('terminal-start').disabled = false;
      $('terminal-stop').disabled = true;
      $('terminal-command').disabled = true;
      $('terminal-send').disabled = true;
      $('terminal-interrupt').disabled = true;
    }
  } catch (error) {
    $('terminal-status').textContent = error.message;
    stopTerminal(false);
    $('terminal-start').disabled = false;
  }
}

$('terminal-stop').addEventListener('click', () => {
  stopTerminal();
  $('terminal-status').textContent = 'Disconnected';
  $('terminal-start').disabled = false;
  $('terminal-stop').disabled = true;
  $('terminal-command').disabled = true;
  $('terminal-send').disabled = true;
  $('terminal-interrupt').disabled = true;
});
$('terminal-interrupt').addEventListener('click', () => sendTerminal('\x03'));
$('terminal-form').addEventListener('submit', event => {
  event.preventDefault();
  const command = $('terminal-command');
  if (!command.value.trim()) return;
  sendTerminal(command.value + '\r');
  command.value = '';
});
async function sendTerminal(input) {
  if (!terminalId) return;
  try { await api('/api/action', { action: 'terminal-input', device_id: state.selected, id: terminalId, input }); }
  catch (error) { notice(error.message, true); }
}

$('forward-form').addEventListener('submit', async event => {
  event.preventDefault();
  const local = Number($('forward-local').value);
  const port = Number($('forward-port').value);
  if (!Number.isInteger(local) || local < 1 || local > 65535 || !Number.isInteger(port) || port < 1 || port > 65535) return;
  const mapping = `${$('forward-protocol').value}/${local}:${$('forward-host').value.trim()}:${port}`;
  try {
    await api('/api/action', { action: 'forward-start', device_id: state.selected, mappings: [mapping] });
    await refreshForwards();
    notice('Port forwarding started.');
  } catch (error) { notice(error.message, true); }
});

async function refreshForwards() {
  if (!state.selected || $('device').hidden) return;
  try {
    const { forwards } = await api('/api/forwards');
    $('forwards').replaceChildren(...forwards.filter(item => item.device_id === state.selected).map(item => {
      const row = node('div', undefined, 'forward-row');
      const mapping = item.mappings[0] || '';
      const port = item.ports[0];
      const stateText = item.status === 'error' ? item.error : `${item.status} · ${mapping}`;
      row.append(node('span', stateText));
      if (item.status === 'ready' && mapping.startsWith('tcp/') && Number.isInteger(port)) {
        const link = node('a', `http://127.0.0.1:${port}`);
        link.href = `http://127.0.0.1:${port}`;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        row.append(link);
      }
      const stop = node('button', 'Stop');
      stop.type = 'button';
      stop.addEventListener('click', async () => {
        try {
          await api('/api/action', { action: 'forward-stop', device_id: item.device_id, id: item.id });
          await refreshForwards();
        } catch (error) { notice(error.message, true); }
      });
      row.append(stop);
      return row;
    }));
  } catch (error) { notice(error.message, true); }
}

window.addEventListener('pagehide', () => {
  if (!terminalId) return;
  fetch('/api/action', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MRC-Session': session },
    body: JSON.stringify({ action: 'terminal-stop', device_id: state.selected, id: terminalId }), keepalive: true });
});

loadDevices().catch(error => {
  $('list-state').textContent = error.message;
  notice(error.message, true);
});
loadOptions().catch(error => {
  $('artifact').replaceChildren(node('option', 'Could not load artifacts; try Refresh'));
  $('artifact').firstChild.value = '';
  notice(error.message, true);
});
loadGroupMembers();
