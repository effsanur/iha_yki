"""Arayüz panelleri: durum şeridi, telemetri, sunucu, görev (kilit/kamikaze), HSS, log, video."""

from __future__ import annotations

import html
import os
import time
from datetime import datetime

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSizePolicy, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import geo
from ..sunucu_istemci import saat_dict_ms
from . import stiller

try:
    import cv2
except ImportError:
    cv2 = None


def _yatay(*wler) -> QHBoxLayout:
    kutu = QHBoxLayout()
    kutu.setContentsMargins(0, 0, 0, 0)
    for w in wler:
        if w == "esnek":
            kutu.addStretch(1)
        else:
            kutu.addWidget(w)
    return kutu


class Cip(QLabel):
    """Durum şeridindeki durum kutucuğu (göz yormayan, ince kenarlıklı)."""

    def __init__(self, baslik: str):
        super().__init__()
        self.baslik = baslik
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumWidth(92)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.ayarla("--", stiller.YAZI_SOLUK)

    def ayarla(self, deger: str, renk: str, yanip_sonme: bool = False) -> None:
        arka = stiller.PANEL_ACIK
        kenar = stiller.KENAR
        if yanip_sonme and int(time.monotonic() * 2) % 2 == 0:
            arka = "#3a2321"          # alarm vurgusu: yumuşak kırmızımsı zemin
            kenar = renk
        elif yanip_sonme:
            kenar = renk
        self.setStyleSheet(
            f"QLabel {{ background:{arka}; color:{renk}; border:1px solid {kenar};"
            f" border-radius:6px; padding:3px 6px; font-weight:bold; font-size:12px; }}"
        )
        self.setText(f"{self.baslik}\n{deger}")


class DurumSeridi(QWidget):
    """Harita üstündeki büyük durum şeridi — kritik bilgiler tek bakışta."""

    def __init__(self):
        super().__init__()
        self.mod = Cip("MOD")
        self.arm = Cip("ARM")
        self.otonomi = Cip("OTONOMİ")
        self.manuel = Cip("MANUEL GEÇİŞ")
        self.sinir = Cip("SINIR")
        self.hss = Cip("HSS")
        self.fcu = Cip("FCU BAĞ")
        self.sunucu = Cip("SUNUCU")
        self.sure = Cip("MÜSABAKA")
        duzen = QHBoxLayout(self)
        duzen.setContentsMargins(0, 0, 0, 4)
        duzen.setSpacing(6)
        for cip in (self.mod, self.arm, self.otonomi, self.manuel, self.sinir, self.hss, self.fcu, self.sunucu, self.sure):
            duzen.addWidget(cip)

    def guncelle(self, durum, ozet) -> None:
        iha = durum.iha
        taze = iha.taze(2.0)
        self.mod.ayarla(iha.mod if taze else "VERİ YOK",
                        (stiller.IYI if iha.otonom else stiller.UYARI) if taze else stiller.HATA)
        self.arm.ayarla("ARMED" if iha.armed else "DISARMED",
                        stiller.HATA if iha.armed else stiller.IYI)
        oto_renk = stiller.IYI if ozet.otonomi_yeterli else stiller.HATA
        self.otonomi.ayarla(f"%{ozet.otonomi_yuzde:.1f}", oto_renk if ozet.musabaka_aktif else stiller.YAZI_SOLUK)
        m_renk = stiller.IYI if ozet.manuel_gecis <= ozet.manuel_limit else stiller.HATA
        self.manuel.ayarla(f"{ozet.manuel_gecis}/{ozet.manuel_limit}", m_renk)
        if ozet.sinir_icinde:
            self.sinir.ayarla("İÇİNDE", stiller.IYI)
        else:
            ek = f" {ozet.sinir_disi_sure:.0f}s" + (f" | {ozet.irtifa_ihlal}" if ozet.irtifa_ihlal else "")
            self.sinir.ayarla("DIŞARIDA!" + ek, stiller.HATA, yanip_sonme=True)
        if ozet.hss_icinde:
            self.hss.ayarla(f"İHLAL! HSS-{ozet.hss_aktif_id} ({ozet.hss_ihlal_toplam_sn:.0f}s)", stiller.HATA, yanip_sonme=True)
        else:
            self.hss.ayarla(f"TEMİZ ({len(durum.hss_listesi)} bölge)", stiller.IYI if not durum.hss_listesi else stiller.UYARI)
        self.fcu.ayarla(f"{iha.kaynak} {ozet.fcu_yas_sn:.1f}s" if ozet.fcu_ok else "KOPUK!",
                        stiller.IYI if ozet.fcu_ok else stiller.HATA, yanip_sonme=not ozet.fcu_ok)
        s = durum.sunucu
        if not s.giris_ok:
            self.sunucu.ayarla("GİRİŞ YOK", stiller.UYARI)
        elif s.telemetri_aktif:
            if ozet.sunucu_kopuk_riski:
                self.sunucu.ayarla(f"KOPUK {ozet.sunucu_telemetri_yas_sn:.0f}s!", stiller.HATA, yanip_sonme=True)
            else:
                self.sunucu.ayarla(f"AKTİF #{s.gonderilen_telemetri}", stiller.IYI)
        else:
            self.sunucu.ayarla("TELEMETRİ KAPALI", stiller.UYARI)
        if ozet.musabaka_aktif:
            dk, sn = divmod(int(ozet.kalan_sn), 60)
            self.sure.ayarla(f"{dk:02d}:{sn:02d}", stiller.VURGU)
        else:
            self.sure.ayarla("BEKLEMEDE", stiller.YAZI_SOLUK)


class TelemetriPaneli(QGroupBox):
    ALANLAR = [
        ("enlem", "Enlem", "{:.7f}"),
        ("boylam", "Boylam", "{:.7f}"),
        ("irtifa", "İrtifa (yere göre)", "{:.1f} m"),
        ("irtifa_msl", "İrtifa (MSL)", "{:.1f} m"),
        ("hiz", "Yer hızı", "{:.1f} m/s"),
        ("yonelme", "Yönelme", "{:.1f}°"),
        ("dikilme", "Dikilme", "{:+.1f}°"),
        ("yatis", "Yatış", "{:+.1f}°"),
        ("batarya", "Batarya", "%{:.0f}"),
        ("gps", "GPS", "{}"),
        ("gps_saat", "GPS saati (UTC)", "{}"),
        ("kaynak", "Veri kaynağı", "{}"),
    ]

    def __init__(self):
        super().__init__("TELEMETRİ")
        self.etiketler = {}
        duzen = QGridLayout(self)
        duzen.setVerticalSpacing(3)
        for i, (anahtar, ad, _) in enumerate(self.ALANLAR):
            baslik = QLabel(ad)
            baslik.setProperty("rol", "soluk")
            deger = QLabel("--")
            deger.setProperty("rol", "deger")
            deger.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            duzen.addWidget(baslik, i, 0)
            duzen.addWidget(deger, i, 1)
            self.etiketler[anahtar] = deger

    def guncelle(self, durum) -> None:
        iha = durum.iha
        for anahtar, _, kalip in self.ALANLAR:
            if anahtar == "gps":
                fix = {0: "YOK", 1: "YOK", 2: "2D", 3: "3D", 4: "DGPS", 5: "RTK-F", 6: "RTK"}.get(iha.gps_fix, str(iha.gps_fix))
                metin = f"{fix} / {iha.gps_uydu} uydu"
            elif anahtar == "gps_saat":
                if iha.gps_utc_ms >= 0:
                    d = saat_dict_ms(iha.gps_utc_ms)
                    metin = f"{d['saat']:02d}:{d['dakika']:02d}:{d['saniye']:02d}"
                else:
                    metin = "YOK (sistem saati!)"
            elif anahtar == "kaynak":
                metin = iha.kaynak
            else:
                try:
                    metin = kalip.format(getattr(iha, anahtar))
                except (ValueError, TypeError):
                    metin = "--"
            self.etiketler[anahtar].setText(metin)
        self.etiketler["gps_saat"].setStyleSheet("" if iha.gps_utc_ms >= 0 else f"color:{stiller.HATA};")


class HizliPanel(QWidget):
    """Mission Planner 'Quick' benzeri büyük rakamlı gösterge ızgarası."""

    ALANLAR = [
        ("irtifa", "İrtifa (m)"),
        ("yer_hizi", "Yer Hızı (m/s)"),
        ("eve_mesafe", "Eve Mesafe (m)"),
        ("qr_mesafe", "QR'a Mesafe (m)"),
        ("batarya", "Batarya (%)"),
        ("otonomi", "Otonomi (%)"),
    ]

    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:#05070a;")
        izgara = QGridLayout(self)
        izgara.setContentsMargins(4, 4, 4, 4)
        izgara.setSpacing(4)
        self.degerler = {}
        for i, (anahtar, baslik) in enumerate(self.ALANLAR):
            hucre = QFrame()
            hucre.setStyleSheet(f"QFrame {{ background:#0b0f14; border:1px solid {stiller.KENAR}; border-radius:4px; }}")
            duzen = QVBoxLayout(hucre)
            duzen.setContentsMargins(6, 3, 6, 3)
            duzen.setSpacing(0)
            b = QLabel(baslik)
            b.setStyleSheet(f"color:{stiller.YAZI_SOLUK}; font-size:11px; border:none;")
            d = QLabel("--")
            d.setStyleSheet("color:white; font-family:Consolas; font-size:26px; font-weight:bold; border:none;")
            d.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            cizgi = QFrame()
            cizgi.setFixedHeight(2)
            cizgi.setStyleSheet(f"background:{stiller.IYI}; border:none;")
            duzen.addWidget(b)
            duzen.addWidget(d, 1)
            duzen.addWidget(cizgi)
            izgara.addWidget(hucre, i // 2, i % 2)
            self.degerler[anahtar] = d

    def guncelle(self, durum, ozet) -> None:
        iha = durum.iha
        self.degerler["irtifa"].setText(f"{iha.irtifa:.1f}")
        self.degerler["yer_hizi"].setText(f"{iha.hiz:.1f}")
        if durum.ev_var and (iha.enlem or iha.boylam):
            self.degerler["eve_mesafe"].setText(f"{geo.haversine_m(iha.enlem, iha.boylam, durum.ev_enlem, durum.ev_boylam):.0f}")
        else:
            self.degerler["eve_mesafe"].setText("--")
        if durum.sunucu.qr_var and (iha.enlem or iha.boylam):
            self.degerler["qr_mesafe"].setText(f"{geo.haversine_m(iha.enlem, iha.boylam, durum.sunucu.qr_enlem, durum.sunucu.qr_boylam):.0f}")
        else:
            self.degerler["qr_mesafe"].setText("--")
        self.degerler["batarya"].setText(f"{iha.batarya:.0f}")
        self.degerler["otonomi"].setText(f"{ozet.otonomi_yuzde:.1f}" if ozet.musabaka_aktif else "--")


class EylemlerPanel(QWidget):
    """Mission Planner 'Actions' benzeri komut paneli.

    Şartname 6.1.7: YKİ'den komut göndermek (varış noktası, irtifa, hız,
    iniş) otonomiyi bozmaz — buradaki komutlar bu kapsamdadır.
    """

    MODLAR = ["AUTO", "GUIDED", "RTL", "LOITER", "TAKEOFF", "FBWA", "FBWB",
              "CRUISE", "CIRCLE", "STABILIZE", "MANUAL"]

    def __init__(self, uyg, komut_kaynagi_getir):
        super().__init__()
        self.uyg = uyg
        self.komut_kaynagi_getir = komut_kaynagi_getir
        duzen = QVBoxLayout(self)
        duzen.setSpacing(6)

        # mod seçimi
        satir = QHBoxLayout()
        self.mod_secim = QComboBox()
        self.mod_secim.addItems(self.MODLAR)
        mod_dugme = QPushButton("MODU AYARLA")
        mod_dugme.clicked.connect(self._mod_ayarla)
        satir.addWidget(self.mod_secim, 1)
        satir.addWidget(mod_dugme, 1)
        duzen.addLayout(satir)

        # hızlı mod düğmeleri (MP Auto/RTL/Loiter kısayolları)
        satir2 = QHBoxLayout()
        for ad in ("AUTO", "GUIDED", "RTL", "LOITER"):
            d = QPushButton(ad)
            d.clicked.connect(lambda _=False, m=ad: self._komut("mod_ayarla", m))
            satir2.addWidget(d)
        duzen.addLayout(satir2)

        # arm / disarm
        satir3 = QHBoxLayout()
        arm_dugme = QPushButton("ARM")
        arm_dugme.setProperty("rol", "onay")
        arm_dugme.clicked.connect(lambda: self._arm(True))
        disarm_dugme = QPushButton("DISARM")
        disarm_dugme.setProperty("rol", "tehlike")
        disarm_dugme.clicked.connect(lambda: self._arm(False))
        satir3.addWidget(arm_dugme)
        satir3.addWidget(disarm_dugme)
        duzen.addLayout(satir3)

        # irtifa / hız
        izgara = QGridLayout()
        izgara.addWidget(QLabel("Hedef irtifa (m):"), 0, 0)
        self.irtifa_spin = QSpinBox()
        self.irtifa_spin.setRange(10, 500)
        self.irtifa_spin.setValue(int(self.uyg.ayarlar.al("komut", "varsayilan_irtifa", varsayilan=100)))
        izgara.addWidget(self.irtifa_spin, 0, 1)
        irtifa_dugme = QPushButton("İRTİFA AYARLA")
        irtifa_dugme.clicked.connect(lambda: self._komut("irtifa_degistir", float(self.irtifa_spin.value())))
        izgara.addWidget(irtifa_dugme, 0, 2)
        izgara.addWidget(QLabel("Hedef hız (m/s):"), 1, 0)
        self.hiz_spin = QSpinBox()
        self.hiz_spin.setRange(8, 60)
        self.hiz_spin.setValue(int(self.uyg.ayarlar.al("komut", "varsayilan_hiz", varsayilan=20)))
        izgara.addWidget(self.hiz_spin, 1, 1)
        hiz_dugme = QPushButton("HIZ AYARLA")
        hiz_dugme.clicked.connect(lambda: self._komut("hiz_degistir", float(self.hiz_spin.value())))
        izgara.addWidget(hiz_dugme, 1, 2)
        duzen.addLayout(izgara)

        bilgi = QLabel("Haritada SAĞ TIK → 'Buraya Uç (GUIDED)'.\n"
                       "YKİ'den komut göndermek otonomiyi bozmaz (şartname 6.1.7);\n"
                       "kumandayla uçurmak manuel sayılır.")
        bilgi.setProperty("rol", "soluk")
        bilgi.setWordWrap(True)
        duzen.addWidget(bilgi)
        duzen.addStretch(1)

    # ---- yardımcılar ----
    def _komut(self, metod: str, *arg) -> None:
        kaynak = self.komut_kaynagi_getir()
        if kaynak is None:
            self.uyg.durum.olay("HATA", "Komut gönderilemedi: bağlı bir telemetri kaynağı yok")
            return
        getattr(kaynak, metod)(*arg)

    def _mod_ayarla(self) -> None:
        self._komut("mod_ayarla", self.mod_secim.currentText())

    def _arm(self, aktif: bool) -> None:
        soru = "Motorlar AKTİF edilsin mi (ARM)?" if aktif else \
               "DISARM edilsin mi? Havada DISARM aracın düşmesine yol açar!"
        if QMessageBox.question(self, "Onay", soru,
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self._komut("arm", aktif)

    def guided_irtifa(self) -> float:
        """Haritadaki 'Buraya Uç' bu irtifayı kullanır."""
        return float(self.irtifa_spin.value())


class GorevPlaniSeridi(QWidget):
    """Harita altındaki görev planı (waypoint) şeridi.

    WP'ler haritaya SAĞ TIK ile eklenir; buradan araca yüklenir/okunur.
    `wps` listesi harita ile paylaşılır: [[enlem, boylam, irtifa], ...]
    """

    def __init__(self, uyg, komut_kaynagi_getir):
        super().__init__()
        self.uyg = uyg
        self.komut_kaynagi_getir = komut_kaynagi_getir
        self.wps: list = []
        self._son_gorev_surumu = 0
        duzen = QHBoxLayout(self)
        duzen.setContentsMargins(0, 2, 0, 0)
        duzen.setSpacing(6)

        baslik = QLabel("GÖREV PLANI")
        baslik.setStyleSheet(f"color:{stiller.VURGU}; font-weight:bold;")
        duzen.addWidget(baslik)
        self.sayi_etiket = QLabel("0 WP")
        self.sayi_etiket.setProperty("rol", "deger")
        duzen.addWidget(self.sayi_etiket)

        duzen.addWidget(QLabel("WP irtifası:"))
        self.irtifa_spin = QSpinBox()
        self.irtifa_spin.setRange(10, 500)
        self.irtifa_spin.setSuffix(" m")
        self.irtifa_spin.setValue(int(uyg.ayarlar.al("komut", "varsayilan_irtifa", varsayilan=100)))
        duzen.addWidget(self.irtifa_spin)

        self.kalkis_kutu = QCheckBox("İlk WP = KALKIŞ")
        self.kalkis_kutu.setChecked(True)
        duzen.addWidget(self.kalkis_kutu)
        duzen.addStretch(1)

        yukle = QPushButton("ARACA YÜKLE")
        yukle.setProperty("rol", "onay")
        yukle.clicked.connect(self._yukle)
        oku = QPushButton("ARAÇTAN OKU")
        oku.clicked.connect(self._oku)
        auto = QPushButton("GÖREVİ BAŞLAT (AUTO)")
        auto.clicked.connect(lambda: self._komut("mod_ayarla", "AUTO"))
        son_sil = QPushButton("SON WP SİL")
        son_sil.clicked.connect(self._son_sil)
        temizle = QPushButton("TEMİZLE")
        temizle.setProperty("rol", "tehlike")
        temizle.clicked.connect(self._temizle)
        for d in (yukle, oku, auto, son_sil, temizle):
            duzen.addWidget(d)

    # ---- işlemler ----
    def _komut(self, metod: str, *arg) -> None:
        kaynak = self.komut_kaynagi_getir()
        if kaynak is None:
            self.uyg.durum.olay("HATA", "Komut gönderilemedi: bağlı bir telemetri kaynağı yok")
            return
        getattr(kaynak, metod)(*arg)

    def wp_ekle(self, enlem: float, boylam: float) -> None:
        self.wps.append([float(enlem), float(boylam), float(self.irtifa_spin.value())])
        self.uyg.durum.olay("BILGI", f"WP{len(self.wps)} eklendi: {enlem:.6f}, {boylam:.6f} @ {self.irtifa_spin.value()} m")

    def wp_sil(self, indeks: int) -> None:
        if 0 <= indeks < len(self.wps):
            self.wps.pop(indeks)
            self.uyg.durum.olay("BILGI", f"WP{indeks + 1} silindi ({len(self.wps)} kaldı)")

    def _son_sil(self) -> None:
        if self.wps:
            self.wp_sil(len(self.wps) - 1)

    def _temizle(self) -> None:
        self.wps.clear()
        self.uyg.durum.olay("BILGI", "Görev planı temizlendi (yalnız YKİ tarafı; araçtaki görev durur)")

    def _yukle(self) -> None:
        if not self.wps:
            self.uyg.durum.olay("HATA", "Yüklenecek WP yok — haritada sağ tık ile 'Waypoint ekle'")
            return
        liste = []
        for i, (lat, lon, alt) in enumerate(self.wps):
            liste.append({"enlem": lat, "boylam": lon, "irtifa": alt,
                          "kalkis": bool(i == 0 and self.kalkis_kutu.isChecked())})
        self._komut("gorev_yukle", liste)

    def _oku(self) -> None:
        self._komut("gorev_oku")

    def guncelle(self, durum) -> None:
        if durum.gorev_surumu != self._son_gorev_surumu:
            self._son_gorev_surumu = durum.gorev_surumu
            self.wps[:] = [[a, b, c] for a, b, c in durum.gorev_wps]
        self.sayi_etiket.setText(f"{len(self.wps)} WP")


class SunucuPaneli(QWidget):
    def __init__(self, uyg):
        super().__init__()
        self.uyg = uyg
        a = uyg.ayarlar
        duzen = QVBoxLayout(self)

        kutu = QGroupBox("YARIŞMA SUNUCUSU BAĞLANTISI")
        form = QFormLayout(kutu)
        self.url_girdi = QLineEdit(str(a.al("sunucu", "url", varsayilan="")))
        self.kadi_girdi = QLineEdit(str(a.al("sunucu", "kadi", varsayilan="")))
        self.sifre_girdi = QLineEdit(str(a.al("sunucu", "sifre", varsayilan="")))
        self.sifre_girdi.setEchoMode(QLineEdit.Password)
        form.addRow("Sunucu adresi:", self.url_girdi)
        form.addRow("Kullanıcı adı:", self.kadi_girdi)
        form.addRow("Şifre:", self.sifre_girdi)
        self.giris_dugme = QPushButton("SUNUCUYA GİRİŞ YAP")
        self.giris_dugme.setProperty("rol", "onay")
        self.giris_dugme.clicked.connect(self._giris)
        form.addRow(self.giris_dugme)
        self.giris_durum = QLabel("Oturum açılmadı")
        self.giris_durum.setProperty("rol", "deger")
        form.addRow("Durum:", self.giris_durum)
        duzen.addWidget(kutu)

        tkutu = QGroupBox("TELEMETRİ GÖNDERİMİ (1–2 Hz)")
        tform = QFormLayout(tkutu)
        self.hz_spin = QDoubleSpinBox()
        self.hz_spin.setRange(1.0, 2.0)
        self.hz_spin.setSingleStep(0.25)
        self.hz_spin.setValue(float(a.al("sunucu", "telemetri_hz", varsayilan=1.5)))
        self.hz_spin.valueChanged.connect(self._hz_degisti)
        tform.addRow("Gönderim hızı (Hz):", self.hz_spin)
        self.telemetri_dugme = QPushButton("TELEMETRİYİ BAŞLAT")
        self.telemetri_dugme.setCheckable(True)
        self.telemetri_dugme.toggled.connect(self._telemetri_degisti)
        tform.addRow(self.telemetri_dugme)
        self.sayac_etiket = QLabel("--")
        self.sayac_etiket.setProperty("rol", "deger")
        tform.addRow("Gönderilen / hata:", self.sayac_etiket)
        self.son_kod_etiket = QLabel("--")
        tform.addRow("Son HTTP durumu:", self.son_kod_etiket)
        duzen.addWidget(tkutu)

        skutu = QGroupBox("SUNUCU SAATİ / SENKRON")
        sform = QFormLayout(skutu)
        self.saat_etiket = QLabel("--:--:--.---")
        self.saat_etiket.setProperty("rol", "deger")
        self.saat_etiket.setStyleSheet(f"font-size:20px; color:{stiller.VURGU};")
        sform.addRow("Sunucu saati:", self.saat_etiket)
        self.gecikme_etiket = QLabel("--")
        sform.addRow("Gecikme (RTT):", self.gecikme_etiket)
        duzen.addWidget(skutu)

        pkutu = QGroupBox("GÖNDERİLEN GÖREV PAKETLERİ")
        pform = QFormLayout(pkutu)
        self.kilit_sayac = QLabel("0")
        self.kilit_sayac.setProperty("rol", "deger")
        self.kamikaze_sayac = QLabel("0")
        self.kamikaze_sayac.setProperty("rol", "deger")
        pform.addRow("Kilitlenme paketi:", self.kilit_sayac)
        pform.addRow("Kamikaze paketi:", self.kamikaze_sayac)
        duzen.addWidget(pkutu)
        duzen.addStretch(1)

    def _giris(self) -> None:
        a = self.uyg.ayarlar
        a.veri["sunucu"]["url"] = self.url_girdi.text().strip()
        a.veri["sunucu"]["kadi"] = self.kadi_girdi.text().strip()
        a.veri["sunucu"]["sifre"] = self.sifre_girdi.text()
        with self.uyg.durum.kilitli():
            self.uyg.durum.sunucu.url = a.veri["sunucu"]["url"].rstrip("/")
        self.uyg.istemci.giris_yap()

    def _hz_degisti(self, deger: float) -> None:
        self.uyg.ayarlar.veri["sunucu"]["telemetri_hz"] = float(deger)

    def _telemetri_degisti(self, acik: bool) -> None:
        self.telemetri_dugme.setText("TELEMETRİYİ DURDUR" if acik else "TELEMETRİYİ BAŞLAT")
        self.uyg.istemci.telemetri_baslat(acik)

    def guncelle(self, durum, saat) -> None:
        s = durum.sunucu
        if s.giris_ok:
            self.giris_durum.setText(f"GİRİŞ OK — Takım {s.takim_numarasi}")
            self.giris_durum.setStyleSheet(f"color:{stiller.IYI}; font-weight:bold;")
        else:
            self.giris_durum.setText("Oturum açılmadı")
            self.giris_durum.setStyleSheet(f"color:{stiller.UYARI};")
        self.sayac_etiket.setText(f"{s.gonderilen_telemetri} / {s.telemetri_hata}")
        if s.son_durum_kodu == 200:
            self.son_kod_etiket.setText("200 OK")
            self.son_kod_etiket.setStyleSheet(f"color:{stiller.IYI};")
        elif s.son_durum_kodu == 0 and s.son_hata:
            self.son_kod_etiket.setText(s.son_hata[:60])
            self.son_kod_etiket.setStyleSheet(f"color:{stiller.HATA};")
        elif s.son_durum_kodu:
            self.son_kod_etiket.setText(s.son_hata or str(s.son_durum_kodu))
            self.son_kod_etiket.setStyleSheet(f"color:{stiller.UYARI};")
        if s.saat_ofset_ok:
            d = saat.simdi_dict()
            gun = f"Gün {saat.gun} — " if saat.gun else ""
            self.saat_etiket.setText(f"{gun}{d['saat']:02d}:{d['dakika']:02d}:{d['saniye']:02d}.{d['milisaniye']:03d}")
            self.gecikme_etiket.setText(f"{saat.gecikme_ms:.0f} ms")
        else:
            self.saat_etiket.setText("SENKRON YOK")
        self.kilit_sayac.setText(str(s.kilit_paket_sayisi))
        self.kamikaze_sayac.setText(str(s.kamikaze_paket_sayisi))


class GorevPaneli(QWidget):
    """Kilitlenme ve Kamikaze görev paketleri."""

    def __init__(self, uyg):
        super().__init__()
        self.uyg = uyg
        self.dalis_baslangic_ms: int | None = None
        duzen = QVBoxLayout(self)

        kkutu = QGroupBox("KİLİTLENME (Savaşan İHA görevi)")
        kduzen = QVBoxLayout(kkutu)
        self.kilit_cubuk = QProgressBar()
        self.kilit_cubuk.setRange(0, 1000)
        self.kilit_cubuk.setFormat("kilit yok")
        kduzen.addWidget(self.kilit_cubuk)
        self.hedef_etiket = QLabel("Hedef kutusu: --")
        self.hedef_etiket.setProperty("rol", "deger")
        kduzen.addWidget(self.hedef_etiket)
        self.oto_kilit_kutu = QCheckBox("Kilit bitince paketi OTOMATİK gönder (4 sn dolduysa)")
        self.oto_kilit_kutu.setChecked(bool(self.uyg.ayarlar.al("gorev", "kilit_otomatik_gonder", varsayilan=True)))
        kduzen.addWidget(self.oto_kilit_kutu)
        satir = QHBoxLayout()
        satir.addWidget(QLabel("otonom_kilitlenme:"))
        self.otonom_secim = QComboBox()
        self.otonom_secim.addItems(["Uçuş modundan (otomatik)", "1 (otonom)", "0 (manuel)"])
        satir.addWidget(self.otonom_secim, 1)
        kduzen.addLayout(satir)
        self.kilit_gonder_dugme = QPushButton("KİLİTLENME PAKETİ GÖNDER (bitiş = şimdi)")
        self.kilit_gonder_dugme.setProperty("rol", "onay")
        self.kilit_gonder_dugme.clicked.connect(self._kilit_gonder_elle)
        kduzen.addWidget(self.kilit_gonder_dugme)
        duzen.addWidget(kkutu)

        akutu = QGroupBox("KAMİKAZE (QR görevi)")
        aduzen = QVBoxLayout(akutu)
        qr_satir = QHBoxLayout()
        self.qr_al_dugme = QPushButton("QR KOORDİNATINI AL")
        self.qr_al_dugme.clicked.connect(self.uyg.istemci.qr_sorgula)
        self.qr_konum_etiket = QLabel("--")
        self.qr_konum_etiket.setProperty("rol", "deger")
        qr_satir.addWidget(self.qr_al_dugme)
        qr_satir.addWidget(self.qr_konum_etiket, 1)
        aduzen.addLayout(qr_satir)
        self.dalis_dugme = QPushButton("DALIŞ BAŞLADI (zamanı işaretle)")
        self.dalis_dugme.clicked.connect(self._dalis_basla)
        aduzen.addWidget(self.dalis_dugme)
        self.dalis_etiket = QLabel("Dalış: bekleniyor")
        aduzen.addWidget(self.dalis_etiket)
        self.qr_metin_girdi = QLineEdit()
        self.qr_metin_girdi.setPlaceholderText("QR metni (görüntü işlemeden otomatik dolar)")
        aduzen.addWidget(self.qr_metin_girdi)
        self.oto_kamikaze_kutu = QCheckBox("QR okununca paketi OTOMATİK gönder (dalış işaretliyken)")
        self.oto_kamikaze_kutu.setChecked(bool(self.uyg.ayarlar.al("gorev", "kamikaze_otomatik_gonder", varsayilan=True)))
        aduzen.addWidget(self.oto_kamikaze_kutu)
        self.kamikaze_gonder_dugme = QPushButton("KAMİKAZE PAKETİ GÖNDER (bitiş = şimdi)")
        self.kamikaze_gonder_dugme.setProperty("rol", "onay")
        self.kamikaze_gonder_dugme.clicked.connect(self._kamikaze_gonder)
        aduzen.addWidget(self.kamikaze_gonder_dugme)
        self.sifirla_dugme = QPushButton("Kamikaze durumunu sıfırla")
        self.sifirla_dugme.clicked.connect(self._sifirla)
        aduzen.addWidget(self.sifirla_dugme)
        duzen.addWidget(akutu)
        duzen.addStretch(1)

    # --- kilit ---
    def otonom_bayragi(self) -> bool:
        secim = self.otonom_secim.currentIndex()
        if secim == 1:
            return True
        if secim == 2:
            return False
        return bool(self.uyg.durum.anlik().iha.otonom)

    def _kilit_gonder_elle(self) -> None:
        self.uyg.istemci.kilitlenme_gonder(self.uyg.saat.simdi_ms(), self.otonom_bayragi())

    def kilit_tamamlandi(self, bitis_ms: int) -> None:
        """KilitTakip 4 sn dolduran oturum bitince çağırır (otomatik kip)."""
        if self.oto_kilit_kutu.isChecked():
            self.uyg.istemci.kilitlenme_gonder(bitis_ms, self.otonom_bayragi())
        else:
            self.uyg.durum.olay("UYARI", "Kilit oturumu 4 sn'yi doldurdu — paket göndermek için düğmeye basın")

    # --- kamikaze ---
    def _dalis_basla(self) -> None:
        self.dalis_baslangic_ms = self.uyg.saat.simdi_ms()
        d = saat_dict_ms(self.dalis_baslangic_ms)
        self.uyg.durum.olay("BILGI", f"Kamikaze dalış başlangıcı işaretlendi: {d['saat']:02d}:{d['dakika']:02d}:{d['saniye']:02d}.{d['milisaniye']:03d}")

    def qr_geldi(self, metin: str) -> None:
        self.qr_metin_girdi.setText(metin)
        if self.oto_kamikaze_kutu.isChecked() and self.dalis_baslangic_ms is not None:
            self._kamikaze_gonder()

    def _kamikaze_gonder(self) -> None:
        if self.dalis_baslangic_ms is None:
            self.uyg.durum.olay("HATA", "Kamikaze gönderilemedi: önce 'DALIŞ BAŞLADI' ile başlangıç zamanını işaretleyin")
            return
        metin = self.qr_metin_girdi.text().strip()
        if not metin:
            self.uyg.durum.olay("HATA", "Kamikaze gönderilemedi: QR metni boş")
            return
        self.uyg.istemci.kamikaze_gonder(self.dalis_baslangic_ms, self.uyg.saat.simdi_ms(), metin)
        self.dalis_baslangic_ms = None

    def _sifirla(self) -> None:
        self.dalis_baslangic_ms = None
        self.qr_metin_girdi.clear()

    def guncelle(self, durum, kilit_takip) -> None:
        kilit = durum.kilit
        esik = max(0.1, kilit_takip.esik_sn)
        sure = kilit_takip.anlik_sure
        self.kilit_cubuk.setValue(int(min(1.0, sure / esik) * 1000))
        if kilit.aktif and kilit.taze(1.0):
            self.kilit_cubuk.setFormat(f"KİLİT: {sure:.1f} / {esik:.1f} sn")
            renk = stiller.IYI if sure >= esik else stiller.UYARI
            self.kilit_cubuk.setStyleSheet(f"QProgressBar::chunk {{ background: {renk}; }}")
            self.hedef_etiket.setText(
                f"Hedef kutusu: X={kilit.merkez_x} Y={kilit.merkez_y} "
                f"G={kilit.genislik} Y={kilit.yukseklik} ({kilit.video_genislik}x{kilit.video_yukseklik})")
        else:
            self.kilit_cubuk.setFormat("kilit yok")
            self.kilit_cubuk.setStyleSheet("")
            self.hedef_etiket.setText("Hedef kutusu: --")
        if self.dalis_baslangic_ms is not None:
            d = saat_dict_ms(self.dalis_baslangic_ms)
            self.dalis_etiket.setText(f"Dalış başlangıcı: {d['saat']:02d}:{d['dakika']:02d}:{d['saniye']:02d}.{d['milisaniye']:03d} — QR bekleniyor")
            self.dalis_etiket.setStyleSheet(f"color:{stiller.UYARI}; font-weight:bold;")
        else:
            self.dalis_etiket.setText("Dalış: bekleniyor")
            self.dalis_etiket.setStyleSheet("")
        s = durum.sunucu
        if s.qr_var:
            self.qr_konum_etiket.setText(f"{s.qr_enlem:.6f}, {s.qr_boylam:.6f}")


class HssPaneli(QWidget):
    def __init__(self, uyg):
        super().__init__()
        self.uyg = uyg
        duzen = QVBoxLayout(self)
        satir = QHBoxLayout()
        self.sorgu_dugme = QPushButton("HSS SORGULA (tek seferlik)")
        self.sorgu_dugme.clicked.connect(self.uyg.istemci.hss_sorgula)
        self.surekli_kutu = QCheckBox("Sürekli sorgula")
        self.surekli_kutu.toggled.connect(self._surekli_degisti)
        satir.addWidget(self.sorgu_dugme)
        satir.addWidget(self.surekli_kutu)
        satir.addStretch(1)
        duzen.addLayout(satir)
        self.tablo = QTableWidget(0, 5)
        self.tablo.setHorizontalHeaderLabels(["ID", "Enlem", "Boylam", "Yarıçap (m)", "Mesafe (m)"])
        self.tablo.horizontalHeader().setStretchLastSection(True)
        self.tablo.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tablo.setAlternatingRowColors(True)
        duzen.addWidget(self.tablo, 1)
        self.ihlal_etiket = QLabel("İhlal: 0.0 sn — ceza tahmini: 0")
        self.ihlal_etiket.setProperty("rol", "deger")
        duzen.addWidget(self.ihlal_etiket)
        bilgi = QLabel("Hakem duyurusundan sonra sorgulayın; duyuru yokken sunucu boş liste döndürür.\n"
                       "Kırmızı bölgede her tam saniye -5 puan; toplam 30 sn'de iniş çağrısı yapılır.")
        bilgi.setProperty("rol", "soluk")
        bilgi.setWordWrap(True)
        duzen.addWidget(bilgi)

    def _surekli_degisti(self, acik: bool) -> None:
        self.uyg.istemci.hss_sorgu_aktif = acik

    def guncelle(self, durum, ozet) -> None:
        liste = durum.hss_listesi
        iha = durum.iha
        if self.tablo.rowCount() != len(liste):
            self.tablo.setRowCount(len(liste))
        for i, bolge in enumerate(liste):
            mesafe = geo.haversine_m(iha.enlem, iha.boylam, bolge.enlem, bolge.boylam) if (iha.enlem or iha.boylam) else 0.0
            icinde = mesafe <= bolge.yaricap
            degerler = [str(bolge.id), f"{bolge.enlem:.6f}", f"{bolge.boylam:.6f}",
                        f"{bolge.yaricap:.0f}", f"{mesafe - bolge.yaricap:+.0f} (kenara)"]
            for j, metin in enumerate(degerler):
                oge = self.tablo.item(i, j)
                if oge is None:
                    oge = QTableWidgetItem()
                    self.tablo.setItem(i, j, oge)
                oge.setText(metin)
                oge.setForeground(Qt.red if icinde else Qt.white)
        self.ihlal_etiket.setText(
            f"İhlal: {ozet.hss_ihlal_toplam_sn:.1f} sn — ceza tahmini: {ozet.hss_ceza_tahmini:.0f} puan")
        self.ihlal_etiket.setStyleSheet(f"color:{stiller.HATA};" if ozet.hss_icinde else "")


class LogPaneli(QWidget):
    RENKLER = {"BILGI": stiller.YAZI_SOLUK, "UYARI": stiller.UYARI, "HATA": stiller.HATA, "BASARI": stiller.IYI}

    def __init__(self, kayit_klasoru: str):
        super().__init__()
        duzen = QVBoxLayout(self)
        duzen.setContentsMargins(4, 4, 4, 4)
        self.metin = QPlainTextEdit()
        self.metin.setReadOnly(True)
        self.metin.setMaximumBlockCount(3000)
        duzen.addWidget(self.metin)
        self._dosya = None
        try:
            os.makedirs(kayit_klasoru, exist_ok=True)
            ad = datetime.now().strftime("yki_%Y%m%d_%H%M%S.log")
            self._dosya = open(os.path.join(kayit_klasoru, ad), "a", encoding="utf-8")  # noqa: SIM115
        except OSError:
            self._dosya = None

    def ekle(self, olay) -> None:
        zaman = datetime.fromtimestamp(olay.zaman).strftime("%H:%M:%S")
        renk = self.RENKLER.get(olay.seviye, stiller.YAZI)
        self.metin.appendHtml(
            f'<span style="color:{stiller.YAZI_SOLUK}">[{zaman}]</span> '
            f'<span style="color:{renk}; font-weight:bold">{olay.seviye}</span> '
            f'<span style="color:{stiller.YAZI}">{html.escape(olay.metin)}</span>')
        if self._dosya:
            try:
                self._dosya.write(f"[{zaman}] {olay.seviye}: {olay.metin}\n")
                self._dosya.flush()
            except OSError:
                pass


class _VideoIsParcasi(QThread):
    kare_hazir = Signal(QImage)
    hata = Signal(str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self._calisiyor = True

    def durdur(self) -> None:
        self._calisiyor = False

    def run(self) -> None:
        kaynak = self.url
        if kaynak.isdigit():
            kaynak = int(kaynak)
        yakala = cv2.VideoCapture(kaynak)
        if not yakala.isOpened():
            self.hata.emit(f"Video kaynağı açılamadı: {self.url}")
            return
        while self._calisiyor:
            tamam, kare = yakala.read()
            if not tamam:
                self.hata.emit("Video akışı kesildi")
                break
            rgb = cv2.cvtColor(kare, cv2.COLOR_BGR2RGB)
            h, w, _ = rgb.shape
            self.kare_hazir.emit(QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy())
        yakala.release()


class VideoPaneli(QWidget):
    """İsteğe bağlı canlı kamera izleme (şartname: YKİ'ye 640x480 aktarım tavsiye edilir)."""

    def __init__(self, uyg):
        super().__init__()
        self.uyg = uyg
        self._is = None
        duzen = QVBoxLayout(self)
        satir = QHBoxLayout()
        self.url_girdi = QLineEdit(str(uyg.ayarlar.al("gorev", "video_url", varsayilan="")))
        self.url_girdi.setPlaceholderText("rtsp://... | udp/gstreamer boru hattı | 0 (yerel kamera)")
        self.baslat_dugme = QPushButton("BAŞLAT")
        self.baslat_dugme.clicked.connect(self._degistir)
        satir.addWidget(self.url_girdi, 1)
        satir.addWidget(self.baslat_dugme)
        duzen.addLayout(satir)
        self.goruntu = QLabel("Video kapalı" if cv2 else "opencv-python kurulu değil (pip install opencv-python)")
        self.goruntu.setAlignment(Qt.AlignCenter)
        self.goruntu.setMinimumHeight(240)
        self.goruntu.setStyleSheet(f"background:#05070a; border:1px solid {stiller.KENAR}; border-radius:6px;")
        duzen.addWidget(self.goruntu, 1)
        if cv2 is None:
            self.baslat_dugme.setEnabled(False)

    def _degistir(self) -> None:
        if self._is is not None:
            self._is.durdur()
            self._is.wait(1500)
            self._is = None
            self.baslat_dugme.setText("BAŞLAT")
            self.goruntu.setText("Video kapalı")
            return
        url = self.url_girdi.text().strip()
        if not url:
            self.uyg.durum.olay("UYARI", "Video adresi boş")
            return
        self._is = _VideoIsParcasi(url)
        self._is.kare_hazir.connect(self._kare)
        self._is.hata.connect(lambda m: self.uyg.durum.olay("HATA", m))
        self._is.start()
        self.baslat_dugme.setText("DURDUR")

    def _kare(self, resim: QImage) -> None:
        self.goruntu.setPixmap(QPixmap.fromImage(resim).scaled(
            self.goruntu.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def kapat(self) -> None:
        if self._is is not None:
            self._is.durdur()
            self._is.wait(1500)
            self._is = None
