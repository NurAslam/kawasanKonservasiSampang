# app.py
import streamlit as st
import geopandas as gpd
import folium
import pandas as pd
from folium import GeoJsonPopup, GeoJsonTooltip
from streamlit_folium import st_folium
import json
import ee
import geemap  # Pastikan ini diimpor langsung

# --- Konfigurasi Halaman ---
st.set_page_config(
    page_title="Peta Sampang",
    layout="wide",
    initial_sidebar_state="collapsed"  # Sembunyikan sidebar
)

# --- Sembunyikan Elemen UI Streamlit ---
hide_streamlit_style = """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    [data-testid="stToolbar"] {visibility: hidden;}
    .block-container {padding-top: 0rem; padding-bottom: 0rem; padding-left: 0rem; padding-right: 0rem;}
    iframe {height: 100vh !important; width: 100vw !important; border: none !important;}
    body, html {margin: 0; padding: 0; background: #000; overflow: hidden;}
    </style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# --- Judul Aplikasi ---
st.title("🌿 Informasi Kawasan Konservasi & Perubahan Pesisir di Sampang, Madura")

# --- Inisialisasi Google Earth Engine ---
@st.cache_resource
@st.cache_resource
def init_ee():
    testing = "testing-460608"

    try:
        ee.Initialize(project=testing)
    except:
        ee.Authenticate()
        ee.Initialize(project=testing)
init_ee()

# --- Baca Data SHP: Kawasan Konservasi ---
@st.cache_data
def load_shp_data():
    try:
        gdf = gpd.read_file("./Kawasan_Konservasi/Kawasan_Konservasi.shp")
        roi_bounds = (113.35, -7.22, 113.38, -7.19)  # Wilayah Sampang
        roi_box = gpd.GeoDataFrame(geometry=[gpd.GeoSeries.from_xy([roi_bounds[0]], [roi_bounds[1]], crs=4326).envelope[0]], crs=4326)
        roi_box = roi_box.to_crs(gdf.crs).geometry.iloc[0]
        gdf_roi = gdf[gdf.intersects(roi_box)]
        return gdf_roi.to_crs(epsg=4326)
    except Exception as e:
        st.error(f"Error membaca file SHP: {e}")
        return None

konservasi_roi = load_shp_data()

if konservasi_roi is None or len(konservasi_roi) == 0:
    st.warning("Tidak ada data kawasan konservasi di wilayah Sampang atau file tidak ditemukan.")
    st.stop()

# --- Hitung Pusat Peta ---
centroids = konservasi_roi.geometry.centroid
center_lat = centroids.y.mean()
center_lon = centroids.x.mean()

# --- Kolom untuk Popup dan Tooltip ---
columns_to_show = ['NAMOBJ', 'KODKWS', 'JNSRPR', 'WKLPR', 'REMARK', 'LUASHA']

gdf_display = konservasi_roi.copy()
if 'LUASHA' in gdf_display.columns:
    gdf_display['LUASHA'] = pd.to_numeric(gdf_display['LUASHA'], errors='coerce')

for col in gdf_display.columns:
    if col != 'geometry':
        gdf_display[col] = gdf_display[col].astype(str).replace('<NA>', '').replace('nan', '-')

# --- Buat Peta Folium ---
m = folium.Map(
    location=[center_lat, center_lon],
    zoom_start=14,
    tiles=None
)

# --- Tambahkan Base Layers ---
folium.TileLayer('OpenStreetMap', name='OpenStreetMap').add_to(m)

# ✅ URL Esri yang benar (tanpa spasi)
folium.TileLayer(
    tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attr='Esri, Maxar, Earthstar Geographics, FAO, NOAA, USGS, OpenStreetMap contributors',
    name='Satellite (Esri)',
    max_zoom=19
).add_to(m)

folium.TileLayer(
    tiles='https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    attr='OpenTopoMap',
    name='Topographic'
).add_to(m)

# --- Tambahkan Kawasan Konservasi ---
tooltip = GeoJsonTooltip(
    fields=['NAMOBJ', 'KODKWS', 'LUASHA'],
    aliases=['Nama Kawasan:', 'Kode:', 'Luas (Ha):'],
    localize=True,
    sticky=False,
    labels=True,
    style="""
        background-color: #F0F0F0;
        border: 1px solid black;
        border-radius: 3px;
        padding: 5px;
        font-family: 'courier new';
        font-size: 14px;
    """
)

popup = GeoJsonPopup(
    fields=columns_to_show,
    aliases=[col.replace('_', ' ') for col in columns_to_show],
    localize=True,
    labels=True,
    style="background-color: #F9F871; padding: 10px; border-radius: 5px; font-family: Arial; font-size: 13px;",
    max_width=300
)

folium.GeoJson(
    konservasi_roi,
    name='Kawasan Konservasi',
    style_function=lambda x: {
        'fillColor': '#32CD32',
        'color': '#228B22',
        'weight': 2,
        'fillOpacity': 0.4
    },
    tooltip=tooltip,
    popup=popup
).add_to(m)

# --- Analisis GEE: Wilayah Air (2015, 2020, 2025) ---
target_years = [2015, 2020, 2024]
roi_ee = ee.Geometry.Rectangle([113.35, -7.22, 113.38, -7.19])

# Warna berbeda untuk tiap tahun
colors = {
    2015: '#4B8BBE',  # Biru muda
    2020: '#306998',  # Biru tua
    2024: '#FFE873'   # Kuning (untuk perubahan terkini)
}

for year in target_years:
    try:
        # Filter citra Sentinel-2
        collection = (ee.ImageCollection("COPERNICUS/S2_HARMONIZED")
                      .filterDate(f'{year}-01-01', f'{year}-12-31')
                      .filterBounds(roi_ee)
                      .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 10)))

        image = collection.median().clip(roi_ee)
        ndwi = image.normalizedDifference(['B3', 'B11']).rename('NDWI')
        water_mask = ndwi.gt(0)

        # Mask dan vektorisasi
        binary_water = water_mask.updateMask(water_mask)
        water_vectors = binary_water.reduceToVectors(
            geometry=roi_ee,
            scale=10,
            geometryType='polygon',
            crs='EPSG:4326',
            maxPixels=1e10
        )

        # Konversi ke GeoJSON
        water_geojson = geemap.ee_to_geojson(water_vectors)

        # Tambahkan ke peta
        folium.GeoJson(
            water_geojson,
            name=f'Air (Tahun {year})',
            style_function=lambda x, col=colors[year]: {
                'color': col,
                'weight': 1.8,
                'fillOpacity': 0.5,
                'fillColor': col
            },
            tooltip=f"Wilayah Air - {year}"
        ).add_to(m)

    except Exception as e:
        st.warning(f"Gagal proses GEE untuk tahun {year}: {e}")

# --- Tambahkan Layer Control dan Klik Koordinat ---
folium.LayerControl(collapsed=False).add_to(m)
folium.LatLngPopup().add_to(m)  # Klik untuk lihat koordinat

# --- Tampilkan Peta di Streamlit ---
st_data = st_folium(m, width="100%", height=1000)

# --- Ekspor Data Kawasan Konservasi ---
with st.expander("📥 Ekspor Data Kawasan di Sampang"):
    geojson_data = json.loads(konservasi_roi.to_json())
    st.download_button(
        label="Download sebagai GeoJSON",
        data=json.dumps(geojson_data, indent=2),
        file_name="kawasan_konservasi_sampang.geojson",
        mime="application/json"
    )