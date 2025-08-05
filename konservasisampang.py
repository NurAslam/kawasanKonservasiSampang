# app.py
import streamlit as st
import geopandas as gpd
import folium
import pandas as pd
from folium import GeoJsonPopup, GeoJsonTooltip
from streamlit_folium import st_folium
import json
import ee
import geemap  

st.set_page_config(
    page_title="Peta Sampang",
    layout="wide",
    initial_sidebar_state="expanded"
)


st.markdown(
    """
    <style>
    .block-container {
        padding-top: 2rem !important;
        padding-left: 1rem;
        padding-right: 1rem;
    }
    [data-testid="column"] {
        padding-left: 0 !important;
        padding-right: 0 !important;
    }
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    [data-testid="stToolbar"] {visibility: hidden;}
    </style>
    """,
    unsafe_allow_html=True
)

# --- Header di Tengah ---
st.markdown(
    """
    <h1 style='text-align: center; margin-top: 0; padding-top: 1rem;'>
        🌊 Perubahan Pesisir & Kawasan Konservasi di Sampang, Madura
    </h1>
    <p style='text-align: center; color: #aaa; margin-bottom: 1.5rem;'>
        Analisis perubahan wilayah air dan darat (2015–2025) berdasarkan NDWI dari Sentinel-2
    </p>
    """,
    unsafe_allow_html=True
)

# --- Inisialisasi Google Earth Engine ---
@st.cache_resource
def init_ee():
    try:
        # Cek apakah ada secrets (di Streamlit Cloud)
        service_account = st.secrets["EE_SERVICE_ACCOUNT"]
        private_key = st.secrets["EE_PRIVATE_KEY"]

        credentials = ee.ServiceAccountCredentials(service_account, key_data=private_key)
        ee.Initialize(credentials)
        print("✅ GEE: Berhasil login dengan Service Account")
    except Exception as e:
        st.error("Gagal login ke Google Earth Engine. Pastikan secrets sudah benar.")
        st.stop()
init_ee()

@st.cache_data
def load_shp_data():
    try:
        gdf = gpd.read_file("./Kawasan_Konservasi/Kawasan_Konservasi.shp")
        gdf = gdf.to_crs(epsg=4326)

        from shapely.geometry import box
        roi_box = box(113.35, -7.22, 113.38, -7.19)
        gdf_clipped = gpd.overlay(gdf, gpd.GeoDataFrame([{'geometry': roi_box}], crs=4326), how='intersection')

        if gdf_clipped.empty:
            return None
        return gdf_clipped
    except Exception as e:
        st.error(f"Error membaca atau memotong SHP: {e}")
        return None

konservasi_roi = load_shp_data()

if konservasi_roi is None or konservasi_roi.empty:
    st.warning("Tidak ada data kawasan konservasi di wilayah Sampang.")
    st.stop()

# --- Hitung pusat peta ---
centroids = konservasi_roi.geometry.centroid
center_lat = centroids.y.mean()
center_lon = centroids.x.mean()

# --- Kolom untuk popup ---
columns_to_show = ['NAMOBJ', 'KODKWS', 'JNSRPR', 'WKLPR', 'REMARK', 'LUASHA']
gdf_display = konservasi_roi.copy()
if 'LUASHA' in gdf_display.columns:
    gdf_display['LUASHA'] = pd.to_numeric(gdf_display['LUASHA'], errors='coerce')
for col in gdf_display.columns:
    if col != 'geometry':
        gdf_display[col] = gdf_display[col].astype(str).replace('<NA>', '').replace('nan', '-')

# --- Konversi kawasan konservasi ke EE Geometry ---
@st.cache_resource
def get_conservation_geometry():
    try:
        geojson_data = json.loads(konservasi_roi.to_json())
        features = [ee.Feature(ee.Geometry(f['geometry'])) for f in geojson_data['features']]
        return ee.FeatureCollection(features).geometry()
    except Exception as e:
        st.error(f"Gagal konversi konservasi ke EE: {e}")
        return None

konservasi_ee = get_conservation_geometry()
if konservasi_ee is None:
    st.stop()

# --- Hitung Statistik: Air, Darat, Darat di Konservasi ---
@st.cache_data
def compute_area_stats(year):
    try:
        roi_ee = ee.Geometry.Rectangle([113.35, -7.22, 113.38, -7.19])
        collection = (ee.ImageCollection("COPERNICUS/S2_HARMONIZED")
                      .filterDate(f'{year}-01-01', f'{year}-12-31')
                      .filterBounds(roi_ee)
                      .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 10)))
        image = collection.median()
        ndwi = image.normalizedDifference(['B3', 'B11'])
        water_mask = ndwi.gt(0)
        land_mask = ndwi.lte(0)
        pixel_area = ee.Image.pixelArea()

        # Luas total
        water_area = water_mask.multiply(pixel_area).reduceRegion(reducer=ee.Reducer.sum(), geometry=roi_ee, scale=10, maxPixels=1e10)
        land_area = land_mask.multiply(pixel_area).reduceRegion(reducer=ee.Reducer.sum(), geometry=roi_ee, scale=10, maxPixels=1e10)

        # Luas darat di kawasan konservasi
        land_in_cons = land_mask.multiply(pixel_area).reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=konservasi_ee.intersection(roi_ee, 10),
            scale=10,
            maxPixels=1e10
        )

        water_ha = (water_area.get('nd').getInfo() or 0) / 10000
        land_ha = (land_area.get('nd').getInfo() or 0) / 10000
        land_cons_ha = (land_in_cons.get('nd').getInfo() or 0) / 10000

        return {
            "Tahun": year,
            "Luas Air (Ha)": round(water_ha, 2),
            "Luas Darat (Ha)": round(land_ha, 2),
            "Darat di Konservasi (Ha)": round(land_cons_ha, 2)
        }
    except Exception as e:
        st.warning(f"Gagal hitung statistik {year}: {e}")
        return {"Tahun": year, "Luas Air (Ha)": 0, "Luas Darat (Ha)": 0, "Darat di Konservasi (Ha)": 0}

# --- Hitung Statistik untuk Tahun Target ---
target_years = [2015, 2020, 2025]
stats_data = [compute_area_stats(year) for year in target_years]
df_stats = pd.DataFrame(stats_data)

# --- Buat Peta ---
m = folium.Map(location=[center_lat, center_lon], zoom_start=14, tiles=None)

# --- Base Layers ---
folium.TileLayer('OpenStreetMap', name='OpenStreetMap').add_to(m)

folium.TileLayer(
    tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
    attr='Google Satellite',
    name='Satellite (Google)',
    max_zoom=19
).add_to(m)

folium.TileLayer(
    tiles='https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    attr='OpenTopoMap',
    name='Topographic'
).add_to(m)

# --- Tambahkan Kawasan Konservasi (sudah dipotong) ---
folium.GeoJson(
    konservasi_roi,
    name='Kawasan Konservasi (ROI)',
    style_function=lambda x: {
        'fillColor': '#FFD700',
        'color': '#FF8C00',
        'weight': 3,
        'fillOpacity': 0.5
    },
    tooltip=GeoJsonTooltip(fields=['NAMOBJ', 'LUASHA'], aliases=['Nama:', 'Luas (Ha):']),
    popup=GeoJsonPopup(
        fields=columns_to_show,
        aliases=[c.replace('_', ' ') for c in columns_to_show],
        localize=True,
        labels=True,
        style="background-color: #FFFACD; font-size: 13px; padding: 8px;"
    )
).add_to(m)

# --- Warna ---
colors_water = {2015: '#4B8BBE', 2020: '#306998', 2025: '#FFE873'}
colors_land = {2015: '#2E8B57', 2020: '#228B22', 2025: '#8B4513'}
colors_cons_land = {2015: '#DC143C', 2020: '#B22222', 2025: '#8B0000'}

roi_ee = ee.Geometry.Rectangle([113.35, -7.22, 113.38, -7.19])

# --- Tambahkan Layer Air, Darat, dan Darat di Konservasi per Tahun ---
for year in target_years:
    try:
        collection = (ee.ImageCollection("COPERNICUS/S2_HARMONIZED")
                      .filterDate(f'{year}-01-01', f'{year}-12-31')
                      .filterBounds(roi_ee)
                      .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 10)))
        image = collection.median().clip(roi_ee)
        ndwi = image.normalizedDifference(['B3', 'B11'])
        water_mask = ndwi.gt(0)
        land_mask = ndwi.lte(0)

        # Air
        water_vectors = water_mask.updateMask(water_mask).reduceToVectors(geometry=roi_ee, scale=10, maxPixels=1e10, crs='EPSG:4326')
        folium.GeoJson(
            geemap.ee_to_geojson(water_vectors),
            name=f'Air ({year})',
            style_function=lambda x, c=colors_water[year]: {'color': c, 'weight': 1.8, 'fillOpacity': 0.5, 'fillColor': c}
        ).add_to(m)

        # Darat
        land_vectors = land_mask.updateMask(land_mask).reduceToVectors(geometry=roi_ee, scale=10, maxPixels=1e10, crs='EPSG:4326')
        folium.GeoJson(
            geemap.ee_to_geojson(land_vectors),
            name=f'Darat ({year})',
            style_function=lambda x, c=colors_land[year]: {'color': c, 'weight': 1.8, 'fillOpacity': 0.4, 'fillColor': c}
        ).add_to(m)

        # 🔥 Darat di Kawasan Konservasi
        land_in_cons_mask = land_mask.updateMask(land_mask).clip(konservasi_ee)
        land_in_cons_vectors = land_in_cons_mask.reduceToVectors(
            geometry=roi_ee,
            scale=10,
            geometryType='polygon',
            crs='EPSG:4326',
            maxPixels=1e10
        )
        land_cons_geojson = geemap.ee_to_geojson(land_in_cons_vectors)

        folium.GeoJson(
            land_cons_geojson,
            name=f'Darat di Konservasi ({year})',
            style_function=lambda x, c=colors_cons_land[year]: {
                'color': c,
                'weight': 2.5,
                'fillColor': c,
                'fillOpacity': 0.6
            },
            tooltip=f"Darat di Konservasi - {year}"
        ).add_to(m)

    except Exception as e:
        st.warning(f"Gagal proses layer untuk {year}: {e}")

# --- Layer Control dan Klik Koordinat ---
folium.LayerControl(collapsed=False).add_to(m)
folium.LatLngPopup().add_to(m)

# --- Layout: 60% Peta, 40% Statistik ---
col_map, col_stats = st.columns([6, 4])

with col_map:
    st_folium(m, width="100%", height=1000)

with col_stats:
    st.subheader("📊 Statistik Perubahan Wilayah")
    st.dataframe(df_stats, use_container_width=True)

    st.subheader("📈 Luas Darat di Kawasan Konservasi")
    st.bar_chart(df_stats.set_index("Tahun")[["Darat di Konservasi (Ha)"]])

    Tentu, berikut adalah kode biasa (tanpa format markdown) untuk menampilkan insight dari data statistik satu per satu menggunakan `st.expander`:

    st.subheader("🔍 Insight dari Data")
    

    insights = [
        "🌊 <b>Wilayah air menyusut, darat bertambah</b><br>"
        "Dalam 10 tahun (2015–2025), luas air berkurang 5,17 Ha, sementara darat bertambah 5,17 Ha. "
        "Ini menunjukkan konversi langsung dari air ke darat, kemungkinan akibat reklamasi atau sedimentasi.",
    
        "🌳 <b>Darat di kawasan konservasi meningkat tajam</b><br>"
        "Luas darat di dalam kawasan konservasi naik dari 7,69 Ha (2015) menjadi 12,83 Ha (2025). "
        "Padahal kawasan ini seharusnya terlindungi — ini indikasi kuat adanya aktivitas manusia atau perubahan lingkungan yang signifikan.",
    
        "🏗️ <b>Indikasi aktivitas manusia</b><br>"
        "Peningkatan darat di konservasi bisa disebabkan oleh perluasan tambak, reklamasi, atau penambangan laut. "
        "Perlu verifikasi lapangan dan kajian tata ruang pesisir.",
    
        "📈 <b>Laju perubahan semakin cepat</b><br>"
        "Pertambahan darat di konservasi lebih cepat di periode 2020–2025 (+3,13 Ha) dibanding 2015–2020 (+2,01 Ha). "
        "Artinya: perubahan sedang mempercepat."
    ]

# Tampilkan satu per satu dengan expander
    for i, insight in enumerate(insights, 1):
        with st.expander(f"💡 Insight {i}"):
            st.markdown(insight, unsafe_allow_html=True)
