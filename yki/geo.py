"""Coğrafi yardımcı hesaplar: enlem/boylam <-> yerel metre, poligon ve daire testleri."""

from __future__ import annotations

import math

DUNYA_YARICAP_M = 6371008.8
M_PER_DEG_LAT = 111319.49


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """İki koordinat arası mesafe (metre)."""
    r1, r2 = math.radians(lat1), math.radians(lat2)
    dlat = r2 - r1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(r1) * math.cos(r2) * math.sin(dlon / 2) ** 2
    return 2 * DUNYA_YARICAP_M * math.asin(min(1.0, math.sqrt(a)))


class YerelDuzlem:
    """Referans noktası etrafında eşit-dikdörtgen (equirectangular) projeksiyon.

    Yarışma alanı birkaç km ölçeğinde olduğundan hata ihmal edilebilir düzeydedir.
    x: doğu (m), y: kuzey (m).
    """

    def __init__(self, lat0: float, lon0: float):
        self.ayarla(lat0, lon0)

    def ayarla(self, lat0: float, lon0: float) -> None:
        self.lat0 = float(lat0)
        self.lon0 = float(lon0)
        self._m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(self.lat0))

    def metreye(self, lat: float, lon: float) -> tuple[float, float]:
        x = (lon - self.lon0) * self._m_per_deg_lon
        y = (lat - self.lat0) * M_PER_DEG_LAT
        return x, y

    def dereceye(self, x: float, y: float) -> tuple[float, float]:
        lat = self.lat0 + y / M_PER_DEG_LAT
        lon = self.lon0 + x / self._m_per_deg_lon if self._m_per_deg_lon else self.lon0
        return lat, lon


def nokta_poligon_icinde(lat: float, lon: float, poligon: list[tuple[float, float]]) -> bool:
    """Işın yöntemiyle nokta-poligon testi. poligon: [(lat, lon), ...] en az 3 köşe.

    lat -> y ekseni, lon -> x ekseni kabul edilir.
    """
    if len(poligon) < 3:
        return True  # sınır tanımsızken "dışarıda" alarmı üretme
    icinde = False
    j = len(poligon) - 1
    for i in range(len(poligon)):
        lat_i, lon_i = poligon[i]
        lat_j, lon_j = poligon[j]
        if (lat_i > lat) != (lat_j > lat):
            x_kesisim = (lon_j - lon_i) * (lat - lat_i) / (lat_j - lat_i) + lon_i
            if lon < x_kesisim:
                icinde = not icinde
        j = i
    return icinde


def daire_icinde(lat: float, lon: float, merkez_lat: float, merkez_lon: float, yaricap_m: float) -> bool:
    return haversine_m(lat, lon, merkez_lat, merkez_lon) <= yaricap_m


def kerteriz_derece(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """1. noktadan 2. noktaya kuzeye göre açı (0-360)."""
    r1, r2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(r2)
    y = math.cos(r1) * math.sin(r2) - math.sin(r1) * math.cos(r2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0
