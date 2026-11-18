# UNeed Malang - Versi 6 Kategori + DSS Terintegrasi
File ZIP ini berisi seluruh struktur proyek UNeed yang sudah diupdate.

## Perubahan yang Telah Dilakukan:
1. **Frontend & Backend (6 Kategori):**
   - Kategori `mal` ("Pusat Perbelanjaan & Mall") telah ditambahkan ke `website_user/app_user.py`, `website_dev/app_dev.py`, dan `shared/catalog.py`.
   - Grid kategori di `website_user/static/css/style.css` diperbarui menjadi responsif 3-kolom.
2. **DSS 6 Variabel:**
   - Modul `shared/dss.py` ditulis ulang agar support 6 kriteria lengkap beserta error-handling.
   - Konfigurasi bobot default 16.6% untuk 6 variabel diterapkan di `shared/dss_config.json`.
3. **Dataset:**
   - `mal.csv` kini terbaca secara otomatis lewat perulangan kategori saat server Flask dijalankan.

## Cara Menjalankan:
1. Ekstrak `UNeed-Updated-Final.zip`.
2. Aktifkan virtual environment Anda.
3. Jalankan `pip install -r requirements.txt`.
4. Jalankan `python website_user/app_user.py` untuk versi User.
5. Jalankan `python website_dev/app_dev.py` untuk versi Dev.
