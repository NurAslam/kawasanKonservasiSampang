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

# --- Konfigurasi Halaman ---
st.set_page_config(
    page_title="Peta Sampang",
    layout="wide",
    initial_sidebar_state="expanded"  # Sembunyikan sidebar
)

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
        roi_bounds = (113.35, -7.22, 113.38, -7.19)
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

centroids = konservasi_roi.geometry.centroid
center_lat = centroids.y.mean()
center_lon = centroids.x.mean()

columns_to_show = ['NAMOBJ', 'KODKWS', 'JNSRPR', 'WKLPR', 'REMARK', 'LUASHA']
gdf_display = konservasi_roi.copy()
if 'LUASHA' in gdf_display.columns:
    gdf_display['LUASHA'] = pd.to_numeric(gdf_display['LUASHA'], errors='coerce')
for col in gdf_display.columns:
    if col != 'geometry':
        gdf_display[col] = gdf_display[col].astype(str).replace('<NA>', '').replace('nan', '-')

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

        # Hitung luas (m² → ha)
        pixel_area = ee.Image.pixelArea()
        water_area_m2 = water_mask.multiply(pixel_area).reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=roi_ee,
            scale=10,
            maxPixels=1e10
        )
        land_area_m2 = land_mask.multiply(pixel_area).reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=roi_ee,
            scale=10,
            maxPixels=1e10
        )

        water_area_ha = water_area_m2.get('nd').getInfo() / 10000 if water_area_m2.get('nd').getInfo() else 0
        land_area_ha = land_area_m2.get('nd').getInfo() / 10000 if land_area_m2.get('nd').getInfo() else 0

        return water_area_ha, land_area_ha
    except:
        return None, None

# --- Hitung Statistik untuk Tahun Target ---
target_years = [2015, 2020, 2025]
area_data = []

for year in target_years:
    water_ha, land_ha = compute_area_stats(year)
    area_data.append({
        "Tahun": year,
        "Luas Air (Ha)": round(water_ha, 2) if water_ha else 0,
        "Luas Darat (Ha)": round(land_ha, 2) if land_ha else 0
    })

df_stats = pd.DataFrame(area_data)


m = folium.Map(
    location=[center_lat, center_lon],
    zoom_start=14,
    tiles=None
)


folium.TileLayer('OpenStreetMap', name='OpenStreetMap').add_to(m)

# Gunakan alternatif jika Esri error 400
try:
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Esri',
        name='Satellite (Esri)',
        max_zoom=19
    ).add_to(m)
except:
    pass

folium.TileLayer(
    tiles='https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    attr='OpenTopoMap',
    name='Topographic'
).add_to(m)


tooltip = GeoJsonTooltip(
    fields=['NAMOBJ', 'KODKWS', 'LUASHA'],
    aliases=['Nama:', 'Kode:', 'Luas (Ha):'],
    localize=True,
    style="background-color: white; border: 1px solid black;"
)

popup = GeoJsonPopup(
    fields=columns_to_show,
    aliases=[c.replace('_', ' ') for c in columns_to_show],
    localize=True,
    labels=True,
    style="background-color: #F9F871; font-size: 13px; padding: 8px;"
)

folium.GeoJson(
    konservasi_roi,
    name='Kawasan Konservasi',
    style_function=lambda x: {'fillColor': '#32CD32', 'color': '#228B22', 'weight': 2, 'fillOpacity': 0.4},
    tooltip=tooltip,
    popup=popup
).add_to(m)


colors_water = {2015: '#4B8BBE', 2020: '#306998', 2025: '#FFE873'}
colors_land = {2015: '#2E8B57', 2020: '#228B22', 2025: '#8B4513'}

roi_ee = ee.Geometry.Rectangle([113.35, -7.22, 113.38, -7.19])

for year in target_years:
    try:
        collection = (ee.ImageCollection("COPERNICUS/S2_HARMONIZED")
                      .filterDate(f'{year}-01-01', f'{year}-12-31')
                      .filterBounds(roi_ee)
                      .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 10)))
        image = collection.median().clip(roi_ee)
        ndwi = image.normalizedDifference(['B3', 'B11'])

        # Mask air dan darat
        water_mask = ndwi.gt(0)
        land_mask = ndwi.lte(0)

        binary_water = water_mask.updateMask(water_mask)
        binary_land = land_mask.updateMask(land_mask)

        water_vectors = binary_water.reduceToVectors(geometry=roi_ee, scale=10, maxPixels=1e10, crs='EPSG:4326')
        land_vectors = binary_land.reduceToVectors(geometry=roi_ee, scale=10, maxPixels=1e10, crs='EPSG:4326')

        # Konversi ke GeoJSON
        water_geojson = geemap.ee_to_geojson(water_vectors)
        land_geojson = geemap.ee_to_geojson(land_vectors)

        # Tambahkan ke peta
        folium.GeoJson(water_geojson, name=f'Air ({year})', style_function=lambda x, c=colors_water[year]: {
            'color': c, 'weight': 1.8, 'fillOpacity': 0.5, 'fillColor': c}).add_to(m)

        folium.GeoJson(land_geojson, name=f'Darat ({year})', style_function=lambda x, c=colors_land[year]: {
            'color': c, 'weight': 1.8, 'fillOpacity': 0.4, 'fillColor': c}).add_to(m)

    except Exception as e:
        st.warning(f"Gagal proses vektor untuk tahun {year}: {e}")


folium.LayerControl(collapsed=False).add_to(m)
folium.LatLngPopup().add_to(m)

col_map, col_stats = st.columns([6, 4])

with col_map:
    st_folium(m, width="100%", height=800)

with col_stats:
    st.subheader("📊 Statistik Perubahan Wilayah")
    st.dataframe(df_stats, use_container_width=True)

    st.markdown("### 📈 Tren Perubahan")
    if len(df_stats) > 1:
        df_stats.set_index("Tahun", inplace=True)
        st.line_chart(df_stats)
