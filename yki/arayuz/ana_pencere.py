"""Ana pencere: tüm bileşenlerin bağlandığı yer."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QLabel, QMainWindow, QPushButton, QSplitter,
    QTabWidget, QToolBar, QVBoxLayout, QWidget,
)

from ..durum import DurumModeli
from ..gorev_alici import KilitTakip
from ..kurallar import KuralTakip
from ..telemetri_kaynagi import SimKaynak
from . import stiller
from .harita import HaritaWidget
from .hud import HudWidget
from .paneller import (
    DurumSeridi, EylemlerPanel, GorevPlaniSeridi, HizliPanel, HssPaneli,
    KamikazePaneli, KilitPaneli, LogPaneli, SunucuPaneli, TelemetriPaneli,
    VideoPaneli,
)


@dataclass
class UygBaglam:
    """Panellerin ortak eriştiği uygulama nesneleri."""

    ayarlar: object
    durum: DurumModeli
    saat: object
    istemci: object
    kaynaklar: list = field(default_factory=list)


class AnaPencere(QMainWindow):
    def __init__(self, uyg: UygBaglam):
        super().__init__()
        self.uyg = uyg
        self.kural = KuralTakip(uyg.ayarlar)
        self.kilit_takip = KilitTakip(uyg.ayarlar)
        self.qr_kuyruk: deque = deque()
        self._onceki_sinir_icinde = True
        self._onceki_hss_icinde = False
        self._onceki_fcu_ok = False
        self._onceki_armed = False
        self._son_bip = 0.0

        self.setWindowTitle("Savaşan İHA YKİ")
        self.resize(1500, 900)
        self._arac_cubugu()
        self._yerlesim()

        self.zamanlayici = QTimer(self)
        self.zamanlayici.timeout.connect(self._tik)
        self.zamanlayici.start(100)

    # ------------- kurulum -------------
    def _arac_cubugu(self) -> None:
        cubuk = QToolBar("Kontrol")
        cubuk.setMovable(False)
        self.addToolBar(cubuk)

        self.musabaka_dugme = QPushButton("MÜSABAKAYI BAŞLAT")
        self.musabaka_dugme.setProperty("rol", "onay")
        self.musabaka_dugme.setCheckable(True)
        self.musabaka_dugme.toggled.connect(self._musabaka_degisti)
        cubuk.addWidget(self.musabaka_dugme)

        sifirla = QAction("Sayaçları sıfırla", self)
        sifirla.triggered.connect(self._sayaclari_sifirla)
        cubuk.addAction(sifirla)

        cubuk.addSeparator()
        sigdir = QAction("Haritayı sınıra sığdır", self)
        sigdir.triggered.connect(lambda: self.harita.sigdir())
        cubuk.addAction(sigdir)

        self.uydu_dugme = QPushButton("UYDU")
        self.uydu_dugme.setCheckable(True)
        self.uydu_dugme.setChecked(bool(self.uyg.ayarlar.al("harita", "uydu", "aktif", varsayilan=True)))
        self.uydu_dugme.toggled.connect(self._uydu_degisti)
        cubuk.addWidget(self.uydu_dugme)

        onbellek = QAction("Uydu haritasını önbelleğe indir", self)
        onbellek.triggered.connect(self._uydu_onbellek)
        cubuk.addAction(onbellek)

        yenile = QAction("Config'i yeniden yükle", self)
        yenile.triggered.connect(self._config_yenile)
        cubuk.addAction(yenile)

        # SİM kontrolleri (yalnızca benzetim kaynağı varsa)
        self.sim = next((k for k in self.uyg.kaynaklar if isinstance(k, SimKaynak)), None)
        if self.sim is not None:
            cubuk.addSeparator()
            etiket = QLabel("  SİM: ")
            etiket.setStyleSheet(f"color:{stiller.UYARI}; font-weight:bold;")
            cubuk.addWidget(etiket)
            self.sim_otonom_dugme = QPushButton("OTONOM")
            self.sim_otonom_dugme.setCheckable(True)
            self.sim_otonom_dugme.setChecked(True)
            self.sim_otonom_dugme.toggled.connect(self._sim_mod)
            cubuk.addWidget(self.sim_otonom_dugme)
            self.sim_kilit_dugme = QPushButton("KİLİT BENZETİMİ")
            self.sim_kilit_dugme.setCheckable(True)
            self.sim_kilit_dugme.toggled.connect(lambda a: self.sim.kilit_sim_degistir(a))
            cubuk.addWidget(self.sim_kilit_dugme)

    def _yerlesim(self) -> None:
        bolucu = QSplitter(Qt.Horizontal)

        # sol sütun (Mission Planner "Flight Data" düzeni):
        # üstte HUD, altta Hızlı / Eylemler / Sunucu / Durum sekmeleri
        sol = QWidget()
        sol_duzen = QVBoxLayout(sol)
        sol_duzen.setContentsMargins(6, 6, 0, 6)
        self.hud = HudWidget()
        sol_duzen.addWidget(self.hud, 5)
        self.hizli_panel = HizliPanel()
        self.eylemler_paneli = EylemlerPanel(self.uyg, self.komut_kaynagi)
        self.sunucu_paneli = SunucuPaneli(self.uyg)
        self.telemetri_paneli = TelemetriPaneli()
        self.sol_sekmeler = QTabWidget()
        self.sol_sekmeler.addTab(self.hizli_panel, "HIZLI")
        self.sol_sekmeler.addTab(self.eylemler_paneli, "EYLEMLER")
        self.sol_sekmeler.addTab(self.sunucu_paneli, "SUNUCU")
        self.sol_sekmeler.addTab(self.telemetri_paneli, "DURUM")
        sol_duzen.addWidget(self.sol_sekmeler, 6)
        bolucu.addWidget(sol)

        # orta: durum şeridi + UYDU HARİTASI + görev planı şeridi
        # (şartname: sınır + rakip + HSS daima görünür)
        orta = QWidget()
        self.orta_duzen = QVBoxLayout(orta)
        self.orta_duzen.setContentsMargins(6, 6, 6, 6)
        self.serit = DurumSeridi()
        self.orta_duzen.addWidget(self.serit)
        self.harita = HaritaWidget(self.uyg.ayarlar, self.uyg.durum)
        self.gorev_seridi = GorevPlaniSeridi(self.uyg, self.komut_kaynagi)
        self.harita.buraya_uc_cb = self._buraya_uc
        self.harita.ev_isaretle_cb = self.uyg.durum.ev_ayarla
        self.harita.wp_ekle_cb = self.gorev_seridi.wp_ekle
        self.harita.wp_sil_cb = self.gorev_seridi.wp_sil
        self.harita.wp_listesi = self.gorev_seridi.wps  # ortak liste
        self.orta_duzen.addWidget(self.harita, 1)
        self.orta_duzen.addWidget(self.gorev_seridi)
        bolucu.addWidget(orta)

        # sağ: kamera alanı, görev/HSS sekmeleri
        self.sag_panel = QWidget()
        self.sag_duzen = QVBoxLayout(self.sag_panel)
        self.sag_duzen.setContentsMargins(0, 0, 0, 0)
        self.kilit_paneli = KilitPaneli(self.uyg)
        self.kamikaze_paneli = KamikazePaneli(self.uyg)
        self.hss_paneli = HssPaneli(self.uyg)
        self.video_paneli = VideoPaneli(self.uyg)
        self.video_paneli.goruntu.clicked.connect(self._alanlari_degistir)
        self.video_paneli.setFixedHeight(340)
        self.sag_duzen.addWidget(self.video_paneli)

        self.sekmeler = QTabWidget()
        self.sekmeler.setTabPosition(QTabWidget.North)
        self.sekmeler.addTab(self.kilit_paneli, "KİLİTLENME")
        self.sekmeler.addTab(self.kamikaze_paneli, "KAMİKAZE")
        self.sekmeler.addTab(self.hss_paneli, "HSS")
        self.sag_duzen.addWidget(self.sekmeler, 1)
        bolucu.addWidget(self.sag_panel)
        self._alanlar_degisti = False

        bolucu.setStretchFactor(0, 2)
        bolucu.setStretchFactor(1, 6)
        bolucu.setStretchFactor(2, 2)
        bolucu.setSizes([330, 880, 340])
        self.setCentralWidget(bolucu)

        # alt: olay günlüğü (her zaman görünür)
        self.log_paneli = LogPaneli(str(self.uyg.ayarlar.al("kayit", "klasor", varsayilan="kayitlar")))
        rihtim = QDockWidget("OLAY GÜNLÜĞÜ", self)
        rihtim.setWidget(self.log_paneli)
        rihtim.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        rihtim.setMaximumHeight(190)
        self.addDockWidget(Qt.BottomDockWidgetArea, rihtim)

        self.statusBar().showMessage("Hazır — sunucuya giriş yapın ve telemetriyi başlatın")

    def _alanlari_degistir(self) -> None:
        """Kamera ve harita alanlarını birbirleriyle değiştirir; ikinci tık geri döndürür."""
        if self._alanlar_degisti:
            self.orta_duzen.removeWidget(self.video_paneli)
            self.sag_duzen.removeWidget(self.harita)
            self.orta_duzen.insertWidget(1, self.harita, 1)
            self.sag_duzen.insertWidget(0, self.video_paneli)
            self.video_paneli.setFixedHeight(340)
            self._alanlar_degisti = False
            return

        self.orta_duzen.removeWidget(self.harita)
        self.sag_duzen.removeWidget(self.video_paneli)
        self.sag_duzen.insertWidget(0, self.harita)
        self.orta_duzen.insertWidget(1, self.video_paneli, 1)
        self.video_paneli.setFixedHeight(730)
        self._alanlar_degisti = True

    # ------------- komut yönlendirme -------------
    def komut_kaynagi(self):
        """Komutların gideceği aktif kaynak: SİM > bağlı MAVLink > bağlı ROS."""
        adaylar = sorted(
            (k for k in self.uyg.kaynaklar if k.komut_destegi()),
            key=lambda k: (not isinstance(k, SimKaynak), not k.bagli),
        )
        for kaynak in adaylar:
            if isinstance(kaynak, SimKaynak) or kaynak.bagli:
                return kaynak
        return None

    def _buraya_uc(self, enlem: float, boylam: float) -> None:
        kaynak = self.komut_kaynagi()
        if kaynak is None:
            self.uyg.durum.olay("HATA", "'Buraya Uç' gönderilemedi: bağlı bir telemetri kaynağı yok")
            return
        kaynak.git(enlem, boylam, self.eylemler_paneli.guided_irtifa())

    # ------------- düğmeler -------------
    def _musabaka_degisti(self, acik: bool) -> None:
        if acik:
            self.kural.musabaka_baslat()
            self.musabaka_dugme.setText("MÜSABAKAYI DURDUR")
            self.uyg.durum.olay("BASARI", "Müsabaka sayaçları BAŞLATILDI (otonomi %, manuel geçiş, süre)")
        else:
            self.kural.musabaka_durdur()
            self.musabaka_dugme.setText("MÜSABAKAYI BAŞLAT")
            o = self.kural.ozet
            self.uyg.durum.olay("BILGI", f"Müsabaka durduruldu — otonomi %{o.otonomi_yuzde:.1f}, manuel geçiş {o.manuel_gecis}")

    def _sayaclari_sifirla(self) -> None:
        self.kural.sifirla()
        self.musabaka_dugme.setChecked(False)
        self.uyg.durum.olay("BILGI", "Kural sayaçları sıfırlandı")

    def _config_yenile(self) -> None:
        try:
            self.uyg.ayarlar.yeniden_yukle()
            self.harita.sigdir()
            self.uyg.durum.olay("BASARI", "config.yaml yeniden yüklendi (sınır/irtifa/modlar güncel)")
        except Exception as e:  # noqa: BLE001
            self.uyg.durum.olay("HATA", f"Config yüklenemedi: {e}")

    def _uydu_degisti(self, acik: bool) -> None:
        if self.harita.karolar is not None:
            self.harita.karolar.aktif = acik
        self.uyg.durum.olay("BILGI", "Uydu haritası " + ("AÇIK" if acik else "KAPALI"))

    def _uydu_onbellek(self) -> None:
        if self.harita.karolar is not None:
            self.harita.karolar.bolgeyi_indir(self.uyg.ayarlar.sinir)

    def _sim_mod(self, otonom: bool) -> None:
        if self.sim:
            self.sim.mod_degistir(otonom)
            self.sim_otonom_dugme.setText("OTONOM" if otonom else "MANUEL")

    # ------------- periyodik döngü -------------
    def _tik(self) -> None:
        anlik = self.uyg.durum.anlik()
        ozet = self.kural.tick(anlik)

        # kilit oturumu takibi -> otomatik paket
        kilit_aktif = anlik.kilit.aktif and anlik.kilit.taze(0.6)
        sonuc = self.kilit_takip.guncelle(kilit_aktif, self.uyg.saat.simdi_ms())
        if sonuc is not None and sonuc[0] == "tamam":
            self.kilit_paneli.kilit_tamamlandi(sonuc[1])

        # görüntü işlemeden gelen QR olayları
        while self.qr_kuyruk:
            self.kamikaze_paneli.qr_geldi(self.qr_kuyruk.popleft())

        # ev (kalkış) noktası: ARM anında otomatik yakala (MP davranışı)
        if anlik.iha.armed and not self._onceki_armed and (anlik.iha.enlem or anlik.iha.boylam):
            self.uyg.durum.ev_ayarla(anlik.iha.enlem, anlik.iha.boylam)
            self.uyg.durum.olay("BILGI", f"Ev noktası işaretlendi (ARM): {anlik.iha.enlem:.6f}, {anlik.iha.boylam:.6f}")
        self._onceki_armed = anlik.iha.armed

        # panolar
        self.serit.guncelle(anlik, ozet)
        self.harita.guncelle(anlik, ozet)
        self.hud.guncelle(anlik.iha)
        self.hizli_panel.guncelle(anlik, ozet)
        self.telemetri_paneli.guncelle(anlik)
        self.sunucu_paneli.guncelle(anlik, self.uyg.saat)
        self.kilit_paneli.guncelle(anlik, self.kilit_takip)
        self.kamikaze_paneli.guncelle(anlik, self.kilit_takip)
        self.hss_paneli.guncelle(anlik, ozet)
        self.gorev_seridi.guncelle(anlik)

        # kritik durum geçişleri -> olay + sesli ikaz
        self._alarmlar(anlik, ozet)

        # olay günlüğü
        for olay in self.uyg.durum.olaylari_al():
            self.log_paneli.ekle(olay)
            if olay.seviye in ("HATA", "UYARI"):
                self.statusBar().showMessage(f"{olay.seviye}: {olay.metin}", 8000)

    def _alarmlar(self, anlik, ozet) -> None:
        simdi = time.monotonic()
        kritik = False
        if ozet.sinir_icinde != self._onceki_sinir_icinde:
            if not ozet.sinir_icinde:
                self.uyg.durum.olay("HATA", "SINIR İHLALİ! Uçuş alanının dışına çıkıldı (10 sn'de eleme, -200)")
            else:
                self.uyg.durum.olay("BASARI", f"Sınır içine dönüldü (dışarıda kalınan süre sayacı: {ozet.sinir_disi_sayisi})")
            self._onceki_sinir_icinde = ozet.sinir_icinde
        if ozet.hss_icinde != self._onceki_hss_icinde:
            if ozet.hss_icinde:
                self.uyg.durum.olay("HATA", f"HSS İHLALİ! HSS-{ozet.hss_aktif_id} bölgesine girildi (saniyede -5 puan)")
            else:
                self.uyg.durum.olay("BASARI", "HSS bölgesinden çıkıldı")
            self._onceki_hss_icinde = ozet.hss_icinde
        if ozet.fcu_ok != self._onceki_fcu_ok:
            if not ozet.fcu_ok and self._onceki_fcu_ok:
                self.uyg.durum.olay("HATA", "FCU/telemetri bağlantısı KOPTU!")
            self._onceki_fcu_ok = ozet.fcu_ok
        kritik = (not ozet.sinir_icinde) or ozet.hss_icinde or ozet.sinir_eleme_riski or ozet.sunucu_kopuk_riski
        if kritik and simdi - self._son_bip > 1.5:
            self._son_bip = simdi
            QApplication.beep()

    # ------------- kapanış -------------
    def closeEvent(self, e) -> None:  # noqa: N802
        self.zamanlayici.stop()
        self.video_paneli.kapat()
        e.accept()
