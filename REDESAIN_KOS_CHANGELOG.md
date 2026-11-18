# UNeed Malang — Redesign Modul Kos

## Ringkasan

Modul daftar dan detail kos dirancang ulang menjadi pengalaman marketplace yang lebih modern, responsif, dan berorientasi pada pencarian. Implementasi tetap menggunakan Flask, Jinja2, CSS, dan Alpine.js sesuai fondasi proyek, serta mempertahankan halaman kategori generik untuk kategori selain kos.

## File dibuat/diubah

- `website_user/app_user.py` — pemrosesan pencarian, filter, pengurutan, normalisasi record kos, dan validasi tautan Maps/WhatsApp.
- `website_user/templates/base.html` — pemuatan stylesheet khusus kos hanya pada route kos.
- `website_user/templates/kos.html` — halaman daftar kos khusus dengan hero pencarian, filter desktop/mobile, chip filter aktif, kartu listing, empty/error state, dan placeholder foto.
- `website_user/templates/kos_detail.html` — halaman detail kos dengan informasi bersumber dari dataset, lokasi, aksi yang tervalidasi, dan penjelasan DSS.
- `website_user/static/css/kos.css` — styling khusus kos yang responsif, state fokus, mode gelap, dan preferensi reduced-motion.
- `tests/test_kos_redesign.py` — pengujian regresi untuk alur utama redesign.

## Integrasi data

Data dibaca dari `website_user/uploads_excel/kos.csv`. Pada snapshot yang diberikan, parser menghasilkan 16 record dengan 3 record bernama yang layak ditampilkan sebagai listing kos. Informasi harga dan fasilitas tidak terisi; status ketersediaan juga tidak tersedia secara valid. Karena itu, UI tidak mengarang harga, foto, fasilitas, rating, atau status kamar. Baris tanpa nama tidak ditampilkan sebagai listing properti, tetapi jumlah record anonim diinformasikan secara transparan.

Filter dan sort menggunakan parameter GET dan diproses di backend. Pengurutan DSS menggunakan skor yang telah disediakan alur aplikasi; algoritma dan konfigurasi bobot global tidak diubah. Pengurutan jarak diberi keterangan bahwa nilainya adalah jarak garis lurus dari titik acuan pusat Kota Malang, bukan jarak jalan atau lokasi perangkat pengguna.

## Pengujian

- `python -m py_compile website_user/app_user.py` — lulus.
- `pytest -q tests/test_kos_redesign.py tests/test_catalog_dss.py` — **26 passed**.
- Full suite `pytest -q` — **38 passed, 25 failed, 1 error** pada snapshot proyek ini. Kegagalan yang tersisa mencakup tes lama yang merujuk ke variabel/struktur (`app_dev._attempts`, `app_user.PENDING`) yang tidak tersedia pada implementasi aplikasi saat ini, serta satu tes yang memicu konflik konteks ketika menggunakan test client dari dua aplikasi Flask. Dua tes lanjutan juga ikut terdampak karena tes maintenance tersebut tidak menyelesaikan state maintenance. Tes lama tidak dihapus atau dinonaktifkan.

Pengujian browser otomatis untuk localhost tidak dapat diselesaikan di lingkungan eksekusi ini karena akses navigasi lokal diblokir; verifikasi route dan render template dilakukan melalui Flask test client.

## Menjalankan

1. Pasang dependency: `pip install -r requirements.txt`.
2. Salin `.env.example` menjadi `.env`, lalu atur nilai secret key dan konfigurasi yang diperlukan untuk lingkungan Anda. File `.env` lokal tidak disertakan dalam paket.
3. Dari root proyek, jalankan: `python website_user/app_user.py`.
4. Buka `http://127.0.0.1:5000/category/kos` setelah server berjalan dan sesi login sesuai kebijakan akses aplikasi.

Tailwind CSS dan Alpine.js tetap menggunakan pola pemuatan proyek yang sudah ada. Fitur reservasi/pembayaran, favorit, peta interaktif, dan kontak yang sumber validnya tidak tersedia tidak ditampilkan sebagai fitur aktif.
