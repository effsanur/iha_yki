"""Mission Planner tarzı HUD.

Tam genişlik yapay ufuk + dikilme merdiveni, üstte yönelme şeridi,
solda hava hızı bandı, sağda irtifa bandı, altta mod/ARM/batarya/GPS
kaplamaları. Renk dili Mission Planner'a yakın tutuldu (yeşil metin,
mavi gök / yeşil-kahve yer, siyah yarı saydam bantlar).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

HUD_YESIL = QColor(80, 255, 80)
HUD_BEYAZ = QColor(255, 255, 255)
BANT_ARKA = QColor(0, 0, 0, 110)
KUTU_ARKA = QColor(0, 0, 0, 190)


class HudWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.iha = None
        self.setMinimumSize(300, 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def guncelle(self, iha) -> None:
        self.iha = iha
        self.update()

    # ---------------------------------------------------------------
    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        g, y = self.width(), self.height()
        if self.iha is None:
            p.fillRect(self.rect(), QColor(10, 12, 16))
            p.setPen(QPen(HUD_YESIL))
            p.drawText(self.rect(), Qt.AlignCenter, "Telemetri bekleniyor...")
            p.end()
            return
        iha = self.iha
        cx, cy = g / 2.0, y / 2.0

        # ---------- yapay ufuk ----------
        p.save()
        p.translate(cx, cy)
        p.rotate(-iha.yatis)
        piksel_per_derece = y / 45.0
        kayma = iha.dikilme * piksel_per_derece
        buyuk = max(g, y) * 2.2

        gok = QLinearGradient(0, -buyuk, 0, kayma)
        gok.setColorAt(0.0, QColor(6, 48, 108))
        gok.setColorAt(1.0, QColor(105, 165, 216))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(gok))
        p.drawRect(QRectF(-buyuk, -buyuk, 2 * buyuk, buyuk + kayma))

        yer = QLinearGradient(0, kayma, 0, buyuk)
        yer.setColorAt(0.0, QColor(146, 123, 51))
        yer.setColorAt(1.0, QColor(66, 52, 17))
        p.setBrush(QBrush(yer))
        p.drawRect(QRectF(-buyuk, kayma, 2 * buyuk, buyuk - kayma + buyuk))

        p.setPen(QPen(HUD_BEYAZ, 2))
        p.drawLine(QPointF(-buyuk, kayma), QPointF(buyuk, kayma))

        # dikilme merdiveni
        f = QFont("Consolas", 9)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QPen(HUD_BEYAZ, 1.6))
        for derece in range(-40, 41, 5):
            if derece == 0:
                continue
            yy = kayma - derece * piksel_per_derece
            if abs(yy) > y * 0.42:
                continue
            yarim = 46 if derece % 10 == 0 else 22
            p.drawLine(QPointF(-yarim, yy), QPointF(yarim, yy))
            if derece % 10 == 0:
                p.drawText(QRectF(yarim + 4, yy - 8, 34, 16), Qt.AlignVCenter | Qt.AlignLeft, f"{abs(derece)}")
                p.drawText(QRectF(-yarim - 38, yy - 8, 34, 16), Qt.AlignVCenter | Qt.AlignRight, f"{abs(derece)}")
        p.restore()

        # ---------- yatış yayı ----------
        yay_r = y * 0.36
        p.setPen(QPen(HUD_BEYAZ, 1.6))
        for aci in (-60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60):
            p.save()
            p.translate(cx, cy)
            p.rotate(aci)
            boy = 12 if aci % 30 == 0 else 7
            p.drawLine(QPointF(0, -yay_r), QPointF(0, -yay_r - boy))
            p.restore()
        p.save()
        p.translate(cx, cy)
        p.rotate(-iha.yatis)
        p.setBrush(QBrush(HUD_YESIL))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(0, -yay_r + 2), QPointF(-6, -yay_r + 13), QPointF(6, -yay_r + 13)]))
        p.restore()

        # ---------- merkez işareti (MP tarzı kanatlar) ----------
        p.setPen(QPen(QColor(255, 60, 60), 3))
        p.drawLine(QPointF(cx - 52, cy), QPointF(cx - 18, cy))
        p.drawLine(QPointF(cx + 18, cy), QPointF(cx + 52, cy))
        p.drawLine(QPointF(cx - 18, cy), QPointF(cx - 10, cy + 8))
        p.drawLine(QPointF(cx + 18, cy), QPointF(cx + 10, cy + 8))
        p.setBrush(QBrush(QColor(255, 60, 60)))
        p.drawEllipse(QPointF(cx, cy), 2.4, 2.4)

        # ---------- yönelme şeridi (üst) ----------
        serit_y = 0
        serit_h = 26
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(BANT_ARKA))
        p.drawRect(QRectF(0, serit_y, g, serit_h))
        p.setClipRect(QRectF(0, serit_y, g, serit_h + 6))
        piksel_per_derece_yon = g / 100.0
        f2 = QFont("Consolas", 9)
        f2.setBold(True)
        p.setFont(f2)
        yon_adlari = {0: "K", 45: "KD", 90: "D", 135: "GD", 180: "G", 225: "GB", 270: "B", 315: "KB"}
        bas = int(iha.yonelme) - 55
        for derece in range(bas, bas + 111):
            if derece % 5 != 0:
                continue
            gercek = derece % 360
            x = cx + (derece - iha.yonelme) * piksel_per_derece_yon
            p.setPen(QPen(HUD_BEYAZ, 1.4))
            tik = 8 if derece % 15 == 0 else 4
            p.drawLine(QPointF(x, serit_h - tik), QPointF(x, serit_h))
            if derece % 15 == 0:
                etiket = yon_adlari.get(gercek, f"{gercek:03d}")
                p.drawText(QRectF(x - 16, serit_y + 1, 32, 13), Qt.AlignCenter, etiket)
        p.setClipping(False)
        # yönelme kutusu
        p.setBrush(QBrush(KUTU_ARKA))
        p.setPen(QPen(HUD_BEYAZ, 1.4))
        yon_kutu = QRectF(cx - 32, serit_h + 2, 64, 20)
        p.drawRect(yon_kutu)
        p.setPen(QPen(HUD_YESIL, 1))
        f3 = QFont("Consolas", 11)
        f3.setBold(True)
        p.setFont(f3)
        p.drawText(yon_kutu, Qt.AlignCenter, f"{iha.yonelme:03.0f}°")
        p.setBrush(QBrush(HUD_BEYAZ))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(cx, serit_h), QPointF(cx - 5, serit_h - 8), QPointF(cx + 5, serit_h - 8)]))

        # ---------- hız bandı (sol) — MP gibi hava hızı esas ----------
        hiz_deger = iha.hava_hizi if iha.hava_hizi > 0.1 else iha.hiz
        self._bant(p, x0=0, geniş=58, merkez_deger=hiz_deger, adim=1, etiket_adim=5,
                   pencere=30.0, saga_hizali=False, birim="m/s", deger_kalip="{:.1f}")
        # ---------- irtifa bandı (sağ) ----------
        self._bant(p, x0=g - 58, geniş=58, merkez_deger=iha.irtifa, adim=5, etiket_adim=10,
                   pencere=120.0, saga_hizali=True, birim="m", deger_kalip="{:.0f}")

        # ---------- alt kaplamalar ----------
        f4 = QFont("Consolas", 10)
        f4.setBold(True)
        p.setFont(f4)
        p.setPen(QPen(HUD_YESIL, 1))
        p.drawText(QPointF(64, y - 24), f"YH {iha.hiz:4.1f} m/s")
        p.drawText(QPointF(64, y - 8), f"HH {iha.hava_hizi:4.1f} m/s")
        fix = {0: "GPS YOK", 1: "GPS YOK", 2: "GPS 2D", 3: "GPS 3D", 4: "DGPS", 5: "RTK F", 6: "RTK"}.get(iha.gps_fix, "GPS ?")
        p.drawText(QRectF(g - 210, y - 34, 148, 14), Qt.AlignRight | Qt.AlignVCenter, f"Bat %{iha.batarya:.0f}")
        p.drawText(QRectF(g - 210, y - 18, 148, 14), Qt.AlignRight | Qt.AlignVCenter, f"{fix} ({iha.gps_uydu})")

        # mod adı — MP'deki gibi büyük yeşil, alt orta (alt bilgilerin üstünde)
        f5 = QFont("Arial", 15)
        f5.setBold(True)
        p.setFont(f5)
        p.setPen(QPen(HUD_YESIL, 1))
        p.drawText(QRectF(0, y - 62, g, 24), Qt.AlignCenter, iha.mod)

        # DISARMED / veri yok kaplaması
        if not iha.taze(2.0):
            p.fillRect(self.rect(), QColor(0, 0, 0, 130))
            p.setPen(QPen(QColor(255, 80, 80), 1))
            f6 = QFont("Arial", 17)
            f6.setBold(True)
            p.setFont(f6)
            p.drawText(self.rect(), Qt.AlignCenter, "VERİ YOK")
        elif not iha.armed:
            p.setPen(QPen(QColor(255, 70, 70), 1))
            f6 = QFont("Arial", 17)
            f6.setBold(True)
            p.setFont(f6)
            p.drawText(QRectF(0, cy - 52, g, 30), Qt.AlignCenter, "DISARMED")
        p.end()

    # ---------------------------------------------------------------
    def _bant(self, p: QPainter, x0: float, geniş: float, merkez_deger: float,
              adim: int, etiket_adim: int, pencere: float, saga_hizali: bool,
              birim: str, deger_kalip: str) -> None:
        """MP tarzı kayan değer bandı (solda hız, sağda irtifa)."""
        y = self.height()
        ust, alt = 52.0, y - 40.0   # üstte yönelme şeridi + kutusuna yer bırak
        h = alt - ust
        cy = (ust + alt) / 2.0
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(BANT_ARKA))
        p.drawRect(QRectF(x0, ust, geniş, h))
        p.setClipRect(QRectF(x0, ust, geniş, h))
        piksel_per_birim = h / pencere
        f = QFont("Consolas", 9)
        f.setBold(True)
        p.setFont(f)
        ilk = int((merkez_deger - pencere / 2.0) // adim) * adim
        deger = ilk
        while deger <= merkez_deger + pencere / 2.0 + adim:
            yy = cy - (deger - merkez_deger) * piksel_per_birim
            if ust <= yy <= alt and deger >= 0:
                p.setPen(QPen(HUD_BEYAZ, 1.3))
                if saga_hizali:
                    p.drawLine(QPointF(x0, yy), QPointF(x0 + 7, yy))
                    if deger % etiket_adim == 0:
                        p.drawText(QRectF(x0 + 9, yy - 8, geniş - 11, 16), Qt.AlignVCenter | Qt.AlignLeft, f"{deger:g}")
                else:
                    p.drawLine(QPointF(x0 + geniş - 7, yy), QPointF(x0 + geniş, yy))
                    if deger % etiket_adim == 0:
                        p.drawText(QRectF(x0 + 2, yy - 8, geniş - 11, 16), Qt.AlignVCenter | Qt.AlignRight, f"{deger:g}")
            deger += adim
        p.setClipping(False)

        # geçerli değer kutusu (ok uçlu)
        kutu_h = 26.0
        yol = QPainterPath()
        if saga_hizali:
            yol.moveTo(x0, cy)
            yol.lineTo(x0 + 10, cy - kutu_h / 2)
            yol.lineTo(x0 + geniş, cy - kutu_h / 2)
            yol.lineTo(x0 + geniş, cy + kutu_h / 2)
            yol.lineTo(x0 + 10, cy + kutu_h / 2)
        else:
            yol.moveTo(x0 + geniş, cy)
            yol.lineTo(x0 + geniş - 10, cy - kutu_h / 2)
            yol.lineTo(x0, cy - kutu_h / 2)
            yol.lineTo(x0, cy + kutu_h / 2)
            yol.lineTo(x0 + geniş - 10, cy + kutu_h / 2)
        yol.closeSubpath()
        p.setBrush(QBrush(KUTU_ARKA))
        p.setPen(QPen(HUD_BEYAZ, 1.4))
        p.drawPath(yol)
        p.setPen(QPen(HUD_YESIL, 1))
        f2 = QFont("Consolas", 12)
        f2.setBold(True)
        p.setFont(f2)
        p.drawText(QRectF(x0, cy - kutu_h / 2, geniş, kutu_h), Qt.AlignCenter, deger_kalip.format(merkez_deger))
        p.setPen(QPen(HUD_BEYAZ, 1))
        f3 = QFont("Consolas", 8)
        p.setFont(f3)
        p.drawText(QRectF(x0, ust - 14, geniş, 12), Qt.AlignCenter, birim)
