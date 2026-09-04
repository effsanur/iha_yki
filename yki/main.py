"""YKİ giriş noktası.

Kullanım (Ubuntu 22.04):
    python3 -m yki.main                 # config.yaml ile
    python3 -m yki.main --sim           # donanımsız benzetim kipi
    python3 -m yki.main --kaynak mavlink
    python3 -m yki.main --config baska.yaml
"""

from __future__ import annotations

import argparse
import os
import signal
import sys


def _proje_koku() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    ayristirici = argparse.ArgumentParser(description="TEKNOFEST 2026 Savaşan İHA Yer Kontrol İstasyonu")
    ayristirici.add_argument("--config", default=os.path.join(_proje_koku(), "config.yaml"),
                             help="config.yaml yolu")
    ayristirici.add_argument("--kaynak", choices=["otomatik", "mavlink", "rosbridge", "sim"],
                             help="telemetri kaynağını config yerine zorla")
    ayristirici.add_argument("--sim", action="store_true", help="--kaynak sim kısayolu")
    argumanlar = ayristirici.parse_args()

    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("HATA: PySide6 kurulu değil. Kurulum: pip install PySide6", file=sys.stderr)
        return 1

    from .ayarlar import Ayarlar
    from .durum import DurumModeli
    from .gorev_alici import GorevAlici
    from .sunucu_istemci import SunucuIstemci, SunucuSaati
    from .telemetri_kaynagi import kaynaklari_olustur
    from .arayuz import stiller
    from .arayuz.ana_pencere import AnaPencere, UygBaglam

    ayarlar = Ayarlar(argumanlar.config)
    if argumanlar.sim:
        ayarlar.veri["baglanti"]["kaynak"] = "sim"
    elif argumanlar.kaynak:
        ayarlar.veri["baglanti"]["kaynak"] = argumanlar.kaynak

    durum = DurumModeli()
    saat = SunucuSaati()
    istemci = SunucuIstemci(ayarlar, durum, saat)
    kaynaklar = kaynaklari_olustur(ayarlar, durum)

    uygulama = QApplication(sys.argv)
    uygulama.setStyle("Fusion")
    uygulama.setStyleSheet(stiller.STIL)
    uygulama.setApplicationName("Savaşan İHA YKİ")

    baglam = UygBaglam(ayarlar=ayarlar, durum=durum, saat=saat, istemci=istemci, kaynaklar=kaynaklar)
    pencere = AnaPencere(baglam)

    # görüntü işleme (Jetson) -> kilit/QR alıcısı
    alici = GorevAlici(ayarlar, durum, qr_geri_cagri=pencere.qr_kuyruk.append)

    istemci.start()
    for kaynak in kaynaklar:
        kaynak.start()
    alici.start()

    durum.olay("BILGI", f"YKİ başladı — kaynak: {ayarlar.al('baglanti', 'kaynak')}, config: {argumanlar.config}")
    durum.olay("BILGI", "Yarışma günü: güvenlik duvarını kapatmayı unutmayın (sudo ufw disable)")

    signal.signal(signal.SIGINT, signal.SIG_DFL)  # Ctrl+C ile kapanabilsin
    pencere.show()
    sonuc = uygulama.exec()

    istemci.durdur()
    alici.durdur()
    for kaynak in kaynaklar:
        kaynak.durdur()
    return sonuc


if __name__ == "__main__":
    sys.exit(main())
