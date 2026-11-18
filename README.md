# UNeed Malang

Aplikasi katalog layanan lokal Malang untuk khalayak umum, dengan dua aplikasi Flask yang tetap terpisah: `website_user` untuk masyarakat/pengguna dan `website_dev` sebagai panel operasional website user.

Dokumentasi perubahan panel operasional developer: [`OPERATIONS_PANEL_REPORT.md`](OPERATIONS_PANEL_REPORT.md).

## Kebutuhan

- Python 3.10 atau lebih baru
- pip
- Akses SMTP yang valid jika ingin mengaktifkan pengiriman OTP pendaftaran dan tautan reset kata sandi

Dependensi dipertahankan pada Flask, Jinja2, pandas, Tailwind CSS CDN, Alpine.js, dan Flask-Mail. pandas digunakan untuk membaca dataset CSV serta Excel. Saat ini tampilan Tailwind CSS dan Alpine.js diambil dari CDN, sehingga koneksi internet diperlukan untuk memuat aset tersebut.

## Instalasi

Jalankan perintah dari folder akar proyek `UNeed-Project`.

```bash
python -m venv .venv
```

Aktifkan virtual environment:

- Windows PowerShell: `.venv\Scripts\Activate.ps1`
- macOS/Linux: `source .venv/bin/activate`

Pasang dependensi dan siapkan konfigurasi:

```bash
python -m pip install -r requirements.txt
```

Salin `.env.example` menjadi `.env`. Ganti `UNEED_SECRET_KEY`, `UNEED_DEV_SECRET_KEY`, dan `UNEED_DEV_KEY` dengan nilai acak yang tidak dipakai di tempat lain. Contoh pembangkit secret:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Untuk mengaktifkan OTP dan reset kata sandi, isi `MAIL_USERNAME`, `MAIL_PASSWORD`, dan `MAIL_DEFAULT_SENDER`, serta pastikan konfigurasi server, port, TLS/SSL sesuai penyedia email. Pada Gmail, gunakan app password yang dibuat untuk akun pengirim, bukan kata sandi akun utama. Jangan commit file `.env`.

## Menjalankan aplikasi pengguna

Terminal pertama, di folder akar proyek:

```bash
python website_user/app_user.py
```

Buka `http://127.0.0.1:5000/`.

Akun baru dibuat melalui pendaftaran dan baru bisa digunakan setelah kode OTP email berhasil diverifikasi. Aplikasi tidak menyediakan akun atau kata sandi bawaan. Tanpa konfigurasi SMTP, pendaftaran baru akan menampilkan pesan bahwa OTP belum dapat dikirim. Login dan pengelolaan sesi tetap tersedia bagi akun yang sudah dibuat pada database lokal.

## Menjalankan konsol developer

Terminal kedua:

```bash
python website_dev/app_dev.py
```

Buka `http://127.0.0.1:5001/dev/login`. Kunci akses wajib disediakan lewat `UNEED_DEV_KEY`. Tidak ada kunci default yang ditanam di JavaScript atau repository. Aplikasi developer berjalan di `127.0.0.1` secara default dan sebaiknya tetap dibatasi ke mesin lokal kecuali tersedia autentikasi jaringan tambahan.

Konsol developer menggunakan identitas visual UNeed dan menjadi panel operasional website user. Dashboard menyediakan jumlah baris per kategori, status maintenance, jumlah akun, ringkasan page view 24 jam, serta antrean pengajuan masyarakat. Menu Aktivitas User menampilkan akun terdaftar dan riwayat page view; menu Pengajuan Data digunakan untuk menyetujui atau menolak data baru sebelum masuk katalog. Konfigurasi DSS tetap tersedia di menu terpisah. Jumlah baris merupakan hasil pembacaan file pada saat halaman dibuka, bukan jumlah entri yang pasti valid atau metrik kesehatan seluruh layanan. Di halaman konfigurasi, total bobot diperbarui secara langsung saat nilai diubah. Tombol `Pulihkan nilai awal` mengisi kembali nilai awal di form; tekan `Simpan konfigurasi` untuk benar-benar menyimpannya.

Pengaturan m, C, dan bobot SAW disimpan pada `instance/dss_config.json`. Kedua aplikasi membaca file konfigurasi yang sama. Mode maintenance disimpan pada `instance/site_config.json`; developer bisa mengaktifkan atau menonaktifkannya melalui Dashboard. Ketika aktif, website user menampilkan halaman pemeliharaan sementara, sedangkan website developer tetap dapat diakses. Total bobot harga, jarak, dan rating harus 100%.

## Data dan autentikasi

- Dataset asli di `website_user/uploads_excel/` dipertahankan. Tidak ada nilai atau baris dataset yang dibuat untuk menggantikan sumber tersebut.
- Akun, preferensi tampilan, OTP sementara, dan token reset disimpan dalam SQLite lokal pada `website_user/instance/uneed_users.sqlite3` secara default. Folder dan skema database dibuat otomatis saat aplikasi diimpor.
- Kata sandi disimpan sebagai hash Werkzeug. OTP kedaluwarsa setelah 10 menit dan dibatasi maksimal lima percobaan. Token reset kedaluwarsa setelah satu jam dan disimpan dalam bentuk hash.
- Endpoint penggantian file dataset secara langsung melalui HTTP tetap dinonaktifkan. Masyarakat dapat mengajukan informasi tempat/layanan lewat menu `Ajukan Tempat/Layanan`; pengajuan berstatus menunggu sampai developer menyetujuinya. Setelah disetujui, data dimasukkan ke katalog dan pemeringkatan DSS tanpa mengubah CSV sumber asli. Pengguna dapat memantau status lewat `Pengajuan Saya`.
- `UNEED_ENV=production` membutuhkan `UNEED_SECRET_KEY` serta `UNEED_DEV_SECRET_KEY`. Aktifkan `UNEED_COOKIE_SECURE=true` jika aplikasi benar-benar diakses melalui HTTPS.
- Sistem menyimpan page view GET dari website user di tabel `access_logs`. Catatan berisi identitas akun jika pengguna login, endpoint halaman, ID sesi acak, dan waktu. Sistem tidak menyimpan alamat IP, password, isi formulir, atau query string. Beri tahu pengguna tentang pencatatan aktivitas sebelum aplikasi dipublikasikan.

## Navigasi dan preferensi tampilan

- Kelima kategori layanan dibuka melalui menu hamburger pada bilah navigasi. Dashboard menampilkan satu daftar rekomendasi lintas kategori, bukan lima kelompok rekomendasi yang memanjang ke bawah.
- Setiap halaman kategori menyediakan pencarian teks, filter rentang harga, pengurutan, dan pilihan tampilan kartu/tabel. Pencarian dan filter berjalan melalui parameter GET di backend dan dapat digabungkan.
- Halaman profil serta pengaturan menggunakan navigasi bersama dan logo UNeed yang sama.
- Pilihan tema dan ukuran teks dapat dipratinjau langsung, kemudian disimpan ke preferensi akun. Setelah disimpan, preferensi diterapkan kembali pada sesi/login berikutnya.

## Catatan metode DSS

- Harga dan jarak diperlakukan sebagai kriteria cost. Rating diperlakukan sebagai kriteria benefit.
- Nilai cost dinormalisasi menggunakan rentang data pada kategori saat itu. Jika seluruh nilai sama, kriteria tersebut diberi nilai setara. Baris tanpa data kriteria tidak dinilai pada kriteria itu, lalu bobot yang tersedia dinormalisasi ulang untuk baris tersebut.
- Formula rating Bayesian menggunakan jumlah ulasan hanya jika jumlahnya tersedia. Dataset saat ini tidak memiliki kolom jumlah ulasan yang konsisten, sehingga rating Maps mentah dipakai untuk baris yang tidak memiliki jumlah ulasan. Parameter Bayesian tetap disimpan dan akan berpengaruh pada data yang benar-benar menyertakan jumlah ulasan.
- Tanpa izin lokasi, sistem tidak menggunakan pusat kota atau kampus sebagai posisi pengguna. Ketika pengguna mengaktifkan GPS, jarak Haversine dan skor DSS dihitung ulang dari koordinat perangkat; angka ini merupakan jarak garis lurus, bukan jarak jalan atau estimasi waktu perjalanan.
- Skor DSS membantu menyusun urutan dan bukan klaim bahwa suatu tempat pasti terbaik.

## Pengujian

Setelah dependensi terpasang:

```bash
python -m pytest -q
```

Tes mencakup parser harga/rating/koordinat, pemrosesan dataset, skor DSS, perlindungan login/CSRF, pendaftaran OTP melalui mock pengiriman email, dan pembatasan akses konsol developer. Pengiriman email SMTP nyata serta tampilan di browser harus diverifikasi di lingkungan yang menyediakan kredensial SMTP dan browser.

## Struktur utama

```text
UNeed-Project/
├── website_user/
│   ├── app_user.py
│   ├── templates/
│   ├── static/
│   ├── uploads_excel/
│   └── instance/                 # database SQLite yang dibuat saat runtime
├── website_dev/
│   ├── app_dev.py
│   ├── templates/
│   └── static/
├── instance/dss_config.json     # konfigurasi DSS bersama
├── instance/site_config.json    # status maintenance (dibuat saat diubah developer)
├── tests/
├── requirements.txt
└── .env.example
```

## Fitur pencarian lokasi, komunitas, dan membership

### Lokasi dinamis dan Google Maps
- Kontrol **Gunakan Lokasi Saya** meminta izin eksplisit melalui Geolocation API browser. Posisi perangkat berada dalam memori halaman; saat perlu memperbarui DSS, koordinat dikirim melalui POST yang dilindungi CSRF ke endpoint `/api/location-dss` tanpa cache, lalu tidak disimpan dalam profil, session, URL, atau access log.
- Pilihan radius 1/3/5/10 km dan semua hasil menghitung jarak garis lurus dengan Haversine serta mengurutkan kartu berdasarkan jarak. Kartu tanpa koordinat valid tidak memperoleh jarak rekaan. Ketika berada di luar cakupan dataset, gunakan pencarian manual atau perluas radius; hasil tetap dibatasi oleh data yang tersedia.
- Tautan Google Maps/petunjuk arah tersedia jika nama atau alamat valid, dan tujuan memakai koordinat hanya jika koordinat tercatat serta lolos validasi. Tidak ada klaim jarak jalan/waktu tempuh pada UNeed.
- Peta interaktif memakai Leaflet + OpenStreetMap hanya jika `UNEED_ENABLE_INTERACTIVE_MAP=true`. Ini bersifat opsional; tanpa konfigurasi tersebut tombol lokasi dan pengurutan tetap berjalan tanpa peta.
- Geolocation di deployment harus memakai HTTPS (localhost dapat menjadi secure context untuk pengembangan). Jangan mengisi koordinat, foto, harga, ulasan, atau rating yang tidak ada di sumber.

### Rekomendasi komunitas dan moderator
- Pengguna terdaftar mengirim rekomendasi melalui `/community/new`. Rekomendasi masuk status `pending` dan baru tampil di feed publik setelah pengelola menyetujui di konsol terpisah (`/dev/community`).
- Anggota dapat menandai rekomendasi yang membantu (sekali per rekomendasi) dan melaporkan informasi keliru, duplikat, spam, atau informasi menyesatkan. Admin dapat meninjau dan menutup laporan. Kode kontributor dipakai pada leaderboard publik untuk menghindari publikasi nama lengkap anggota.
- Daftar tempat pribadi berada di `/saved-places`. Koleksi menyimpan nama/alamat saat disimpan dan menandai jika ID record tidak ditemukan lagi pada dataset terkini.
- Skema baru ditambahkan saat kedua aplikasi mulai. Migrasi menambah kolom membership pengguna tanpa mengganti framework atau menghapus tabel lama.

### Membership dan penghargaan
- `/membership` menampilkan Basic/Premium sesuai konfigurasi, riwayat poin, badge dan peringkat kontribusi. Konsol pengelola memiliki `/dev/membership` untuk mengatur label/manfaat/harga informasi (boleh kosong), poin dan ambang badge, serta `/dev/membership/users` untuk mengatur tier dan status secara manual.
- Harga/manfaat Premium tidak ditentukan secara otomatis dan gateway pembayaran belum aktif. Status membership tidak memengaruhi skor/rekomendasi organik; model pendapatan yang direncanakan hanya membership.
- Konfigurasi tersimpan pada `instance/membership_config.json` (atau path yang diatur lewat `UNEED_MEMBERSHIP_CONFIG`). Jangan mengisi harga/manfaat sebelum keputusan tim disepakati.

### Pengujian
Jalankan `python -m pytest -q`. Pengujian helper lokasi, schema, dan membership tidak memerlukan Flask. Untuk menjalankan aplikasi atau tes Flask, instal dependensi pada `requirements.txt` ke virtual environment lokal terlebih dahulu. Beberapa berkas tes lama mungkin mengacu pada antarmuka versi aplikasi berbeda; laporan hasil uji harus membedakan kegagalan test lama dari fitur baru.
