import math
import pandas as pd

# --- 1. HAVERSINE FORMULA UNTUK JARAK (COST) ---
def hitung_jarak_haversine(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2): return None
    try:
        R = 6371.0
        dlat = math.radians(float(lat2) - float(lat1))
        dlon = math.radians(float(lon2) - float(lon1))
        a = math.sin(dlat / 2)**2 + math.cos(math.radians(float(lat1))) * math.cos(math.radians(float(lat2))) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c
    except (ValueError, TypeError):
        return None

# --- 2. BAYESIAN AVERAGE RATING (BENEFIT) ---
def hitung_bayesian_rating(rating_aktual, jumlah_ulasan, m=10, C=3.5):
    if rating_aktual is None: return None
    if not jumlah_ulasan or jumlah_ulasan == 0: return float(rating_aktual)
    v = float(jumlah_ulasan)
    R = float(rating_aktual)
    return (v / (v + m)) * R + (m / (v + m)) * C

# --- 3. NORMALISASI SAW (COST & BENEFIT) ---
def normalisasi_saw(data_alternatif, kriteria_aktif):
    min_max = {}
    for kriteria in kriteria_aktif.keys():
        nilai_valid = [alt[kriteria] for alt in data_alternatif if alt.get(kriteria) is not None and alt[kriteria] >= 0]
        if nilai_valid:
            min_max[kriteria] = {'min': min(nilai_valid), 'max': max(nilai_valid)}
        else:
            min_max[kriteria] = None

    hasil_normalisasi = []
    for alt in data_alternatif:
        alt_norm = {'id': alt.get('id', ''), 'nama': alt.get('nama', ''), 'data_asli': alt}
        is_lengkap = True
        
        for kriteria, tipe in kriteria_aktif.items():
            val = alt.get(kriteria)
            if val is None or val < 0 or min_max[kriteria] is None:
                alt_norm[kriteria] = None
                is_lengkap = False
                continue
                
            cmin = min_max[kriteria]['min']
            cmax = min_max[kriteria]['max']
            
            if cmin == cmax:
                alt_norm[kriteria] = 1.0 
            elif tipe == 'cost':
                alt_norm[kriteria] = cmin / val if val != 0 else 1.0
            else:
                alt_norm[kriteria] = val / cmax if cmax != 0 else 0.0
                
        alt_norm['is_lengkap'] = is_lengkap
        hasil_normalisasi.append(alt_norm)
    return hasil_normalisasi

# --- 4. PERHITUNGAN SKOR AKHIR SAW ---
def hitung_skor_akhir(hasil_normalisasi, bobot_kriteria):
    total_bobot = sum(bobot_kriteria.values())
    bobot_presisi = {k: v / total_bobot for k, v in bobot_kriteria.items()}
    
    hasil_ranking = []
    for alt in hasil_normalisasi:
        skor = 0.0
        if not alt['is_lengkap']:
            hasil_ranking.append({'id': alt.get('id', ''), 'nama': alt.get('nama', ''), 'skor': 0, 'status': 'Data Tidak Lengkap', 'detail': alt})
            continue
            
        for kriteria, bobot in bobot_presisi.items():
            skor += alt[kriteria] * bobot
            
        hasil_ranking.append({'id': alt.get('id', ''), 'nama': alt.get('nama', ''), 'skor': round(skor, 4), 'status': 'Sukses', 'detail': alt})
        
    return sorted(hasil_ranking, key=lambda x: x['skor'], reverse=True)
