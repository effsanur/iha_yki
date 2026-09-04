"""Uydu görüntülü taktik harita + görev planı (waypoint) işlemleri.

Şartname 7.2.2 / 6.4.2 gereği teknik kontrolde zorunlu katmanlar:
  - Yarışma sınırları (poligon)
  - Rakip hava araçları (sunucu konumBilgileri)
  - Aktif Hava Savunma Sistemi / Sinyal Karıştırma bölgeleri (kırmızı daireler)

Uydu görüntüsü XYZ karo (tile) sunucusundan indirilir ve DİSKE önbelleklenir;
sahada internet yokken önbellekten çalışır ("Uydu haritasını önbelleğe indir"
aracıyla yarışma alanı önceden indirilebilir). Karo yokken koyu ızgara çizilir.

Sağ tık menüsü: Waypoint ekle · Buraya Uç (GUIDED) · Ev işaretle · merkezle ·
koordinat kopyala. Waypoint listesi ana penceredeki Görev Planı şeridiyle ortak.
"""

from __future__ import annotations

import math
import os
import queue
import threading
import time
from collections import OrderedDict

import requests
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from .. import geo
from . import stiller

KARO_BOYUT = 256
DUNYA_CEVRE = 156543.03392  # m/px, z=0, ekvator


def karo_xy(lat: float, lon: float, z: int) -> tuple[float, float]:
    """Enlem/boylam -> kesirli karo koordinatı (slippy map)."""
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(max(-85.05, min(85.05, lat)))
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def karo_konum(x: float, y: float, z: int) -> tuple[float, float]:
    """Karo koordinatı -> enlem/boylam (karonun KB köşesi)."""
    n = 2 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lat, lon


class KaroKatmani:
    """XYZ uydu karolarını indirir, diskte + bellekte önbellekler, çizer."""

    def __init__(self, ayarlar, durum):
        self.ayarlar = ayarlar
        self.durum = durum
        self.aktif = bool(ayarlar.al("harita", "uydu", "aktif", varsayilan=True))
        self._bellek: OrderedDict = OrderedDict()   # (z,x,y) -> QPixmap
        self._kuyruk: queue.Queue = queue.Queue()
        self._beklemede: set = set()
        self._basarisiz: dict = {}                  # karo -> monotonic zaman
        self._kilit = threading.Lock()
        self._isciler_basladi = False
        self._toplu_is = None

    # ---- ayarlar ----
    @property
    def url_kalip(self) -> str:
        return str(self.ayarlar.al("harita", "uydu", "url", varsayilan=""))

    @property
    def klasor(self) -> str:
        k = str(self.ayarlar.al("harita", "uydu", "onbellek", varsayilan="harita_onbellek"))
        if not os.path.isabs(k):
            k = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), k)
        return k

    def _yol(self, z: int, x: int, y: int) -> str:
        return os.path.join(self.klasor, str(z), str(x), f"{y}.jpg")

    # ---- karo temini ----
    def _pixmap(self, z: int, x: int, y: int):
        from PySide6.QtGui import QPixmap
        anahtar = (z, x, y)
        pm = self._bellek.get(anahtar)
        if pm is not None:
            self._bellek.move_to_end(anahtar)
            return pm
        yol = self._yol(z, x, y)
        if os.path.exists(yol):
            pm = QPixmap(yol)
            if not pm.isNull():
                self._bellek[anahtar] = pm
                if len(self._bellek) > 512:
                    self._bellek.popitem(last=False)
                return pm
        return None

    def _iste(self, z: int, x: int, y: int) -> None:
        if not self.url_kalip:
            return
        anahtar = (z, x, y)
        with self._kilit:
            if anahtar in self._beklemede:
                return
            if time.monotonic() - self._basarisiz.get(anahtar, -999.0) < 30.0:
                return
            self._beklemede.add(anahtar)
        self._kuyruk.put(anahtar)
        self._iscileri_baslat()

    def _iscileri_baslat(self) -> None:
        if self._isciler_basladi:
            return
        self._isciler_basladi = True
        for _ in range(2):
            threading.Thread(target=self._isci, daemon=True, name="karo-indirici").start()

    def _isci(self) -> None:
        oturum = requests.Session()
        oturum.headers["User-Agent"] = "SavasanIhaYKI/1.0"
        while True:
            anahtar = self._kuyruk.get()
            z, x, y = anahtar
            try:
                self._indir(oturum, z, x, y)
            except Exception:  # noqa: BLE001
                with self._kilit:
                    self._basarisiz[anahtar] = time.monotonic()
            finally:
                with self._kilit:
                    self._beklemede.discard(anahtar)

    def _indir(self, oturum, z: int, x: int, y: int) -> bool:
        adres = self.url_kalip.format(z=z, x=x, y=y)
        cevap = oturum.get(adres, timeout=6)
        if cevap.status_code != 200 or not cevap.content:
            with self._kilit:
                self._basarisiz[(z, x, y)] = time.monotonic()
            return False
        yol = self._yol(z, x, y)
        os.makedirs(os.path.dirname(yol), exist_ok=True)
        with open(yol, "wb") as f:
            f.write(cevap.content)
        return True

    # ---- çizim ----
    def ciz(self, p: QPainter, harita: "HaritaWidget") -> None:
        if not self.aktif:
            return
        lat0 = harita.duzlem.lat0
        olcek = harita.olcek  # ekran px / metre
        z = int(round(math.log2(max(1e-9, DUNYA_CEVRE * math.cos(math.radians(lat0)) * olcek))))
        z = max(3, min(19, z))
        lat_kb, lon_kb = harita._cografi(0, 0)
        lat_gd, lon_gd = harita._cografi(harita.width(), harita.height())
        x0f, y0f = karo_xy(lat_kb, lon_kb, z)
        x1f, y1f = karo_xy(lat_gd, lon_gd, z)
        x0, x1 = int(math.floor(min(x0f, x1f))), int(math.floor(max(x0f, x1f)))
        y0, y1 = int(math.floor(min(y0f, y1f))), int(math.floor(max(y0f, y1f)))
        if (x1 - x0 + 1) * (y1 - y0 + 1) > 24 * 24:
            return  # aşırı uzak görünüm; indirme fırtınası yaratma
        n = 2 ** z
        for tx in range(x0, x1 + 1):
            for ty in range(max(0, y0), min(n - 1, y1) + 1):
                pm = self._pixmap(z, tx % n, ty)
                if pm is None:
                    self._iste(z, tx % n, ty)
                    continue
                kb_lat, kb_lon = karo_konum(tx, ty, z)
                gd_lat, gd_lon = karo_konum(tx + 1, ty + 1, z)
                p1 = harita._ekran(kb_lat, kb_lon)
                p2 = harita._ekran(gd_lat, gd_lon)
                hedef = QRectF(p1, p2).normalized().adjusted(-0.5, -0.5, 0.5, 0.5)
                p.drawPixmap(hedef, pm, QRectF(pm.rect()))
        # göz yormasın + katmanlar okunsun diye hafif karartma örtüsü
        p.fillRect(harita.rect(), QColor(10, 14, 18, 64))

    # ---- yarışma alanını önceden indirme ----
    def bolgeyi_indir(self, sinir: list, z_min: int = 13, z_max: int = 18) -> None:
        """Sınır kutusunu (400 m pay ile) verilen zoom aralığında indirir (arka plan)."""
        if self._toplu_is is not None and self._toplu_is.is_alive():
            self.durum.olay("UYARI", "Önbellek indirme zaten sürüyor")
            return
        if not sinir or not self.url_kalip:
            self.durum.olay("HATA", "Önbellek indirilemedi: sınır veya uydu URL tanımsız")
            return

        def isle():
            pay = 400.0 / 111319.49
            lat_min = min(p[0] for p in sinir) - pay
            lat_max = max(p[0] for p in sinir) + pay
            lon_min = min(p[1] for p in sinir) - pay / math.cos(math.radians(lat_min))
            lon_max = max(p[1] for p in sinir) + pay / math.cos(math.radians(lat_min))
            oturum = requests.Session()
            oturum.headers["User-Agent"] = "SavasanIhaYKI/1.0"
            toplam = indirilen = mevcut = hata = 0
            for z in range(z_min, z_max + 1):
                x0, y1 = karo_xy(lat_min, lon_min, z)
                x1, y0 = karo_xy(lat_max, lon_max, z)
                for tx in range(int(min(x0, x1)), int(max(x0, x1)) + 1):
                    for ty in range(int(min(y0, y1)), int(max(y0, y1)) + 1):
                        toplam += 1
                        if toplam > 8000:
                            self.durum.olay("UYARI", "Önbellek indirme 8000 karo sınırında durdu")
                            return
                        if os.path.exists(self._yol(z, tx, ty)):
                            mevcut += 1
                            continue
                        try:
                            if self._indir(oturum, z, tx, ty):
                                indirilen += 1
                            else:
                                hata += 1
                        except Exception:  # noqa: BLE001
                            hata += 1
                        if (indirilen + hata) % 100 == 0 and (indirilen + hata):
                            self.durum.olay("BILGI", f"Uydu önbelleği: {indirilen} indirildi, {hata} hata (z{z})")
            self.durum.olay("BASARI", f"Uydu önbelleği tamam: {indirilen} yeni, {mevcut} mevcut, {hata} hata → {self.klasor}")

        self.durum.olay("BILGI", f"Uydu önbelleği indiriliyor (z{z_min}-{z_max})...")
        self._toplu_is = threading.Thread(target=isle, daemon=True, name="karo-toplu")
        self._toplu_is.start()


class HaritaWidget(QWidget):
    def __init__(self, ayarlar, durum_modeli=None, parent=None):
        super().__init__(parent)
        self.ayarlar = ayarlar
        merkez = ayarlar.al("harita", "merkez", varsayilan=[40.2306, 29.0060])
        self.duzlem = geo.YerelDuzlem(float(merkez[0]), float(merkez[1]))
        self.olcek = 1.0                  # piksel / metre
        self.merkez_m = [0.0, 0.0]        # görünüm merkezi (metre)
        self.takip = True
        self.durum = None                 # DurumModeli anlık kopyası
        self.ozet = None                  # KuralOzeti
        self._surukleme = None
        self._imlec_konum = None
        self._ilk_sigdirma = False
        self.karolar = KaroKatmani(ayarlar, durum_modeli) if durum_modeli is not None else None
        # geri çağrılar (ana pencere bağlar)
        self.buraya_uc_cb = None          # callable(lat, lon)
        self.ev_isaretle_cb = None        # callable(lat, lon)
        self.wp_ekle_cb = None            # callable(lat, lon)
        self.wp_sil_cb = None             # callable(indeks)
        self.wp_listesi: list = []        # [[lat, lon, alt], ...] (Görev Planı şeridiyle ortak)
        self.setMinimumSize(420, 380)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)

    # ---------- veri ----------
    def guncelle(self, durum, ozet) -> None:
        self.durum = durum
        self.ozet = ozet
        if not self._ilk_sigdirma:
            self.sigdir()
        if self.takip and durum and durum.iha.taze(5.0) and (durum.iha.enlem or durum.iha.boylam):
            x, y = self.duzlem.metreye(durum.iha.enlem, durum.iha.boylam)
            self.merkez_m = [x, y]
        self.update()

    def sigdir(self) -> None:
        sinir = self.ayarlar.sinir
        if len(sinir) >= 3:
            merkez_lat = sum(p[0] for p in sinir) / len(sinir)
            merkez_lon = sum(p[1] for p in sinir) / len(sinir)
            self.duzlem.ayarla(merkez_lat, merkez_lon)
            xs, ys = [], []
            for lat, lon in sinir:
                x, y = self.duzlem.metreye(lat, lon)
                xs.append(x)
                ys.append(y)
            genislik_m = max(50.0, max(xs) - min(xs))
            yukseklik_m = max(50.0, max(ys) - min(ys))
            self.merkez_m = [(max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0]
            if self.width() > 40 and self.height() > 40:
                self.olcek = min((self.width() - 60) / genislik_m, (self.height() - 60) / yukseklik_m)
                self.olcek = max(0.02, min(30.0, self.olcek))
                self._ilk_sigdirma = True

    # ---------- dönüşümler ----------
    def _ekran(self, lat: float, lon: float) -> QPointF:
        x, y = self.duzlem.metreye(lat, lon)
        px = self.width() / 2.0 + (x - self.merkez_m[0]) * self.olcek
        py = self.height() / 2.0 - (y - self.merkez_m[1]) * self.olcek
        return QPointF(px, py)

    def _cografi(self, px: float, py: float) -> tuple[float, float]:
        x = (px - self.width() / 2.0) / self.olcek + self.merkez_m[0]
        y = -(py - self.height() / 2.0) / self.olcek + self.merkez_m[1]
        return self.duzlem.dereceye(x, y)

    # ---------- fare ----------
    def wheelEvent(self, e) -> None:
        adim = 1.18 if e.angleDelta().y() > 0 else 1 / 1.18
        eski = self.olcek
        self.olcek = max(0.02, min(30.0, self.olcek * adim))
        poz = e.position()
        dx = (poz.x() - self.width() / 2.0)
        dy = (poz.y() - self.height() / 2.0)
        self.merkez_m[0] += dx / eski - dx / self.olcek
        self.merkez_m[1] -= dy / eski - dy / self.olcek
        self.update()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._surukleme = e.position()
            self.takip = False

    def mouseMoveEvent(self, e) -> None:
        self._imlec_konum = e.position()
        if self._surukleme is not None:
            fark = e.position() - self._surukleme
            self._surukleme = e.position()
            self.merkez_m[0] -= fark.x() / self.olcek
            self.merkez_m[1] += fark.y() / self.olcek
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        self._surukleme = None

    def mouseDoubleClickEvent(self, e) -> None:
        self.takip = True

    def _yakin_wp(self, poz) -> int:
        """Ekranda tıklanan noktaya 20 px'ten yakın WP indeksi, yoksa -1."""
        for i, wp in enumerate(self.wp_listesi):
            nokta = self._ekran(wp[0], wp[1])
            if (nokta - poz).manhattanLength() < 20:
                return i
        return -1

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        lat, lon = self._cografi(e.pos().x(), e.pos().y())
        yakin = self._yakin_wp(QPointF(e.pos()))
        menu = QMenu(self)
        wp = menu.addAction(f"Waypoint ekle  —  {lat:.6f}, {lon:.6f}")
        wp.setEnabled(self.wp_ekle_cb is not None)
        wp_sil = menu.addAction(f"WP{yakin + 1}'i sil") if yakin >= 0 else None
        uc = menu.addAction("Buraya Uç (GUIDED)")
        uc.setEnabled(self.buraya_uc_cb is not None)
        ev = menu.addAction("Ev noktasını buraya işaretle")
        menu.addSeparator()
        merkezle = menu.addAction("Haritayı buraya merkezle")
        kopyala = menu.addAction("Koordinatı kopyala")
        secim = menu.exec(e.globalPos())
        if secim is None:
            return
        if secim == wp and self.wp_ekle_cb:
            self.wp_ekle_cb(lat, lon)
        elif wp_sil is not None and secim == wp_sil and self.wp_sil_cb:
            self.wp_sil_cb(yakin)
        elif secim == uc and self.buraya_uc_cb:
            self.buraya_uc_cb(lat, lon)
        elif secim == ev and self.ev_isaretle_cb:
            self.ev_isaretle_cb(lat, lon)
        elif secim == merkezle:
            x, y = self.duzlem.metreye(lat, lon)
            self.merkez_m = [x, y]
            self.takip = False
            self.update()
        elif secim == kopyala:
            QApplication.clipboard().setText(f"{lat:.7f}, {lon:.7f}")

    # ---------- çizim ----------
    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(stiller.ARKA))
        if self.karolar is not None:
            try:
                self.karolar.ciz(p, self)
            except Exception:  # noqa: BLE001 - karo hatası haritayı düşürmesin
                pass
        if self.karolar is None or not self.karolar.aktif:
            self._izgara(p)
        self._sinir(p)
        self._hss(p)
        self._qr(p)
        self._ev(p)
        self._wp_ciz(p)
        self._guided_hedef(p)
        self._rakipler(p)
        self._kendi(p)
        self._ust_bilgi(p)
        self._olcek_cubugu(p)
        p.end()

    def _etiket(self, p: QPainter, nokta: QPointF, metin: str, renk: QColor, boyut: int = 9) -> None:
        """Uydu görüntüsü üzerinde okunaklı, koyu zeminli etiket."""
        f = QFont("Consolas", boyut)
        f.setBold(True)
        p.setFont(f)
        olcum = p.fontMetrics()
        genislik = olcum.horizontalAdvance(metin) + 8
        r = QRectF(nokta.x(), nokta.y() - olcum.height() + 2, genislik, olcum.height() + 2)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(10, 14, 18, 170)))
        p.drawRoundedRect(r, 3, 3)
        p.setPen(QPen(renk, 1))
        p.drawText(r, Qt.AlignCenter, metin)

    def _izgara(self, p: QPainter) -> None:
        hedef_px = 90.0
        adim_m = hedef_px / self.olcek
        for aday in (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000):
            if aday >= adim_m:
                adim_m = aday
                break
        else:
            adim_m = 10000
        p.setPen(QPen(QColor(255, 255, 255, 14), 1))
        adim_px = adim_m * self.olcek
        if adim_px < 8:
            return
        bas_x = (self.width() / 2.0 - self.merkez_m[0] * self.olcek) % adim_px
        x = bas_x
        while x < self.width():
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            x += adim_px
        bas_y = (self.height() / 2.0 + self.merkez_m[1] * self.olcek) % adim_px
        y = bas_y
        while y < self.height():
            p.drawLine(QPointF(0, y), QPointF(self.width(), y))
            y += adim_px

    def _sinir(self, p: QPainter) -> None:
        sinir = self.ayarlar.sinir
        if len(sinir) < 3:
            self._etiket(p, QPointF(10, 24), "UYARI: yarışma sınırı tanımsız (config.yaml -> harita.sinir)", QColor(stiller.HATA))
            return
        poligon = QPolygonF([self._ekran(lat, lon) for lat, lon in sinir])
        ihlal = self.ozet is not None and not self.ozet.sinir_icinde
        renk = QColor(stiller.HATA) if ihlal else QColor(stiller.SINIR)
        p.setPen(QPen(renk, 3 if ihlal else 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(QBrush(QColor(214, 188, 87, 12)))
        p.drawPolygon(poligon)
        p.setBrush(Qt.NoBrush)
        for i, (lat, lon) in enumerate(sinir):
            nokta = self._ekran(lat, lon)
            p.setPen(QPen(renk, 1))
            p.drawEllipse(nokta, 3, 3)
            self._etiket(p, nokta + QPointF(5, -5), f"S{i + 1}", renk, 8)

    def _hss(self, p: QPainter) -> None:
        if not self.durum:
            return
        for bolge in self.durum.hss_listesi:
            merkez = self._ekran(bolge.enlem, bolge.boylam)
            r_px = max(4.0, bolge.yaricap * self.olcek)
            p.setPen(QPen(QColor(stiller.HSS), 2))
            p.setBrush(QBrush(QColor(207, 91, 91, 52)))
            p.drawEllipse(merkez, r_px, r_px)
            p.setBrush(Qt.NoBrush)
            self._etiket(p, merkez + QPointF(-24, -6), f"HSS-{bolge.id} r={bolge.yaricap:.0f}m", QColor(stiller.HSS))

    def _qr(self, p: QPainter) -> None:
        if not self.durum or not self.durum.sunucu.qr_var:
            return
        nokta = self._ekran(self.durum.sunucu.qr_enlem, self.durum.sunucu.qr_boylam)
        renk = QColor(stiller.QR)
        p.setPen(QPen(renk, 2))
        p.setBrush(QBrush(QColor(121, 184, 132, 70)))
        elmas = QPolygonF([
            nokta + QPointF(0, -9), nokta + QPointF(9, 0),
            nokta + QPointF(0, 9), nokta + QPointF(-9, 0),
        ])
        p.drawPolygon(elmas)
        p.setBrush(Qt.NoBrush)
        self._etiket(p, nokta + QPointF(11, 5), "QR (Kamikaze)", renk)

    def _ev(self, p: QPainter) -> None:
        if not self.durum or not self.durum.ev_var:
            return
        nokta = self._ekran(self.durum.ev_enlem, self.durum.ev_boylam)
        p.setPen(QPen(QColor(stiller.IYI), 2))
        p.setBrush(QBrush(QColor(22, 44, 30, 220)))
        p.drawEllipse(nokta, 9, 9)
        f = QFont(self.font())
        f.setBold(True)
        f.setPointSize(9)
        p.setFont(f)
        p.setPen(QPen(QColor(stiller.IYI), 1))
        p.drawText(QRectF(nokta.x() - 9, nokta.y() - 9, 18, 18), Qt.AlignCenter, "H")

    def _wp_ciz(self, p: QPainter) -> None:
        """Görev planı waypoint'leri — MP tarzı numaralı daireler + rota çizgisi."""
        if not self.wp_listesi:
            return
        sari = QColor(222, 196, 96)
        yesil = QColor(126, 194, 140)
        onceki = None
        if self.durum and self.durum.ev_var:
            onceki = self._ekran(self.durum.ev_enlem, self.durum.ev_boylam)
        p.setPen(QPen(sari, 2))
        noktalar = []
        for wp in self.wp_listesi:
            nokta = self._ekran(wp[0], wp[1])
            noktalar.append(nokta)
            if onceki is not None:
                p.drawLine(onceki, nokta)
            onceki = nokta
        f = QFont("Consolas", 9)
        f.setBold(True)
        p.setFont(f)
        for i, nokta in enumerate(noktalar):
            p.setPen(QPen(yesil, 2))
            p.setBrush(QBrush(QColor(20, 34, 24, 230)))
            p.drawEllipse(nokta, 10, 10)
            p.setPen(QPen(QColor(230, 240, 230), 1))
            p.drawText(QRectF(nokta.x() - 10, nokta.y() - 10, 20, 20), Qt.AlignCenter, str(i + 1))
            self._etiket(p, nokta + QPointF(12, 4), f"{self.wp_listesi[i][2]:.0f}m", yesil, 8)

    def _guided_hedef(self, p: QPainter) -> None:
        if not self.durum or not self.durum.hedef_var:
            return
        nokta = self._ekran(self.durum.hedef_enlem, self.durum.hedef_boylam)
        mor = QColor(186, 138, 230)
        iha = self.durum.iha
        if iha.enlem or iha.boylam:
            kendi = self._ekran(iha.enlem, iha.boylam)
            p.setPen(QPen(mor, 1.6, Qt.DashLine))
            p.drawLine(kendi, nokta)
        p.setPen(QPen(mor, 2))
        p.setBrush(QBrush(QColor(186, 138, 230, 70)))
        p.drawEllipse(nokta, 8, 8)
        p.drawLine(nokta + QPointF(-12, 0), nokta + QPointF(12, 0))
        p.drawLine(nokta + QPointF(0, -12), nokta + QPointF(0, 12))
        self._etiket(p, nokta + QPointF(12, -8), f"HEDEF {self.durum.hedef_irtifa:.0f} m", mor)

    def _ucak_sekli(self, p: QPainter, nokta: QPointF, yonelme: float, renk: QColor, boyut: float) -> None:
        p.save()
        p.translate(nokta)
        p.rotate(yonelme)
        yol = QPainterPath()
        yol.moveTo(0, -boyut)
        yol.lineTo(boyut * 0.85, boyut * 0.75)
        yol.lineTo(0, boyut * 0.32)
        yol.lineTo(-boyut * 0.85, boyut * 0.75)
        yol.closeSubpath()
        p.setPen(QPen(QColor(15, 20, 26), 1.6))
        p.setBrush(QBrush(renk))
        p.drawPath(yol)
        p.restore()

    def _rakipler(self, p: QPainter) -> None:
        if not self.durum:
            return
        for rakip in self.durum.rakipler.values():
            if not (rakip.enlem or rakip.boylam):
                continue
            taze = rakip.taze(4.0)
            nokta = self._ekran(rakip.enlem, rakip.boylam)
            renk = QColor(stiller.RAKIP) if taze else QColor(120, 120, 120)
            self._ucak_sekli(p, nokta, rakip.yonelme, renk, 9.0)
            self._etiket(p, nokta + QPointF(12, -4),
                         f"T{rakip.takim_numarasi}  {rakip.irtifa:.0f}m {rakip.hiz:.0f}m/s", renk)

    def _kendi(self, p: QPainter) -> None:
        if not self.durum:
            return
        iha = self.durum.iha
        if not (iha.enlem or iha.boylam):
            return
        if len(self.durum.iz) > 1:
            p.setPen(QPen(QColor(90, 167, 214, 110), 2))
            onceki = None
            for lat, lon in list(self.durum.iz)[-600:]:
                nokta = self._ekran(lat, lon)
                if onceki is not None:
                    p.drawLine(onceki, nokta)
                onceki = nokta
        nokta = self._ekran(iha.enlem, iha.boylam)
        taze = iha.taze(2.0)
        renk = QColor(stiller.VURGU) if taze else QColor(stiller.HATA)
        if taze and iha.hiz > 1.0:
            uzunluk_px = iha.hiz * 15.0 * self.olcek
            aci = math.radians(iha.yonelme)
            uc = nokta + QPointF(math.sin(aci) * uzunluk_px, -math.cos(aci) * uzunluk_px)
            p.setPen(QPen(QColor(90, 167, 214, 150), 1.6, Qt.DashLine))
            p.drawLine(nokta, uc)
        self._ucak_sekli(p, nokta, iha.yonelme, renk, 13.0)
        self._etiket(p, nokta + QPointF(16, -6), "BİZ" if taze else "BİZ (VERİ YOK)", renk)
        self._etiket(p, nokta + QPointF(16, 12), f"{iha.irtifa:.0f}m {iha.hiz:.0f}m/s", QColor(stiller.YAZI))

    def _ust_bilgi(self, p: QPainter) -> None:
        satirlar = []
        if self._imlec_konum is not None:
            lat, lon = self._cografi(self._imlec_konum.x(), self._imlec_konum.y())
            satirlar.append(f"İmleç: {lat:.6f}, {lon:.6f}")
        satirlar.append("Takip: AÇIK" if self.takip else "Takip: KAPALI (çift tık = aç)")
        if self.wp_listesi:
            satirlar.append(f"Görev planı: {len(self.wp_listesi)} WP (sağ tık = ekle/sil)")
        y = 18
        for satir in satirlar:
            self._etiket(p, QPointF(8, y), satir, QColor(stiller.YAZI_SOLUK))
            y += 17
        ok_x = self.width() - 26
        p.setPen(QPen(QColor(stiller.YAZI), 2))
        p.drawLine(ok_x, 34, ok_x, 12)
        p.drawLine(ok_x, 12, ok_x - 5, 22)
        p.drawLine(ok_x, 12, ok_x + 5, 22)
        p.drawText(ok_x - 5, 48, "K")

    def _olcek_cubugu(self, p: QPainter) -> None:
        hedef_px = 120.0
        m = hedef_px / self.olcek
        for aday in (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000):
            if aday >= m:
                m = aday
                break
        px = m * self.olcek
        x0, y0 = 10, self.height() - 14
        p.setPen(QPen(QColor(stiller.YAZI), 2))
        p.drawLine(x0, y0, x0 + px, y0)
        p.drawLine(x0, y0 - 4, x0, y0 + 4)
        p.drawLine(x0 + px, y0 - 4, x0 + px, y0 + 4)
        self._etiket(p, QPointF(x0 + px / 2 - 20, y0 - 8), f"{m:g} m", QColor(stiller.YAZI), 8)
