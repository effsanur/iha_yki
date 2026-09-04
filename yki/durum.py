"""Merkezi, iş parçacığı güvenli uçuş/görev durum modeli.

Tüm kaynaklar (MAVLink/rosbridge/SİM, görev alıcısı, sunucu istemcisi) buraya
yazar; arayüz ve sunucu istemcisi buradan anlık kopya (snapshot) okur.
"""

from __future__ import annotations

import copy
import threading
import time
from collections import deque
from dataclasses import dataclass, field


def mono() -> float:
    return time.monotonic()


@dataclass
class IhaDurum:
    """Kendi hava aracımızın anlık durumu (telemetri paketinin kaynağı)."""

    enlem: float = 0.0
    boylam: float = 0.0
    irtifa: float = 0.0          # yere göre (kalkış noktasına göre) metre
    irtifa_msl: float = 0.0      # deniz seviyesine göre metre (bilgi amaçlı)
    dikilme: float = 0.0         # pitch, derece  [-90, +90]
    yatis: float = 0.0           # roll, derece   [-90, +90]
    yonelme: float = 0.0         # heading, derece [0, 360)
    hiz: float = 0.0             # yer hızı m/s (yönsüz)
    hava_hizi: float = 0.0       # hava hızı m/s (VFR_HUD.airspeed)
    batarya: float = 0.0         # %
    mod: str = "---"             # otopilot mod adı (AUTO, FBWA, ...)
    otonom: bool = False         # şartname tanımına göre otonom mu
    armed: bool = False
    gps_fix: int = 0             # 0-1 yok, 2=2D, 3=3D...
    gps_uydu: int = 0
    gps_utc_ms: int = -1         # uçaktan gelen GPS saati; günün milisaniyesi (UTC), -1 = yok
    gps_utc_kaynak_mono: float = 0.0  # gps_utc_ms alındığı andaki monotonic
    guncelleme_mono: float = 0.0      # son telemetri geliş anı (monotonic)
    kaynak: str = "-"            # MAVLINK / ROS / SIM

    def taze(self, esik_sn: float = 2.0) -> bool:
        return self.guncelleme_mono > 0 and (mono() - self.guncelleme_mono) < esik_sn


@dataclass
class KilitDurum:
    """Görüntü işleme (Jetson/RPi) tarafından bildirilen kilitlenme durumu."""

    aktif: bool = False
    merkez_x: int = 0
    merkez_y: int = 0
    genislik: int = 0
    yukseklik: int = 0
    video_genislik: int = 640
    video_yukseklik: int = 480
    guncelleme_mono: float = 0.0
    # kesintisiz kilit takibi (KilitTakip doldurur)
    oturum_sure_sn: float = 0.0

    def taze(self, esik_sn: float = 1.0) -> bool:
        return self.guncelleme_mono > 0 and (mono() - self.guncelleme_mono) < esik_sn


@dataclass
class RakipIha:
    takim_numarasi: int = 0
    enlem: float = 0.0
    boylam: float = 0.0
    irtifa: float = 0.0
    dikilme: float = 0.0
    yonelme: float = 0.0
    yatis: float = 0.0
    hiz: float = 0.0
    zaman_farki: int = 0         # ms, sunucu bildirimi
    guncelleme_mono: float = 0.0

    def taze(self, esik_sn: float = 4.0) -> bool:
        return self.guncelleme_mono > 0 and (mono() - self.guncelleme_mono) < esik_sn


@dataclass
class HssBolge:
    id: int = 0
    enlem: float = 0.0
    boylam: float = 0.0
    yaricap: float = 0.0


@dataclass
class SunucuDurum:
    url: str = ""
    giris_ok: bool = False
    takim_numarasi: int = 0
    telemetri_aktif: bool = False
    son_durum_kodu: int = 0
    son_hata: str = ""
    gecikme_ms: float = -1.0
    saat_ofset_ok: bool = False
    sunucu_gun: int = 0
    gonderilen_telemetri: int = 0
    telemetri_hata: int = 0
    son_telemetri_mono: float = 0.0
    kilit_paket_sayisi: int = 0
    kamikaze_paket_sayisi: int = 0
    qr_enlem: float = 0.0
    qr_boylam: float = 0.0
    qr_var: bool = False


@dataclass
class Olay:
    zaman: float          # time.time()
    seviye: str           # BILGI / UYARI / HATA / BASARI
    metin: str


@dataclass
class DurumModeli:
    iha: IhaDurum = field(default_factory=IhaDurum)
    kilit: KilitDurum = field(default_factory=KilitDurum)
    rakipler: dict = field(default_factory=dict)      # takim_no -> RakipIha
    hss_listesi: list = field(default_factory=list)   # [HssBolge]
    sunucu: SunucuDurum = field(default_factory=SunucuDurum)
    qr_metni: str = ""
    iz: deque = field(default_factory=lambda: deque(maxlen=1800))  # kendi izimiz (lat, lon)
    # ev (kalkış) noktası — ARM anında otomatik yakalanır, haritadan da işaretlenebilir
    ev_var: bool = False
    ev_enlem: float = 0.0
    ev_boylam: float = 0.0
    # GUIDED "Buraya Uç" hedefi (harita işareti için)
    hedef_var: bool = False
    hedef_enlem: float = 0.0
    hedef_boylam: float = 0.0
    hedef_irtifa: float = 0.0
    # araçtan okunan görev (waypoint) listesi: [(enlem, boylam, irtifa), ...]
    gorev_wps: list = field(default_factory=list)
    gorev_surumu: int = 0

    def __post_init__(self):
        self._kilit_lock = threading.RLock()
        self._olaylar = deque(maxlen=2000)

    # ---- yazma yardımcıları ------------------------------------------------
    def kilitli(self):
        return self._kilit_lock

    def iha_guncelle(self, **alanlar) -> None:
        with self._kilit_lock:
            for k, v in alanlar.items():
                setattr(self.iha, k, v)
            self.iha.guncelleme_mono = mono()
            if self.iha.enlem or self.iha.boylam:
                if not self.iz or (self.iz[-1][0] != self.iha.enlem or self.iz[-1][1] != self.iha.boylam):
                    self.iz.append((self.iha.enlem, self.iha.boylam))

    def kilit_guncelle(self, **alanlar) -> None:
        with self._kilit_lock:
            for k, v in alanlar.items():
                setattr(self.kilit, k, v)
            self.kilit.guncelleme_mono = mono()

    def rakipleri_guncelle(self, liste: list[dict], kendi_takim_no: int) -> None:
        simdi = mono()
        with self._kilit_lock:
            for r in liste:
                try:
                    no = int(r.get("takim_numarasi", 0))
                except (TypeError, ValueError):
                    continue
                if no == 0 or no == kendi_takim_no:
                    continue
                mevcut = self.rakipler.get(no) or RakipIha(takim_numarasi=no)
                mevcut.enlem = float(r.get("iha_enlem", mevcut.enlem) or 0.0)
                mevcut.boylam = float(r.get("iha_boylam", mevcut.boylam) or 0.0)
                mevcut.irtifa = float(r.get("iha_irtifa", mevcut.irtifa) or 0.0)
                mevcut.dikilme = float(r.get("iha_dikilme", mevcut.dikilme) or 0.0)
                mevcut.yonelme = float(r.get("iha_yonelme", mevcut.yonelme) or 0.0)
                mevcut.yatis = float(r.get("iha_yatis", mevcut.yatis) or 0.0)
                # sunucu cevabında alan adı "iha_hizi" (dokümandaki örneğe göre)
                mevcut.hiz = float(r.get("iha_hizi", r.get("iha_hiz", mevcut.hiz)) or 0.0)
                try:
                    mevcut.zaman_farki = int(r.get("zaman_farki", 0) or 0)
                except (TypeError, ValueError):
                    mevcut.zaman_farki = 0
                mevcut.guncelleme_mono = simdi
                self.rakipler[no] = mevcut

    def hss_guncelle(self, liste: list[dict]) -> None:
        yeni = []
        for h in liste:
            try:
                yeni.append(HssBolge(
                    id=int(h.get("id", 0)),
                    enlem=float(h.get("hssEnlem", 0.0)),
                    boylam=float(h.get("hssBoylam", 0.0)),
                    yaricap=float(h.get("hssYaricap", 0.0)),
                ))
            except (TypeError, ValueError):
                continue
        with self._kilit_lock:
            self.hss_listesi = yeni

    def ev_ayarla(self, enlem: float, boylam: float) -> None:
        with self._kilit_lock:
            self.ev_var = True
            self.ev_enlem = float(enlem)
            self.ev_boylam = float(boylam)

    def hedef_ayarla(self, enlem: float, boylam: float, irtifa: float) -> None:
        with self._kilit_lock:
            self.hedef_var = True
            self.hedef_enlem = float(enlem)
            self.hedef_boylam = float(boylam)
            self.hedef_irtifa = float(irtifa)

    def gorev_wps_yaz(self, liste: list) -> None:
        """Araçtan okunan görev listesini yayınlar (arayüz sürüm numarasıyla fark eder)."""
        with self._kilit_lock:
            self.gorev_wps = [(float(a), float(b), float(c)) for a, b, c in liste]
            self.gorev_surumu += 1

    def olay(self, seviye: str, metin: str) -> None:
        with self._kilit_lock:
            self._olaylar.append(Olay(time.time(), seviye, metin))

    def olaylari_al(self) -> list[Olay]:
        """Bekleyen olayları alır ve kuyruğu boşaltır (arayüz tüketir)."""
        with self._kilit_lock:
            liste = list(self._olaylar)
            self._olaylar.clear()
        return liste

    # ---- okuma -------------------------------------------------------------
    def anlik(self) -> "DurumModeli":
        """Arayüz çizimleri için derin olmayan güvenli kopya."""
        with self._kilit_lock:
            kopya = DurumModeli.__new__(DurumModeli)
            kopya.iha = copy.copy(self.iha)
            kopya.kilit = copy.copy(self.kilit)
            kopya.rakipler = {k: copy.copy(v) for k, v in self.rakipler.items()}
            kopya.hss_listesi = list(self.hss_listesi)
            kopya.sunucu = copy.copy(self.sunucu)
            kopya.qr_metni = self.qr_metni
            kopya.iz = deque(self.iz, maxlen=self.iz.maxlen)
            kopya.ev_var = self.ev_var
            kopya.ev_enlem = self.ev_enlem
            kopya.ev_boylam = self.ev_boylam
            kopya.hedef_var = self.hedef_var
            kopya.hedef_enlem = self.hedef_enlem
            kopya.hedef_boylam = self.hedef_boylam
            kopya.hedef_irtifa = self.hedef_irtifa
            kopya.gorev_wps = list(self.gorev_wps)
            kopya.gorev_surumu = self.gorev_surumu
            kopya._kilit_lock = threading.RLock()
            kopya._olaylar = deque()
            return kopya
