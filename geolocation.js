(function () {
  'use strict';

  const R = 6371.0088;
  const explorers = Array.from(document.querySelectorAll('[data-location-explorer]'));
  if (!explorers.length) return;

  explorers.forEach(function (explorer) {
    let location = null;
    let locationWatchId = null;
    let map = null;
    let placeMarkers = [];
    let userMarker = null;
    const locateButton = explorer.querySelector('[data-locate]');
    const refreshButton = explorer.querySelector('[data-refresh]');
    const radiusSelect = explorer.querySelector('[data-radius]');
    const followCheckbox = explorer.querySelector('[data-follow]');
    const status = explorer.querySelector('[data-location-status]');
    const emptyMessage = explorer.querySelector('[data-location-empty]');
    const coverage = explorer.querySelector('[data-location-coverage]');
    const cards = Array.from(document.querySelectorAll('[data-place-card]'));
    const csrfToken = explorer.dataset.csrfToken || '';
    let lastDssRequestAt = 0;
    let queuedDssRequest = null;
    let dssRequestSerial = 0;
    if (!cards.length) {
      if (locateButton) locateButton.disabled = true;
      if (status) status.textContent = 'Tidak ada kartu tempat yang dapat diukur pada halaman ini. Buka halaman kategori untuk mencari tempat terdekat.';
      return;
    }

    function haversine(lat1, lng1, lat2, lng2) {
      const toRad = function (degrees) { return degrees * Math.PI / 180; };
      const p1 = toRad(lat1), p2 = toRad(lat2);
      const dp = toRad(lat2 - lat1), dl = toRad(lng2 - lng1);
      const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
      return 2 * R * Math.asin(Math.sqrt(Math.max(0, Math.min(1, a))));
    }

    function getCoords(card) {
      const lat = Number(card.dataset.lat), lng = Number(card.dataset.lng);
      if (card.dataset.lat === '' || card.dataset.lng === '' || !Number.isFinite(lat) || !Number.isFinite(lng) || lat < -90 || lat > 90 || lng < -180 || lng > 180) return null;
      return { lat: lat, lng: lng };
    }

    function getDistance(card) {
      const point = getCoords(card);
      if (!point || !location) return null;
      return haversine(location.lat, location.lng, point.lat, point.lng);
    }

    function resultContainer(card) { return card.parentElement; }

    function updateCards() {
      if (!location) return;
      const radiusValue = radiusSelect ? radiusSelect.value : '5';
      const allResults = radiusValue === 'all';
      const radius = Number(radiusValue);
      const groups = new Map();
      let matched = 0, withCoords = 0, withoutCoords = 0;
      cards.forEach(function (card) {
        const point = getCoords(card);
        const distance = getDistance(card);
        if (point) withCoords += 1; else withoutCoords += 1;
        card.dataset.currentDistance = distance === null ? '' : String(distance);
        const validInRadius = allResults || (distance !== null && Number.isFinite(radius) && distance <= radius);
        card.hidden = !validInRadius;
        if (validInRadius) matched += 1;
        const distanceBadge = card.querySelector('[data-current-distance]');
        if (distanceBadge) {
          if (distance === null) {
            distanceBadge.hidden = true;
          } else {
            distanceBadge.hidden = false;
            distanceBadge.textContent = distance < 1 ? Math.round(distance * 1000) + ' m dari lokasi Anda' : distance.toFixed(2) + ' km dari lokasi Anda';
          }
        }
        const group = resultContainer(card);
        if (group) {
          if (!groups.has(group)) groups.set(group, []);
          groups.get(group).push(card);
        }
      });
      groups.forEach(function (items, group) {
        items.sort(function (a, b) {
          const da = a.dataset.currentDistance === '' ? Number.POSITIVE_INFINITY : Number(a.dataset.currentDistance);
          const db = b.dataset.currentDistance === '' ? Number.POSITIVE_INFINITY : Number(b.dataset.currentDistance);
          return da - db;
        });
        items.forEach(function (card) { group.appendChild(card); });
      });
      if (emptyMessage) emptyMessage.hidden = matched > 0;
      if (coverage) {
        coverage.hidden = false;
        coverage.textContent = withCoords + ' dari ' + cards.length + ' tempat pada hasil halaman memiliki koordinat valid.' + (withoutCoords ? ' ' + withoutCoords + ' tempat tanpa koordinat tidak dapat diurutkan berdasarkan jarak.' : '');
      }
      updateMap();
    }

    function updateMap() {
      if (!map || !window.L) return;
      placeMarkers.forEach(function (marker) { map.removeLayer(marker); });
      placeMarkers = [];
      cards.forEach(function (card) {
        if (card.hidden) return;
        const point = getCoords(card);
        if (!point) return;
        const label = card.dataset.title || 'Tempat UNeed';
        const popup = window.L.DomUtil.create('div', 'uneed-map-popup');
        const title = window.L.DomUtil.create('strong', '', popup);
        title.textContent = label;
        const detail = window.L.DomUtil.create('p', '', popup);
        const categoryLabels = { kos: 'Kos & Kontrakan', warung: 'Tempat Makan & Warung', cafe: 'Café & Working Space', klinik: 'Layanan Kesehatan', atk: 'ATK & Percetakan' };
        detail.textContent = (categoryLabels[card.dataset.category] || 'Tempat') + (card.dataset.currentDistance ? ' · ' + (Number(card.dataset.currentDistance) < 1 ? Math.round(Number(card.dataset.currentDistance) * 1000) + ' m' : Number(card.dataset.currentDistance).toFixed(2) + ' km') + ' dari lokasi Anda' : '');
        const focusButton = window.L.DomUtil.create('button', '', popup);
        focusButton.type = 'button';
        focusButton.textContent = 'Lihat tempat di daftar';
        focusButton.setAttribute('class', 'mt-2 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-bold text-white');
        focusButton.addEventListener('click', function () {
          card.scrollIntoView({ behavior: 'smooth', block: 'center' });
          card.classList.add('ring-2', 'ring-blue-500');
          window.setTimeout(function () { card.classList.remove('ring-2', 'ring-blue-500'); }, 1800);
          if (map) map.closePopup();
        });
        const marker = window.L.marker([point.lat, point.lng]).addTo(map).bindPopup(popup);
        placeMarkers.push(marker);
      });
      if (location) {
        if (userMarker) map.removeLayer(userMarker);
        userMarker = window.L.circleMarker([location.lat, location.lng], { radius: 8, color: '#2563eb', fillColor: '#60a5fa', fillOpacity: 0.9 }).addTo(map).bindPopup('Lokasi Anda saat ini');
        map.setView([location.lat, location.lng], 14);
      } else if (placeMarkers.length) {
        const group = window.L.featureGroup(placeMarkers);
        map.fitBounds(group.getBounds().pad(0.15));
      }
    }

    function applyDssScores(items) {
      const index = new Map();
      (Array.isArray(items) ? items : []).forEach(function (item) {
        if (!item || item.category === undefined || item.record_id === undefined) return;
        index.set(String(item.category) + ':' + String(item.record_id), item);
      });
      cards.forEach(function (card) {
        const key = String(card.dataset.category || '') + ':' + String(card.dataset.recordId || '');
        const result = index.get(key);
        if (!result) return;
        const badge = card.querySelector('[data-dss-score]');
        if (!badge) return;
        const score = result.dss_score;
        if (score === null || score === undefined || !Number.isFinite(Number(score))) {
          badge.hidden = true;
          badge.textContent = '';
        } else {
          badge.hidden = false;
          badge.textContent = 'Skor DSS ' + Number(score).toFixed(3);
        }
      });
    }

    async function refreshDssScores(force) {
      if (!location || !csrfToken) return;
      const now = Date.now();
      const minimumInterval = 8000;
      if (!force && now - lastDssRequestAt < minimumInterval) {
        if (queuedDssRequest === null) {
          const wait = Math.max(250, minimumInterval - (now - lastDssRequestAt));
          queuedDssRequest = window.setTimeout(function () {
            queuedDssRequest = null;
            refreshDssScores(true);
          }, wait);
        }
        return;
      }
      lastDssRequestAt = now;
      const serial = ++dssRequestSerial;
      const snapshot = { lat: location.lat, lng: location.lng };
      try {
        const response = await fetch('/api/location-dss', {
          method: 'POST',
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify(snapshot)
        });
        if (!response.ok) throw new Error('Perhitungan DSS berbasis lokasi belum tersedia.');
        const payload = await response.json();
        if (serial !== dssRequestSerial) return;
        // Ignore a response for an older position if the device moved while the request was running.
        if (!location || Math.abs(location.lat - snapshot.lat) > 0.00001 || Math.abs(location.lng - snapshot.lng) > 0.00001) return;
        applyDssScores(payload.items);
        if (status) status.textContent += ' Skor DSS disesuaikan dengan jarak dari posisi ini; koordinat tidak disimpan.';
      } catch (error) {
        if (serial === dssRequestSerial && status) {
          status.textContent += ' Jarak di kartu dihitung di browser, tetapi skor DSS berbasis lokasi belum berhasil diperbarui.';
        }
      }
    }

    function updateLocation(position) {
      const c = position && position.coords;
      if (!c || !Number.isFinite(c.latitude) || !Number.isFinite(c.longitude) || c.latitude < -90 || c.latitude > 90 || c.longitude < -180 || c.longitude > 180) {
        if (status) status.textContent = 'Browser tidak memberikan koordinat yang valid. Cari secara manual berdasarkan nama atau area.';
        return;
      }
      location = { lat: c.latitude, lng: c.longitude, accuracy: c.accuracy, timestamp: position.timestamp || Date.now() };
      if (refreshButton) refreshButton.hidden = false;
      if (radiusSelect) radiusSelect.disabled = false;
      if (followCheckbox) followCheckbox.disabled = false;
      const accuracy = Number.isFinite(c.accuracy) ? ' Akurasi perkiraan ±' + Math.round(c.accuracy) + ' m.' : '';
      if (status) status.textContent = 'Lokasi diperbarui ' + new Date(location.timestamp).toLocaleString('id-ID') + '.' + accuracy + ' Hasil diurutkan menurut jarak garis lurus.';
      updateCards();
      refreshDssScores(false);
    }

    function locationError(error) {
      let reason = 'Lokasi tidak dapat diperoleh.';
      if (error && error.code === 1) reason = 'Izin lokasi ditolak. Anda dapat mencari manual berdasarkan nama atau area.';
      else if (error && error.code === 2) reason = 'Posisi tidak tersedia. Periksa GPS/perangkat lalu coba lagi, atau gunakan pencarian manual.';
      else if (error && error.code === 3) reason = 'Permintaan lokasi melewati batas waktu. Coba lagi atau gunakan pencarian manual.';
      if (status) status.textContent = reason;
    }

    function locate() {
      if (!navigator.geolocation) {
        locationError({ code: 2 });
        return;
      }
      if (status) status.textContent = 'Meminta izin lokasi perangkat...';
      navigator.geolocation.getCurrentPosition(updateLocation, locationError, { enableHighAccuracy: true, timeout: 12000, maximumAge: 0 });
    }

    function setFollow(enabled) {
      if (!navigator.geolocation) {
        if (followCheckbox) followCheckbox.checked = false;
        locationError({ code: 2 });
        return;
      }
      if (!enabled) {
        if (locationWatchId !== null) navigator.geolocation.clearWatch(locationWatchId);
        locationWatchId = null;
        if (status && location) status.textContent = 'Ikuti lokasi dinonaktifkan. Posisi terakhir tetap digunakan sampai Anda memperbaruinya.';
        return;
      }
      if (locationWatchId !== null) navigator.geolocation.clearWatch(locationWatchId);
      if (status) status.textContent = 'Mengikuti perubahan lokasi sesuai izin Anda. Nonaktifkan kotak ini untuk menghentikan pemantauan.';
      locationWatchId = navigator.geolocation.watchPosition(updateLocation, locationError, { enableHighAccuracy: true, maximumAge: 5000, timeout: 15000 });
    }

    function initMap() {
      const mapElement = explorer.querySelector('[data-nearby-map]');
      if (!mapElement || !window.L) return;
      map = window.L.map(mapElement, { scrollWheelZoom: false });
      window.L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap contributors'
      }).addTo(map);
      updateMap();
    }

    if (locateButton) locateButton.addEventListener('click', locate);
    if (refreshButton) refreshButton.addEventListener('click', locate);
    if (radiusSelect) radiusSelect.addEventListener('change', updateCards);
    if (followCheckbox) followCheckbox.addEventListener('change', function () { setFollow(followCheckbox.checked); });
    window.addEventListener('pagehide', function () {
      if (locationWatchId !== null && navigator.geolocation) navigator.geolocation.clearWatch(locationWatchId);
    });
    initMap();
  });
})();
