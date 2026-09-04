#!/usr/bin/env python3
"""Jetson/RPi görüntü işleme tarafı için örnek YKİ besleyici.

Görüntü işleme döngünüzden (OpenCV/YOLO vb.) her karede bu modüldeki
YkiBildirici'yi çağırın; YKİ'deki kilitlenme paneli, telemetri paketindeki
hedef_* alanları ve 4 sn kilit sayacı otomatik beslenir. QR okununca da
qr_bildir() çağrısı kamikaze panelini doldurur (otomatik gönderim açıksa
paket kendiliğinden gider).

Bu dosya uçuş bilgisayarında çalışır; YKİ bilgisayarının IP'sini verin.
"""

from __future__ import annotations

import json
import socket
import time


class YkiBildirici:
    def __init__(self, yki_ip: str, port: int = 42000):
        self.adres = (yki_ip, port)
        self.soket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def kilit_bildir(self, kilit_var: bool, merkez_x: int = 0, merkez_y: int = 0,
                     genislik: int = 0, yukseklik: int = 0,
                     video_genislik: int = 640, video_yukseklik: int = 480) -> None:
        """Her karede çağırın (kilit yokken de kilit_var=False ile çağırın)."""
        paket = {
            "kilitlenme": 1 if kilit_var else 0,
            "merkez_x": int(merkez_x),
            "merkez_y": int(merkez_y),
            "genislik": int(genislik),
            "yukseklik": int(yukseklik),
            "video_genislik": int(video_genislik),
            "video_yukseklik": int(video_yukseklik),
        }
        self.soket.sendto(json.dumps(paket).encode("utf-8"), self.adres)

    def qr_bildir(self, metin: str) -> None:
        """Kamikaze dalışında QR çözülünce bir kez çağırın."""
        self.soket.sendto(json.dumps({"qr_metni": metin}).encode("utf-8"), self.adres)


if __name__ == "__main__":
    # ---- deneme: sahte bir hedefi 6 sn kilitleyip QR gönderir ----
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--yki-ip", default="127.0.0.1", help="YKİ bilgisayarının IP adresi")
    p.add_argument("--port", type=int, default=42000)
    args = p.parse_args()

    bildirici = YkiBildirici(args.yki_ip, args.port)
    print(f"YKİ'ye gönderiliyor: {args.yki_ip}:{args.port}")
    print("6 sn kilit benzetimi...")
    t0 = time.time()
    while time.time() - t0 < 6.0:
        bildirici.kilit_bildir(True, merkez_x=320, merkez_y=236, genislik=72, yukseklik=48)
        time.sleep(0.05)  # ~20 Hz (görüntü işleme kare hızınız)
    bildirici.kilit_bildir(False)
    print("Kilit bırakıldı (YKİ 4 sn'yi doldurduğu için otomatik paket göndermeli)")
    time.sleep(1.0)
    bildirici.qr_bildir("teknofest2026")
    print("QR metni gönderildi")

    # ---- gerçek kullanım iskeleti ----
    # bildirici = YkiBildirici("192.168.1.10")           # YKİ IP
    # while True:
    #     kare = kamera.oku()
    #     tespit = takipci.isle(kare)                    # sizin algoritmanız
    #     if tespit is not None:
    #         bildirici.kilit_bildir(True, tespit.cx, tespit.cy, tespit.w, tespit.h,
    #                                kare.shape[1], kare.shape[0])
    #     else:
    #         bildirici.kilit_bildir(False)
    #     if qr_cozuldu:                                  # kamikaze dalışında
    #         bildirici.qr_bildir(qr_metni)
