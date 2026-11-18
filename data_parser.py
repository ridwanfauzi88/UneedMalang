import pandas as pd

def hitung_skor_fasilitas(row, kategori):
    # Implementasikan logika rating fasilitas 1-5 berdasarkan checklist
    if pd.isna(row.get('Fasilitas')): return 0.0
    jumlah_fasilitas = len(str(row.get('Fasilitas')).split(','))
    return min(5.0, max(1.0, float(jumlah_fasilitas)))

def baca_dan_standarisasi_csv(filepath, kategori):
    df = pd.read_csv(filepath, encoding='utf-8-sig')
    
    # Standarisasi Nama
    for col in ['Nama Toko', 'Nama Tempat', 'Nama Klinik', 'Nama Kos', 'Nama']:
        if col in df.columns:
            df.rename(columns={col: 'nama'}, inplace=True)
            break
            
    # Standarisasi Rating
    if 'Rating Maps' in df.columns: df.rename(columns={'Rating Maps': 'raw_rating'}, inplace=True)
    elif 'Rating' in df.columns: df.rename(columns={'Rating': 'raw_rating'}, inplace=True)
    
    data_terstandar = []
    for _, row in df.iterrows():
        data_terstandar.append({
            'nama': row.get('nama', 'Tanpa Nama'),
            'harga': float(row['Harga']) if 'Harga' in df.columns and pd.notna(row['Harga']) else None,
            'lat': float(row['Latitude']) if 'Latitude' in df.columns and pd.notna(row['Latitude']) else None,
            'lon': float(row['Longitude']) if 'Longitude' in df.columns and pd.notna(row['Longitude']) else None,
            'fasilitas': hitung_skor_fasilitas(row, kategori), 
            'raw_rating': float(row['raw_rating']) if pd.notna(row.get('raw_rating')) else None,
            'reviews': int(row['Jumlah Ulasan']) if 'Jumlah Ulasan' in df.columns and pd.notna(row['Jumlah Ulasan']) else 0,
            'kenyamanan': float(row['Kenyamanan']) if 'Kenyamanan' in df.columns and pd.notna(row['Kenyamanan']) else None,
            'keamanan': float(row['Keamanan']) if 'Keamanan' in df.columns and pd.notna(row['Keamanan']) else None,
        })
    return data_terstandar
