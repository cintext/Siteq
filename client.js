const money = new Intl.NumberFormat('id-ID', { style: 'currency', currency: 'IDR', maximumFractionDigits: 0 });
const grid = document.getElementById('animal-grid');
const search = document.getElementById('search');
const sort = document.getElementById('sort');
const dialog = (id) => document.getElementById(id);
let animals = [];
let pens = [];
let orders = [];
let users = [];
let auditLogs = [];
let activeFilter = 'semua';
let selectedAnimalId = null;
let managerAuthenticated = false;
let currentUser = null;
let apiOnline = false;
let toastTimer;

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[char]));
const animalIcon = (animal) => animal.category === 'Sapi' ? '🐄' : '🐐';
const qurbanEligible = (animal) => Number(animal.age) >= (animal.category === 'Sapi' ? 24 : 12) && animal.health === 'Sehat';

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  let response;
  try {
    response = await fetch(`/api${path}`, { ...options, headers, credentials: 'same-origin' });
  } catch {
    throw new Error('Backend tidak terhubung. Jalankan server SITEQ lalu buka http://127.0.0.1:8765.');
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.error || `Request gagal (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return body;
}

function toast(message) {
  const node = dialog('toast');
  node.textContent = message;
  node.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove('show'), 3000);
}

function setConnectionStatus(online, message = '') {
  apiOnline = online;
  const node = dialog('connection-status');
  node.innerHTML = online
    ? '<span><strong>TERHUBUNG</strong> Data disajikan dari backend SITEQ.</span>'
    : `<span><strong>BACKEND OFFLINE</strong> ${esc(message || 'Jalankan server SITEQ untuk memuat katalog.')}</span>`;
}

function renderCatalog() {
  if (!apiOnline) {
    dialog('catalog-count').textContent = 'Backend belum terhubung';
    grid.innerHTML = '<div class="empty"><strong>Hubungkan ke backend SITEQ.</strong>Buka halaman dari server lokal di <code>http://127.0.0.1:8765</code>.</div>';
    return;
  }
  const query = search.value.trim().toLocaleLowerCase('id-ID');
  let rows = animals.filter((animal) => {
    const filterMatches = activeFilter === 'semua'
      || (activeFilter === 'qurban' ? qurbanEligible(animal) : animal.category === activeFilter);
    const queryMatches = [animal.name, animal.id, animal.category, animal.breed, animal.pen]
      .join(' ').toLocaleLowerCase('id-ID').includes(query);
    return filterMatches && queryMatches;
  });
  if (sort.value === 'low') rows.sort((a, b) => Number(a.price) - Number(b.price));
  if (sort.value === 'high') rows.sort((a, b) => Number(b.price) - Number(a.price));
  if (sort.value === 'weight') rows.sort((a, b) => Number(b.weight) - Number(a.weight));
  dialog('catalog-count').textContent = `${rows.length} hewan`;
  if (!rows.length) {
    grid.innerHTML = '<div class="empty"><strong>Belum ada hasil yang cocok.</strong>Coba kata kunci atau filter yang lain.</div>';
    return;
  }
  grid.innerHTML = rows.map((animal) => `
    <article class="animal-card">
      <div class="animal-visual ${animal.category === 'Kambing' ? 'goat' : animal.id.endsWith('1') ? 'blue' : ''}">
        <span class="animal-badge">${qurbanEligible(animal) ? 'Indikasi awal qurban' : 'Perlu cek umur/kondisi'}</span>
        <span class="animal-symbol" aria-hidden="true">${animalIcon(animal)}</span>
      </div>
      <div class="animal-body">
        <div class="animal-title"><h3>${esc(animal.name)}</h3><span class="animal-id">${esc(animal.id)}</span></div>
        <p class="animal-breed">${esc(animal.breed)} · ${esc(animal.pen)}</p>
        <div class="animal-pills"><span class="pill">◷ ${esc(animal.age)} bulan</span><span class="pill">⚖ ${esc(animal.weight)} kg</span></div>
        <div class="health"><i class="dot" aria-hidden="true"></i>${esc(animal.health)} · dicatat pengurus</div>
        <div class="price-line"><div><small>${animal.available ? 'HARGA' : 'STATUS'}</small><span class="price">${animal.available ? money.format(Number(animal.price) || 0) : 'Tidak tersedia'}</span></div><button class="button ${animal.available ? 'secondary' : 'light'}" type="button" data-detail="${esc(animal.id)}">Lihat detail ↗</button></div>
      </div>
    </article>`).join('');
  grid.querySelectorAll('[data-detail]').forEach((button) => button.addEventListener('click', () => showDetail(button.dataset.detail)));
}

function showDetail(id) {
  const animal = animals.find((item) => item.id === id);
  if (!animal) return;
  selectedAnimalId = id;
  dialog('detail-title').textContent = `${animal.name} · ${animal.category}`;
  dialog('detail-icon').textContent = animalIcon(animal);
  dialog('detail-breed').textContent = animal.breed;
  dialog('detail-pen').textContent = `${animal.id} · ${animal.pen}`;
  dialog('detail-age').value = `${animal.age} bulan${qurbanEligible(animal) ? ' · indikasi awal' : ''}`;
  dialog('detail-weight').value = `${animal.weight} kg`;
  dialog('detail-health').value = animal.health;
  dialog('detail-price').value = money.format(Number(animal.price) || 0);
  const button = dialog('start-order');
  button.disabled = !animal.available;
  button.textContent = animal.available ? 'Ajukan minat' : 'Tidak tersedia';
  button.classList.toggle('secondary', !animal.available);
  dialog('detail-dialog').showModal();
}

async function refreshPublicData() {
  const [nextAnimals, nextPens] = await Promise.all([api('/animals'), api('/pens')]);
  animals = nextAnimals;
  pens = nextPens;
  setConnectionStatus(true);
  renderCatalog();
}

function switchPanel(name) {
  const targetTab = [...document.querySelectorAll('.manager-tab')].find((button) => button.dataset.panel === name && !button.hidden);
  if (!targetTab) name = 'dashboard';
  document.querySelectorAll('.manager-tab').forEach((button) => {
    const active = button.dataset.panel === name;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
  });
  document.querySelectorAll('.panel').forEach((panel) => panel.classList.toggle('active', panel.id === `panel-${name}`));
}

function showManagerGate(authenticated) {
  managerAuthenticated = authenticated;
  if (!authenticated) currentUser = null;
  dialog('manager-gate').hidden = authenticated;
  dialog('manager-content').hidden = !authenticated;
  dialog('manager-logout').hidden = !authenticated;
  dialog('profile-security-open').hidden = !authenticated;
  dialog('manager-profile').hidden = !authenticated;
  dialog('manager-profile').textContent = authenticated && currentUser
    ? `${currentUser.display_name} · ${currentUser.role_name}` : '';
  document.querySelectorAll('[data-role]').forEach((node) => {
    const allowed = (node.dataset.role || '').split(',').includes(currentUser?.role || '');
    node.hidden = !allowed;
  });
  if (authenticated && currentUser?.role === 'vet') {
    dialog('inventory-note').textContent = 'Dokter hewan dapat membaca inventaris dan memperbarui status kesehatan.';
    dialog('quick-action-note').textContent = 'Pembaruan kesehatan ternak';
  } else {
    dialog('inventory-note').textContent = 'Data yang tersedia di katalog pembeli.';
    dialog('quick-action-note').textContent = 'Kelola data ternak';
  }
}

function switchView(view) {
  const manager = view === 'manager';
  document.getElementById('store-view').hidden = manager;
  document.getElementById('manager-view').hidden = !manager;
  document.querySelectorAll('[data-view]').forEach((button) => button.classList.toggle('active', button.dataset.view === view));
  dialog('store-nav').hidden = manager;
  dialog('header-cta').hidden = manager;
  if (manager) {
    if (managerAuthenticated) renderManager();
    else showManagerGate(false);
  } else renderCatalog();
  history.replaceState(null, '', manager ? '#pengurus' : '#beranda');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function renderInventory() {
  const body = dialog('inventory-body');
  const isVet = currentUser?.role === 'vet';
  if (!animals.length) {
    body.innerHTML = '<tr><td colspan="7">Belum ada data ternak.</td></tr>';
    return;
  }
  body.innerHTML = animals.map((animal) => `<tr>
    <td><span class="row-name">${esc(animal.name)}</span><span class="row-sub">${esc(animal.id)}</span></td>
    <td>${esc(animal.category)}<span class="row-sub">${esc(animal.breed)}</span></td>
    <td>${esc(animal.age)} bln · ${esc(animal.weight)} kg</td><td>${money.format(Number(animal.price) || 0)}</td>
    <td>${esc(animal.pen)}</td><td>${isVet && animal.health !== 'Terjual'
      ? `<select class="health-select" data-health-select="${esc(animal.id)}" aria-label="Status kesehatan ${esc(animal.name)}"><option ${animal.health === 'Sehat' ? 'selected' : ''}>Sehat</option><option ${animal.health === 'Perawatan' ? 'selected' : ''}>Perawatan</option><option ${animal.health === 'Karantina' ? 'selected' : ''}>Karantina</option></select>`
      : `<span class="status-tag ${animal.health === 'Sehat' ? 'ok' : animal.health === 'Terjual' ? 'no' : ''}">${esc(animal.health)}</span>`}</td>
    <td><div class="row-actions">${isVet && animal.health !== 'Terjual'
      ? `<button class="row-button" data-save-health="${esc(animal.id)}">Simpan</button>`
      : isVet ? '—'
      : `<button class="row-button" data-edit-animal="${esc(animal.id)}">Ubah</button><button class="row-button" data-delete-animal="${esc(animal.id)}">Hapus</button>`}</div></td>
  </tr>`).join('');
  body.querySelectorAll('[data-edit-animal]').forEach((button) => button.addEventListener('click', () => openAnimalForm(button.dataset.editAnimal)));
  body.querySelectorAll('[data-delete-animal]').forEach((button) => button.addEventListener('click', () => deleteAnimal(button.dataset.deleteAnimal)));
  body.querySelectorAll('[data-save-health]').forEach((button) => button.addEventListener('click', () => updateAnimalHealth(button.dataset.saveHealth)));
}

function renderPens() {
  const container = dialog('pens-grid');
  if (!pens.length) {
    container.innerHTML = '<div class="empty"><strong>Belum ada kandang.</strong>Tambahkan kandang untuk mulai mengelola ternak.</div>';
    return;
  }
  container.innerHTML = pens.map((pen) => {
    const occupied = Number(pen.occupied) || 0;
    const percent = Math.min(100, Math.round(occupied / Number(pen.capacity) * 100));
    return `<article class="pen-card"><div class="pen-top"><div><h3>${esc(pen.name)}</h3><p>${esc(pen.category)} · kapasitas ${esc(pen.capacity)} ekor</p></div><span class="status-tag ${percent >= 85 ? 'no' : 'ok'}">${percent >= 85 ? 'Hampir penuh' : 'Aktif'}</span></div><div class="pen-count"><strong>${occupied}<span style="font-size:10px;font-weight:500"> / ${esc(pen.capacity)}</span></strong><span>${percent}% terisi</span></div><div class="progress"><i class="${percent >= 85 ? 'full' : ''}" style="width:${percent}%"></i></div></article>`;
  }).join('');
}

function renderOrders() {
  const container = dialog('orders-list');
  const pending = orders.filter((order) => order.status === 'Menunggu konfirmasi').length;
  dialog('pending-count').textContent = pending ? `· ${pending}` : '';
  if (!orders.length) {
    container.innerHTML = '<div class="empty"><strong>Belum ada permintaan.</strong>Permintaan pembeli akan muncul di sini setelah dikirim.</div>';
    return;
  }
  container.innerHTML = [...orders].map((order) => `<article class="order-card"><div>
    <h3>${esc(order.animal_name)} · ${esc(order.category)}</h3><p>${esc(order.buyer_name)} · ${esc(order.buyer_phone)}</p>
    <div class="order-meta"><span>${esc(order.animal_id)}</span><span>${esc(order.note || 'Tanpa catatan')}</span><span class="status-tag ${order.status === 'Dikonfirmasi' ? 'ok' : order.status === 'Ditolak' ? 'no' : ''}">${esc(order.status)}</span></div>
    <div class="order-date">${new Date(order.created_at).toLocaleString('id-ID', { dateStyle: 'medium', timeStyle: 'short' })}</div>
  </div><div class="order-actions">${order.status === 'Menunggu konfirmasi'
    ? `<button class="button small" data-order-status="${esc(order.id)}" data-status="Dikonfirmasi">Konfirmasi</button><button class="button danger small" data-order-status="${esc(order.id)}" data-status="Ditolak">Tolak</button>`
    : `<button class="button light small" data-order-status="${esc(order.id)}" data-status="Menunggu konfirmasi">Buka lagi</button>`}
  </div></article>`).join('');
  container.querySelectorAll('[data-order-status]').forEach((button) => button.addEventListener('click', () => updateOrder(button.dataset.orderStatus, button.dataset.status)));
}

function renderDashboard() {
  const isVet = currentUser?.role === 'vet';
  dialog('orders-summary').hidden = isVet;
  const pending = orders.filter((order) => order.status === 'Menunggu konfirmasi').length;
  const healthy = animals.filter((animal) => animal.health === 'Sehat').length;
  const needingCare = animals.filter((animal) => ['Perawatan', 'Karantina'].includes(animal.health)).length;
  const stats = isVet
    ? [['Total ternak', animals.length, 'Data pada database'], ['Kondisi sehat', healthy, 'Dicatat dalam inventaris'], ['Perlu pemantauan', needingCare, 'Perawatan atau karantina'], ['Kandang aktif', pens.length, 'Lokasi terdaftar']]
    : [['Total ternak', animals.length, 'Data pada database'], ['Kandang aktif', pens.length, 'Lokasi terdaftar'], ['Menunggu konfirmasi', pending, 'Permintaan pembeli'], ['Kondisi sehat', healthy, 'Dicatat pengurus']];
  dialog('stats').innerHTML = stats.map(([title, value, sub]) => `<article class="stat"><small>${title}</small><strong>${value}</strong><span>${sub}</span></article>`).join('');
  const latest = orders.slice(0, 4);
  dialog('recent-orders').innerHTML = latest.length ? latest.map((order) => `<div class="list-row"><div><strong>${esc(order.buyer_name)} · ${esc(order.animal_name)}</strong><small>${esc(order.animal_id)} · ${esc(order.buyer_phone)}</small></div><span class="status-tag ${order.status === 'Dikonfirmasi' ? 'ok' : order.status === 'Ditolak' ? 'no' : ''}">${esc(order.status)}</span></div>`).join('') : '<div class="empty"><strong>Belum ada permintaan.</strong>Permintaan pembeli akan tampil di sini.</div>';
}

async function renderManager() {
  if (!managerAuthenticated) return;
  try {
    const requests = [api('/animals'), api('/pens')];
    const canSeeOrders = ['owner', 'staff'].includes(currentUser?.role);
    const isOwner = currentUser?.role === 'owner';
    if (canSeeOrders) requests.push(api('/admin/orders'));
    if (isOwner) requests.push(api('/admin/users'), api('/admin/audit'));
    const results = await Promise.all(requests);
    const [nextAnimals, nextPens] = results;
    animals = nextAnimals;
    pens = nextPens;
    orders = canSeeOrders ? results[2] : [];
    users = isOwner ? results[3] : [];
    auditLogs = isOwner ? results[4] : [];
    renderDashboard();
    renderInventory();
    renderPens();
    if (canSeeOrders) renderOrders();
    if (isOwner) { renderUsers(); renderAudit(); }
    renderCatalog();
  } catch (error) {
    if (error.status === 401) showManagerGate(false);
    toast(error.message);
  }
}

function renderUsers() {
  const body = dialog('users-body');
  body.innerHTML = users.length ? users.map((user) => `<tr>
    <td><span class="row-name">${esc(user.display_name)}</span></td><td>${esc(user.username)}</td>
    <td><span class="status-tag">${esc(user.role_name)}</span></td><td>${user.active ? 'Aktif' : 'Nonaktif'}</td>
    <td><button class="row-button" data-delete-user="${user.id}" ${user.id === currentUser?.id ? 'disabled title="Akun yang sedang dipakai tidak dapat dihapus"' : ''}>Hapus</button></td>
  </tr>`).join('') : '<tr><td colspan="5">Belum ada akun lain.</td></tr>';
  body.querySelectorAll('[data-delete-user]').forEach((button) => button.addEventListener('click', () => deleteUser(Number(button.dataset.deleteUser))));
}

function renderAudit() {
  const container = dialog('audit-list');
  container.innerHTML = auditLogs.length ? auditLogs.map((item) => `<div class="list-row"><div><strong>${esc(item.actor_username)} · ${esc(item.action)} ${esc(item.entity_type)}</strong><small>${esc(item.entity_id)} · ${new Date(item.created_at).toLocaleString('id-ID', { dateStyle: 'medium', timeStyle: 'short' })}</small></div><span class="status-tag">${esc(ROLE_NAMES_JS[item.actor_role] || item.actor_role)}</span></div>`).join('') : '<div class="empty"><strong>Belum ada aktivitas.</strong>Login dan perubahan data akan tercatat.</div>';
}

const ROLE_NAMES_JS = { owner: 'Pemilik', staff: 'Petugas Kandang', vet: 'Dokter Hewan', buyer: 'Pembeli' };

function updatePenOptions(category, selected = '') {
  const select = dialog('animal-pen');
  const matchingPens = pens.filter((pen) => pen.category === category);
  select.innerHTML = matchingPens.length
    ? matchingPens.map((pen) => `<option value="${esc(pen.name)}" ${pen.name === selected ? 'selected' : ''}>${esc(pen.name)}</option>`).join('')
    : '<option value="">Belum ada kandang</option>';
  select.disabled = matchingPens.length === 0;
}

function openAnimalForm(id = '') {
  const animal = animals.find((item) => item.id === id);
  dialog('animal-form').reset();
  dialog('animal-edit-id').value = animal?.id || '';
  dialog('animal-form-title').textContent = animal ? 'Ubah data ternak' : 'Tambah ternak';
  dialog('animal-category').value = animal?.category || 'Sapi';
  dialog('animal-name').value = animal?.name || '';
  dialog('animal-breed').value = animal?.breed || '';
  dialog('animal-age').value = animal?.age || '';
  dialog('animal-weight').value = animal?.weight || '';
  dialog('animal-price').value = animal?.price || '';
  dialog('animal-health').value = animal?.health || 'Sehat';
  updatePenOptions(dialog('animal-category').value, animal?.pen || '');
  dialog('animal-form-dialog').showModal();
}

async function saveAnimal(event) {
  event.preventDefault();
  const id = dialog('animal-edit-id').value;
  const payload = {
    name: dialog('animal-name').value.trim(), category: dialog('animal-category').value,
    breed: dialog('animal-breed').value.trim(), pen: dialog('animal-pen').value,
    age: Number(dialog('animal-age').value), weight: Number(dialog('animal-weight').value),
    price: Number(dialog('animal-price').value), health: dialog('animal-health').value
  };
  try {
    await api(id ? `/admin/animals/${encodeURIComponent(id)}` : '/admin/animals', {
      method: id ? 'PATCH' : 'POST', body: JSON.stringify(payload)
    });
    dialog('animal-form-dialog').close();
    await refreshPublicData();
    await renderManager();
    toast(id ? 'Data ternak diperbarui.' : 'Ternak ditambahkan.');
  } catch (error) { toast(error.message); }
}

async function deleteAnimal(id) {
  if (!window.confirm('Hapus data hewan ini? Hewan dengan riwayat permintaan tidak dapat dihapus.')) return;
  try {
    await api(`/admin/animals/${encodeURIComponent(id)}`, { method: 'DELETE' });
    await refreshPublicData();
    await renderManager();
    toast('Data hewan dihapus.');
  } catch (error) { toast(error.message); }
}

async function updateOrder(id, status) {
  try {
    await api(`/admin/orders/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ status }) });
    await refreshPublicData();
    await renderManager();
    toast(`Status permintaan diubah: ${status}.`);
  } catch (error) { toast(error.message); }
}

async function updateAnimalHealth(id) {
  const health = dialog('inventory-body').querySelector(`[data-health-select="${CSS.escape(id)}"]`)?.value;
  if (!health) return;
  try {
    await api(`/vet/animals/${encodeURIComponent(id)}/health`, { method: 'PATCH', body: JSON.stringify({ health }) });
    await refreshPublicData();
    await renderManager();
    toast(`Kesehatan diperbarui: ${health}.`);
  } catch (error) { toast(error.message); }
}

async function saveUser(event) {
  event.preventDefault();
  const payload = {
    display_name: dialog('user-display-name').value.trim(),
    username: dialog('user-username').value.trim(),
    password: dialog('user-password').value,
    role: dialog('user-role').value
  };
  try {
    await api('/admin/users', { method: 'POST', body: JSON.stringify(payload) });
    event.currentTarget.reset();
    dialog('user-form-dialog').close();
    await renderManager();
    toast('Akun pengurus dibuat. Sampaikan password sementara secara aman.');
  } catch (error) { toast(error.message); }
}

async function deleteUser(id) {
  if (!window.confirm('Hapus akun pengurus ini? Sesi aktif akun tersebut juga akan dicabut.')) return;
  try {
    await api(`/admin/users/${id}`, { method: 'DELETE' });
    await renderManager();
    toast('Akun pengurus dihapus.');
  } catch (error) { toast(error.message); }
}

async function changePassword(event) {
  event.preventDefault();
  const currentPassword = dialog('profile-current-password').value;
  const newPassword = dialog('profile-new-password').value;
  if (newPassword !== dialog('profile-confirm-password').value) {
    toast('Ulangan password baru belum sama.');
    return;
  }
  try {
    await api('/admin/profile', { method: 'PATCH', body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) });
    event.currentTarget.reset();
    dialog('profile-security-dialog').close();
    toast('Password diperbarui. Sesi di perangkat lain sudah dikeluarkan.');
  } catch (error) { toast(error.message); }
}

async function savePen(event) {
  event.preventDefault();
  const payload = {
    name: dialog('pen-name').value.trim(), category: dialog('pen-category').value,
    capacity: Number(dialog('pen-capacity').value)
  };
  try {
    await api('/admin/pens', { method: 'POST', body: JSON.stringify(payload) });
    dialog('pen-form-dialog').close();
    await refreshPublicData();
    await renderManager();
    toast('Kandang ditambahkan.');
  } catch (error) { toast(error.message); }
}

async function submitOrder(event) {
  event.preventDefault();
  const payload = {
    animal_id: selectedAnimalId,
    buyer_name: dialog('buyer-name').value.trim(),
    buyer_phone: dialog('buyer-phone').value.trim(),
    note: dialog('buyer-note').value.trim()
  };
  try {
    await api('/orders', { method: 'POST', body: JSON.stringify(payload) });
    event.currentTarget.reset();
    dialog('order-dialog').close();
    await refreshPublicData();
    if (managerAuthenticated) await renderManager();
    toast('Permintaan tersimpan. Pengurus dapat melihatnya di portal.');
  } catch (error) { toast(error.message); }
}

async function login(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    const result = await api('/admin/login', { method: 'POST', body: JSON.stringify({ username: form.get('username'), password: form.get('password') }) });
    currentUser = result.user;
    event.currentTarget.reset();
    dialog('login-dialog').close();
    showManagerGate(true);
    switchPanel('dashboard');
    await renderManager();
    toast('Berhasil masuk.');
  } catch (error) { toast(error.message); }
}

async function logout() {
  try { await api('/admin/logout', { method: 'POST', body: '{}' }); }
  catch (error) { toast(error.message); }
  showManagerGate(false);
  switchView('manager');
  toast('Sesi pengurus berakhir.');
}

async function bootstrap() {
  try {
    await api('/health');
    setConnectionStatus(true);
    await refreshPublicData();
    try {
      const session = await api('/admin/session');
      currentUser = session;
      showManagerGate(true);
      await renderManager();
    } catch { showManagerGate(false); }
  } catch (error) {
    setConnectionStatus(false, 'Buka http://127.0.0.1:8765 setelah menjalankan backend.');
    renderCatalog();
  }
  if (location.hash === '#pengurus') switchView('manager');
}

document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => switchView(button.dataset.view)));
document.querySelectorAll('[data-filter]').forEach((button) => button.addEventListener('click', () => {
  activeFilter = button.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach((filter) => {
    const active = filter === button;
    filter.classList.toggle('active', active);
    filter.setAttribute('aria-pressed', String(active));
  });
  renderCatalog();
}));
search.addEventListener('input', renderCatalog);
sort.addEventListener('change', renderCatalog);
document.querySelectorAll('.manager-tab').forEach((button) => button.addEventListener('click', () => switchPanel(button.dataset.panel)));
document.querySelectorAll('[data-open-panel]').forEach((button) => button.addEventListener('click', () => switchPanel(button.dataset.openPanel)));
document.querySelectorAll('[data-action="add-animal"]').forEach((button) => button.addEventListener('click', () => openAnimalForm()));
document.querySelectorAll('[data-action="add-pen"]').forEach((button) => button.addEventListener('click', () => { dialog('pen-form').reset(); dialog('pen-form-dialog').showModal(); }));
document.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', () => dialog(button.dataset.close).close()));
document.querySelectorAll('dialog').forEach((node) => node.addEventListener('click', (event) => { if (event.target === node) node.close(); }));
dialog('start-order').addEventListener('click', () => {
  const animal = animals.find((item) => item.id === selectedAnimalId);
  if (!animal?.available) { dialog('detail-dialog').close(); toast('Hewan ini sudah tidak tersedia.'); return; }
  dialog('detail-dialog').close();
  dialog('order-animal-label').textContent = `Minat untuk ${animal.name} (${animal.id}) · ${money.format(Number(animal.price) || 0)}`;
  dialog('order-dialog').showModal();
  dialog('buyer-name').focus();
});
dialog('order-form').addEventListener('submit', submitOrder);
dialog('animal-category').addEventListener('change', (event) => updatePenOptions(event.target.value));
dialog('animal-form').addEventListener('submit', saveAnimal);
dialog('pen-form').addEventListener('submit', savePen);
dialog('user-form').addEventListener('submit', saveUser);
dialog('user-add-open').addEventListener('click', () => dialog('user-form-dialog').showModal());
dialog('profile-security-open').addEventListener('click', () => {
  dialog('profile-security-label').textContent = `${currentUser?.display_name || ''} · ${currentUser?.username || ''} · ${currentUser?.role_name || ''}`;
  dialog('profile-security-form').reset();
  dialog('profile-security-dialog').showModal();
});
dialog('profile-security-form').addEventListener('submit', changePassword);
dialog('manager-login-open').addEventListener('click', () => dialog('login-dialog').showModal());
dialog('login-form').addEventListener('submit', login);
dialog('manager-logout').addEventListener('click', logout);

if (window.Lenis && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
  window.siteqLenis = new Lenis({ autoRaf: true, anchors: true, allowNestedScroll: true });
}
bootstrap();
