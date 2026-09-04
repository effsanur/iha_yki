#!/usr/bin/env python3
"""YKİ hızlı sağlık testi — arayüz açmadan, sahte sunucuya karşı uçtan uca dener.

Kullanım: python3 selftest.py
Çıkış kodu 0 = tüm zorunlu adımlar geçti.
"""

from __future__ import annotations

import importlib
import sys
import threading
import time

# konsol kodlaması ne olursa olsun (Windows cp125x dahil) çökmesin
for _akis in (sys.stdout, sys.stderr):
    try:
        _akis.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SONUCLAR: list[tuple[str, bool, str]] = []


def kaydet(ad: str, tamam: bool, detay: str = "") -> None:
    SONUCLAR.append((ad, tamam, detay))
    isaret = "✔" if tamam else "✘"
    print(f"  {isaret} {ad}" + (f" — {detay}" if detay else ""))


def modul_kontrol() -> bool:
    print("\n[1] Modül kontrolü")
    zorunlu_tamam = True
    if sys.version_info < (3, 8):
        kaydet("Python sürümü", False, f"{sys.version.split()[0]} (>=3.8 gerekli)")
        zorunlu_tamam = False
    else:
        kaydet("Python sürümü", True, sys.version.split()[0])
    for ad, zorunlu, ipucu in [
        ("requests", True, "pip install requests"),
        ("yaml", True, "pip install pyyaml"),
        ("PySide6", False, "pip install PySide6  (arayüz için zorunlu)"),
        ("pymavlink", False, "pip install pymavlink  (MAVLink kaynağı için)"),
        ("roslibpy", False, "pip install roslibpy  (rosbridge kaynağı için)"),
        ("cv2", False, "pip install opencv-python  (video paneli için)"),
    ]:
        try:
            importlib.import_module(ad)
            kaydet(ad, True)
        except ImportError:
            kaydet(ad, not zorunlu, f"YOK — {ipucu}")
            if zorunlu:
                zorunlu_tamam = False
    return zorunlu_tamam


def protokol_testi() -> None:
    print("\n[2] Sahte sunucuya karşı protokol testi")
    import requests

    import sim_sunucu
    from yki.ayarlar import Ayarlar
    from yki.durum import DurumModeli, mono
    from yki.gorev_alici import KilitTakip
    from yki.sunucu_istemci import SunucuIstemci, SunucuSaati

    args = sim_sunucu.argumanlar(["--host", "127.0.0.1", "--port", "0", "--hss-baslangic", "0", "--hss-sure", "9999"])
    sunucu = sim_sunucu.sunucu_kur(args)
    port = sunucu.server_address[1]
    threading.Thread(target=sunucu.serve_forever, daemon=True).start()
    kaydet("Sahte sunucu", True, f"127.0.0.1:{port}")

    ayarlar = Ayarlar(None)
    ayarlar.veri["sunucu"]["url"] = f"http://127.0.0.1:{port}"
    ayarlar.veri["sunucu"]["telemetri_hz"] = 2.0
    durum = DurumModeli()
    saat = SunucuSaati()
    istemci = SunucuIstemci(ayarlar, durum, saat)

    # sahte taze telemetri beslemesi (FCU yerine)
    besleme_dur = threading.Event()

    def besle():
        while not besleme_dur.is_set():
            durum.iha_guncelle(
                kaynak="TEST", enlem=40.2306, boylam=29.0060, irtifa=95.0,
                dikilme=4.0, yatis=-9.0, yonelme=180.0, hiz=21.0, batarya=88.0,
                mod="AUTO", otonom=True, armed=True, gps_fix=3, gps_uydu=12,
                gps_utc_ms=43_200_000, gps_utc_kaynak_mono=mono(),
            )
            time.sleep(0.2)

    threading.Thread(target=besle, daemon=True).start()
    istemci.start()

    istemci.giris_yap()
    time.sleep(1.0)
    kaydet("Sunucuya giriş (POST /api/giris)", durum.sunucu.giris_ok, f"takım={durum.sunucu.takim_numarasi}")
    kaydet("Saat senkronu (GET /api/sunucusaati)", saat.senkron, f"gecikme={saat.gecikme_ms:.0f} ms")

    istemci.telemetri_baslat(True)
    time.sleep(3.2)
    with durum.kilitli():
        gonderilen = durum.sunucu.gonderilen_telemetri
        hata = durum.sunucu.telemetri_hata
        rakip_sayisi = len(durum.rakipler)
    kaydet("Telemetri gönderimi (2 Hz)", gonderilen >= 4 and hata == 0, f"gönderilen={gonderilen}, hata={hata}")
    kaydet("Rakip konumları alındı (konumBilgileri)", rakip_sayisi >= 3, f"{rakip_sayisi} rakip")

    istemci.kilitlenme_gonder(saat.simdi_ms(), True)
    istemci.kamikaze_gonder(saat.simdi_ms() - 15000, saat.simdi_ms(), "teknofest2026")
    istemci.qr_sorgula()
    istemci.hss_sorgula()
    time.sleep(1.5)
    with durum.kilitli():
        kaydet("Kilitlenme paketi (POST /api/kilitlenme_bilgisi)", durum.sunucu.kilit_paket_sayisi == 1)
        kaydet("Kamikaze paketi (POST /api/kamikaze_bilgisi)", durum.sunucu.kamikaze_paket_sayisi == 1)
        kaydet("QR koordinatı (GET /api/qr_koordinati)", durum.sunucu.qr_var,
               f"{durum.sunucu.qr_enlem:.6f},{durum.sunucu.qr_boylam:.6f}")
        kaydet("HSS koordinatları (GET /api/hss_koordinatlari)", len(durum.hss_listesi) == 3,
               f"{len(durum.hss_listesi)} bölge")

    istemci.telemetri_baslat(False)
    istemci.durdur()

    # hız limiti: peş peşe 3 hızlı paket -> 400 + "3"
    oturum = requests.Session()
    oturum.post(f"http://127.0.0.1:{port}/api/giris", json={"kadi": args.kadi, "sifre": args.sifre}, timeout=2)
    paket = istemci._telemetri_paketi()  # taze beslemeden geçerli paket
    time.sleep(1.1)                      # istemcinin son gönderimlerinin penceresi dolsun
    kodlar, govdeler = [], []
    for _ in range(3):
        c = oturum.post(f"http://127.0.0.1:{port}/api/telemetri_gonder", json=paket, timeout=2)
        kodlar.append(c.status_code)
        govdeler.append(c.text.strip())
    kaydet("Hız aşımı koruması (2 Hz üstü -> 400/3)",
           kodlar[-1] == 400 and govdeler[-1] == "3", f"kodlar={kodlar}")

    # istemci alan sınırlama: aşırı değerler pakete sınırlandırılarak yazılmalı
    besleme_dur.set()
    time.sleep(0.3)
    durum.iha_guncelle(dikilme=140.0, yatis=-140.0, yonelme=400.0, batarya=140.0)
    p = istemci._telemetri_paketi()
    kaydet("Paket alan sınırlama (dikilme/yatis/yonelme/batarya)",
           p is not None and p["iha_dikilme"] == 90.0 and p["iha_yatis"] == -90.0
           and 0.0 <= p["iha_yonelme"] < 360.0 and p["iha_batarya"] == 100)

    sunucu.shutdown()

    # kilit takibi mantığı (0.5 sn eşikli hızlı test)
    ayarlar.veri["gorev"]["kilitlenme_esigi_sn"] = 0.5
    ayarlar.veri["gorev"]["kilit_bosluk_toleransi_sn"] = 0.1
    takip = KilitTakip(ayarlar)
    olay = None
    bitis = time.monotonic() + 0.7
    while time.monotonic() < bitis:
        olay = takip.guncelle(True, 1000) or olay
        time.sleep(0.03)
    bitis = time.monotonic() + 0.3
    while time.monotonic() < bitis:
        olay = takip.guncelle(False, 2000) or olay
        time.sleep(0.03)
    kaydet("Kilit oturumu takibi (4 sn kuralı mantığı)", olay is not None and olay[0] == "tamam")


def main() -> int:
    print("=" * 62)
    print(" Savaşan İHA YKİ — kendi kendine test")
    print("=" * 62)
    zorunlu_ok = modul_kontrol()
    if zorunlu_ok:
        try:
            protokol_testi()
        except Exception as e:  # noqa: BLE001
            kaydet("Protokol testi", False, f"beklenmeyen hata: {e!r}")
    else:
        print("\nZorunlu modüller eksik — protokol testi atlandı.")
    basarisiz = [s for s in SONUCLAR if not s[1]]
    print("\n" + "=" * 62)
    if basarisiz:
        print(f"SONUÇ: {len(basarisiz)} adım BAŞARISIZ / toplam {len(SONUCLAR)}")
        for ad, _, detay in basarisiz:
            print(f"  ✘ {ad}" + (f" — {detay}" if detay else ""))
        return 1
    print(f"SONUÇ: tüm adımlar geçti ({len(SONUCLAR)}/{len(SONUCLAR)}) ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
