"""config.yaml yükleme ve varsayılanlar."""

from __future__ import annotations

import copy
import os

try:
    import yaml
except ImportError:  # pragma: no cover - selftest ayrıca uyarır
    yaml = None

VARSAYILAN = {
    "sunucu": {
        "url": "http://127.0.0.1:5000",   # yarışma günü hakemlerce bildirilecek
        "kadi": "takimkadi",
        "sifre": "takimsifresi",
        "takim_numarasi": 1,              # girişte sunucudan gelen değerle güncellenir
        "telemetri_hz": 1.5,              # şartname: en az 1 Hz, en fazla 2 Hz (1.5 = güvenli orta)
        "saat_senk_sn": 10.0,             # sunucu saati senkron aralığı
        "hss_sorgu_sn": 2.0,              # HSS sorgu aralığı (sorgu açıkken)
        "zaman_asimi_sn": 2.0,            # HTTP zaman aşımı
    },
    "baglanti": {
        # otomatik: önce mavlink, olmazsa rosbridge; sim: benzetim
        "kaynak": "otomatik",             # otomatik | mavlink | rosbridge | sim
        "mavlink_url": "udpin:0.0.0.0:14550",
        "mavlink_baud": 57600,
        "rosbridge_host": "127.0.0.1",    # Jetson/RPi IP adresi
        "rosbridge_port": 9090,
    },
    "gorev": {
        "kilitlenme_udp_port": 42000,     # Jetson görüntü işleme -> YKİ JSON akışı
        "kilitlenme_esigi_sn": 4.0,       # şartname: kesintisiz 4 sn
        "kilit_bosluk_toleransi_sn": 0.25,  # kare kayıpları için oturum köprüsü
        "kilit_otomatik_gonder": True,    # kilit kopunca paketi otomatik gönder
        "kamikaze_otomatik_gonder": True, # dalış aktifken QR okununca otomatik gönder
        "video_url": "",                  # örn. rtsp://... veya "udp://0.0.0.0:5600" (boş=kapalı)
    },
    # Şartname 6.1.7: yarı-oto modlar (FBWA/FBWB vb.) manuel sayılır.
    "otonom_modlar": ["AUTO", "GUIDED", "RTL", "LOITER", "CIRCLE", "TAKEOFF", "LAND", "QRTL", "QLOITER", "QLAND", "AUTOLAND"],
    "komut": {
        "varsayilan_irtifa": 100,         # haritadan "Buraya Uç" için hedef irtifa (m)
        "varsayilan_hiz": 20,             # hız komutu varsayılanı (m/s)
    },
    "harita": {
        "merkez": [40.2306, 29.0060],
        "uydu": {
            "aktif": True,
            # XYZ karo şablonu; {z} {x} {y} yer tutucuları kullanılır.
            # ESRI World Imagery (varsayılan) — anahtar gerektirmez.
            "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            "onbellek": "harita_onbellek",   # karo disk önbelleği (sahada çevrimdışı çalışır)
        },
        # Yarışma sınır köşeleri (lat, lon) — ilk yarışma günü paylaşılan koordinatlarla güncellenecek
        "sinir": [
            [40.2340, 29.0010],
            [40.2340, 29.0110],
            [40.2280, 29.0110],
            [40.2280, 29.0010],
        ],
        "min_irtifa": 30.0,               # hakemlerce bildirilecek; sınır ihlali sayılır
        "maks_irtifa": 200.0,
    },
    "musabaka": {
        "sure_dk": 15.0,                  # müsabaka turu süresi
        "otonomi_hedef_yuzde": 75.0,      # puan için gereken asgari otonom uçuş oranı
        "manuel_gecis_limiti": 3,         # tur başına manuel moda geçiş limiti
        "sinir_disi_limit_sn": 10.0,      # tek seferde 10 sn dışarıda = eleme
        "hss_toplam_limit_sn": 30.0,      # HSS içinde toplam 30 sn = iniş çağrısı
    },
    "kayit": {
        "klasor": "kayitlar",             # olay günlükleri buraya yazılır
    },
}


def _derin_birlestir(taban: dict, ek: dict) -> dict:
    sonuc = copy.deepcopy(taban)
    for k, v in (ek or {}).items():
        if isinstance(v, dict) and isinstance(sonuc.get(k), dict):
            sonuc[k] = _derin_birlestir(sonuc[k], v)
        else:
            sonuc[k] = copy.deepcopy(v)
    return sonuc


class Ayarlar:
    def __init__(self, yol: str | None = None):
        self.yol = yol
        self.veri = copy.deepcopy(VARSAYILAN)
        if yol:
            self.yukle(yol)

    def yukle(self, yol: str) -> None:
        self.yol = yol
        if not os.path.exists(yol):
            return
        if yaml is None:
            raise RuntimeError("PyYAML kurulu değil: pip install pyyaml")
        with open(yol, "r", encoding="utf-8") as f:
            ek = yaml.safe_load(f) or {}
        self.veri = _derin_birlestir(VARSAYILAN, ek)

    def yeniden_yukle(self) -> None:
        if self.yol:
            self.yukle(self.yol)

    def al(self, *anahtarlar, varsayilan=None):
        v = self.veri
        for a in anahtarlar:
            if not isinstance(v, dict) or a not in v:
                return varsayilan
            v = v[a]
        return v

    # sık kullanılanlar
    @property
    def sinir(self) -> list[tuple[float, float]]:
        return [(float(a), float(b)) for a, b in self.al("harita", "sinir", varsayilan=[])]

    @property
    def otonom_modlar(self) -> set:
        return {str(m).upper() for m in self.al("otonom_modlar", varsayilan=[])}

    @property
    def telemetri_araligi_sn(self) -> float:
        hz = float(self.al("sunucu", "telemetri_hz", varsayilan=1.0) or 1.0)
        hz = min(2.0, max(1.0, hz))       # şartname sınırı: 1-2 Hz
        return 1.0 / hz
