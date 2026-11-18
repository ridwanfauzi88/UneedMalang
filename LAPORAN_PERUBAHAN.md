# Laporan Perubahan UNeed Malang

## A. Ringkasan audit (kondisi awal)

| # | Temuan | Dampak |
|---|---|---|
| 1 | `app.secret_key` dan kredensial email tertulis di source code | Keamanan |
| 2 | Login menerima **email/password apa pun**; registrasi tidak menyimpan akun; hapus akun hanya `print` | Autentikasi palsu |
| 3 | OTP ditampilkan di flash ("Testing Code") dan di-hardcode `123456` di JS; "reset password" mengaku mengirim email padahal tidak | Keamanan / klaim palsu |
| 4 | `verify_otp.html` memanggil endpoint `otp_verification` yang tidak ada (BuildError) | Bug |
| 5 | Developer console: login hanya di JavaScript (`admin123` terlihat di source), semua route `/dev/*` terbuka tanpa autentikasi | Keamanan kritis |
| 6 | Parameter DSS developer disimpan di dict memori dan **tidak terhubung** ke website pengguna; "142 active users" adalah angka simulasi | Fitur tidak berfungsi |
| 7 | Rumus DSS di `dss.js`: konstanta `500000/harga`, `0.5/jarak` (bukan normalisasi SAW), pembagian nol, tidak dipakai oleh halaman manapun | Matematis |
| 8 | Dashboard: 15 kartu "Top 3" berisi nama/harga karangan; pencarian tidak berfungsi; halaman detail statis dan palsu; keranjang & komentar tidak berfungsi; pengaturan hanya `alert()` | Data palsu / fitur kosong |
| 9 | Dashboard memuat dua header (navbar dari base + header sendiri); sidebar `components/sidebar.html` tak pernah dipakai dan menautkan `/category/makan` & `/kesehatan` (tidak valid); Font Awesome dipakai tanpa dimuat; `main.js`/`auth.js`/`dss.js`/`style.css` tidak dimuat satupun | Inkonsistensi |
| 10 | Nomor telepon klinik terbaca sebagai float (`8.19e10`); CSV klinik bukan UTF-8; baris kerangka kosong tampil sebagai "Tanpa Nama"; `href` CSV hanya dicek `'http' in ...` (celah `javascript:`) | Data / XSS |
| 11 | Dataset tidak memiliki kolom jumlah ulasan, padahal Bayesian membutuhkannya; kos hampir kosong (3 baris berisi) | Keterbatasan data |
| 12 | Kedua aplikasi memakai cookie `session` yang sama pada host yang sama (cookie tidak dibedakan port) | Konflik sesi |
| 13 | `debug=True` permanen, tidak ada CSRF, tidak ada pembatasan percobaan login | Keamanan |

## B–C. Implementasi dan ringkasan perubahan

**Struktur dipertahankan** (`website_user/`, `website_dev/`, `templates/`, `components/`, `static/`, `uploads_excel/`). Penambahan: folder `shared/` (kode bersama), `tests/`, `README.md`, `.env.example`, `requirements.txt`, `.gitignore`, template `search.html`, `reset_password.html`, `error.html`, `components/macros.html`, `components/cart.html`, `dev_error.html`.
Dataset CSV dan gambar **tidak diubah** (diverifikasi byte-per-byte).

**Backend pengguna (`app_user.py`)**
* Konfigurasi dari environment; `SECRET_KEY` wajib di production; cookie `HttpOnly`/`SameSite`/`Secure`; cookie bernama unik.
* Akun nyata (hash password `werkzeug`, disimpan di `instance/users.json`); registrasi OTP dengan kedaluwarsa 10 menit, maksimum 5 percobaan, jeda kirim ulang, dan batas permintaan; reset password via tautan bertanda tangan sekali pakai; ganti password; hapus akun dengan konfirmasi password; logout via POST.
* CSRF di semua form POST, pembatasan percobaan login (5/5 menit), respons seragam agar akun tidak dapat ditebak, header keamanan dasar, error handler 400/403/404/405/413/500.
* SMTP memakai `smtplib` standar (dependensi Flask-Mail dihapus). Bila SMTP belum diisi: production menolak dengan pesan jelas; development menampilkan kode di layar dengan label "Mode pengembangan".
* Rute baru: `/search`, `/detail/<kategori>/<id>` (+ulasan), `/resend-otp`, `/reset-password/<token>`, `/profile/password`. Alias lama `makan`→`warung`, `kesehatan`→`klinik` dialihkan. Query filter yang ada (`sort`, `min_harga`, `max_harga`, `view`) dipertahankan; ditambah `sort=rekomendasi` dan `w_price/w_dist/w_rating`.

**Data (`shared/catalog.py`)** — membaca semua nilai sebagai teks (nomor telepon tidak rusak), deteksi encoding & delimiter, abaikan baris kosong/"Total terdata", validasi harga/rating/koordinat (di luar Indonesia ditolak), `safe_url` (hanya http/https), file hilang/rusak/kosong tidak menjatuhkan halaman, catatan kualitas data ditampilkan di halaman kategori.

**DSS (`shared/dss.py`)** — lihat bagian berikut.

**Developer console** — login server-side (tanpa kata sandi bawaan; hash dari env atau kata sandi acak yang dicetak di terminal), CSRF, pembatasan percobaan, bind `127.0.0.1`, logout POST. Dashboard menampilkan metrik nyata (status baca dataset, kelengkapan data, jumlah akun/ulasan, parameter aktif); angka simulasi dihapus. Konfigurasi DSS divalidasi, disimpan atomik ke `shared/dss_config.json`, dan **langsung dipakai website pengguna**; ada pratinjau peringkat.

**UI/UX** — satu layout (`base.html`) dengan navbar, sidebar (menu aktif, tutup via Esc/overlay), keranjang pilihan, dan footer bersama; kartu tempat dari satu macro (kategori, dashboard, pencarian); dashboard Top 3 dihitung dari dataset; pencarian, halaman detail (jarak, tempat terdekat per kategori, skor & penjelasan DSS, ulasan), tabel responsif dengan header lengket, modal filter dengan slider bobot; halaman auth seragam (label, tampilkan password, indikator kekuatan, cooldown OTP, cegah kirim ganda); pengaturan (ukuran font, warna aksen, mode gelap) benar-benar berfungsi dan tersimpan di browser. Atribut ARIA, fokus terlihat, tautan "lewati ke konten", dan `prefers-reduced-motion` ditambahkan.

## Perubahan DSS (dijelaskan dan terukur)

| Aspek | Sebelumnya | Sekarang | Alasan |
|---|---|---|---|
| Normalisasi | `500000/harga`, `0.5/jarak`, rating/5 (tak terbatas) | SAW: cost `min/x`, benefit `x/max` pada kandidat yang dibandingkan | Skor lama bukan SAW, konstanta sembarang, bisa meledak |
| Bobot | 3 slider tanpa konfigurasi pusat | Bobot default di konfigurasi developer, total wajib 100%; bobot pengguna dinormalkan; bobot invalid → default | Konsistensi & keamanan input |
| Nilai kosong/0/negatif | Pembagian nol/NaN | Dibuang dari perhitungan; jarak minimal 0,05 km; tempat dengan < 2 kriteria valid **tidak diberi skor** | Hindari rekomendasi menyesatkan |
| Bayesian | rumus benar tetapi `v` tak tersedia | Rumus sama; `v` dari kolom `Jumlah Ulasan` bila ada, selain itu **asumsi `v` (default 20)** yang ditampilkan eksplisit di UI | Dataset tidak memiliki jumlah ulasan |
| Frontend vs backend | Rumus di JS | Hanya backend yang menghitung; JS hanya menampilkan bobot | Satu sumber kebenaran |

Rumus Bayesian (`m=5`, `C=4.2`) dan kriteria asli tidak diubah. **Catatan jujur:** bobot default 40/30/30 adalah pilihan saya karena versi awal tidak punya nilai default tersimpan; ubah di console sesuai kebutuhan. Skor tempat dengan data tidak lengkap dihitung dari kriteria yang tersedia (bobot dinormalkan ulang) dan diberi penanda "data tidak lengkap (n/3)".

## D. Hasil pengujian (aktual)

* **39 tes otomatis lulus** (`python -m unittest discover -s tests`): parsing (harga, rating, koordinat, telepon, URL), loader terhadap dataset nyata + file hilang/kosong/header-saja/kolom salah, matematika DSS (Bayesian, normalisasi, bobot, nilai tidak valid, satu kandidat), validasi & simpan konfigurasi (termasuk file rusak), alur registrasi-OTP-login, CSRF, rate limit, reset password sekali pakai, ulasan (termasuk escape HTML), ganti password, hapus akun, proteksi route developer, kata sandi bawaan lama (`admin123`) ditolak, konfigurasi developer terbaca sisi pengguna.
* Kedua server dijalankan sungguhan (`python website_user/app_user.py`, `python website_dev/app_dev.py`): halaman login 200, route terproteksi mengalihkan, static 200.
* Uji tata letak Chromium headless pada 375, 768, 1366, 1920 px untuk 11 halaman (44 kombinasi): **tidak ada horizontal overflow dan tidak ada error JavaScript**.

**Yang BELUM teruji / batasan pengujian**
* Sandbox tidak memiliki internet, sehingga Tailwind (dikompilasi lokal dengan versi yang sama, 3.4) dan **Alpine.js tidak dimuat**. Interaksi Alpine (buka/tutup sidebar, keranjang, modal filter, tombol tabel/kartu, halaman pengaturan) **belum saya uji di browser**; kode ditulis ulang dengan hati-hati tetapi perlu Anda coba sekali.
* Pengiriman email SMTP nyata tidak diuji (tidak ada kredensial/jaringan); hanya jalur "belum dikonfigurasi" dan mode development yang diuji.
* Tampilan mode gelap dan tema warna belum diperiksa visual.

## Risiko / keterbatasan yang diketahui
1. Penyimpanan akun/ulasan berupa file JSON — cukup untuk skala kecil, bukan untuk banyak pengguna bersamaan. Tingkatkan ke SQLite/PostgreSQL bila perlu.
2. Rate limit dan pendaftaran tertunda disimpan di memori proses (hilang saat restart; tidak dibagi antar worker gunicorn).
3. Tailwind & Alpine dari CDN (Tailwind CDN tidak disarankan untuk production): bangun CSS dengan Tailwind CLI dan hosting lokal untuk production.
4. Kos hanya memiliki 3 baris berisi (13 baris tanpa nama) dan tidak punya harga; dataset belum memiliki `Jumlah Ulasan`. Menambah kolom tersebut akan langsung dipakai DSS.
5. Fitur unggah dataset publik tetap dinonaktifkan seperti rancangan awal (`/upload_excel/*` → ditolak); tidak ada unggahan di console, sehingga validasi unggahan tidak relevan.
6. Perubahan perilaku: `/logout` kini hanya POST; semua akun lama tidak ada (versi awal tidak menyimpan akun) — daftar ulang atau pakai `flask create-user`.
