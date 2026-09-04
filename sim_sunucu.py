#!/usr/bin/env python3
"""Yarışma sunucusu benzetimi (test/prova için).

Haberleşme Dokümanı 2026'daki API'yi taklit eder — YKİ'yi sahaya çıkmadan
uçtan uca denemek içindir. Yalnızca Python standart kütüphanesi kullanır.

Kullanım:
    python3 sim_sunucu.py                     # 0.0.0.0:5000
    python3 sim_sunucu.py --port 5000 --kadi takimkadi --sifre takimsifresi
    python3 sim_sunucu.py --hss-baslangic 30 --hss-sure 90

Davranış:
  - /api/giris     : kadi/sifre doğruysa çerez verir, gövde = takım numarası
  - /api/telemetri_gonder : oturum ister; 1 sn içinde 2'den fazla pakette
                     400 + "3" döndürür (hız aşımı); cevapta 3 sahte rakip
  - /api/hss_koordinatlari : başlangıçta boş; --hss-baslangic sn sonra
                     --hss-sure sn boyunca 3 bölge aktif olur (döngüsel)
  - /api/qr_koordinati, /api/kilitlenme_bilgisi, /api/kamikaze_bilgisi
"""

from __future__ import annotations

import argparse
import json
import math
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KILIT = threading.Lock()


def sunucu_saati() -> dict:
    simdi = datetime.now()
    return {
        "gun": simdi.day,
        "saat": simdi.hour,
        "dakika": simdi.minute,
        "saniye": simdi.second,
        "milisaniye": simdi.microsecond // 1000,
    }


class Durum:
    def __init__(self, args):
        self.args = args
        self.oturumlar: set[str] = set()
        self.telemetri_zamanlari: dict[int, list] = {}
        self.son_telemetri: dict[int, dict] = {}
        self.baslangic = time.monotonic()
        self.sayac = 0

    def rakipler(self) -> list[dict]:
        """Merkez etrafında dönen 3 sahte rakip üretir."""
        t = time.monotonic() - self.baslangic
        lat0, lon0 = self.args.merkez
        liste = []
        for i, (yaricap, hiz, faz, irtifa) in enumerate(
            [(220.0, 18.0, 0.0, 55.0), (300.0, 22.0, 2.1, 80.0), (170.0, 25.0, 4.2, 110.0)]
        ):
            aci = (hiz / yaricap) * t + faz
            lat = lat0 + (yaricap * math.cos(aci)) / 111319.49
            lon = lon0 + (yaricap * math.sin(aci)) / (111319.49 * math.cos(math.radians(lat0)))
            liste.append({
                "takim_numarasi": 10 + i,
                "iha_enlem": round(lat, 7),
                "iha_boylam": round(lon, 7),
                "iha_irtifa": irtifa + 8.0 * math.sin(t / 6.0 + i),
                "iha_dikilme": round(5.0 * math.sin(t / 4.0 + i), 1),
                "iha_yonelme": round((math.degrees(aci) + 90.0) % 360.0, 1),
                "iha_yatis": round(15.0 * math.sin(t / 5.0 + i), 1),
                "iha_hizi": round(hiz, 1),
                "zaman_farki": 120 + i * 60,
            })
        # gerçek takımların son paketlerini de yansıt
        for takim_no, paket in self.son_telemetri.items():
            liste.append({
                "takim_numarasi": takim_no,
                "iha_enlem": paket.get("iha_enlem", 0.0),
                "iha_boylam": paket.get("iha_boylam", 0.0),
                "iha_irtifa": paket.get("iha_irtifa", 0.0),
                "iha_dikilme": paket.get("iha_dikilme", 0.0),
                "iha_yonelme": paket.get("iha_yonelme", 0.0),
                "iha_yatis": paket.get("iha_yatis", 0.0),
                "iha_hizi": paket.get("iha_hiz", 0.0),
                "zaman_farki": 50,
            })
        return liste

    def hss(self) -> list[dict]:
        gecen = time.monotonic() - self.baslangic
        dongu = self.args.hss_baslangic + self.args.hss_sure
        if dongu <= 0 or (gecen % dongu) < self.args.hss_baslangic:
            return []
        lat0, lon0 = self.args.merkez
        d = 1.0 / 111319.49
        return [
            {"id": 0, "hssEnlem": round(lat0 + 250 * d, 8), "hssBoylam": round(lon0 + 180 * d / math.cos(math.radians(lat0)), 8), "hssYaricap": 50},
            {"id": 1, "hssEnlem": round(lat0 - 200 * d, 8), "hssBoylam": round(lon0 - 220 * d / math.cos(math.radians(lat0)), 8), "hssYaricap": 75},
            {"id": 2, "hssEnlem": round(lat0 + 30 * d, 8), "hssBoylam": round(lon0 - 300 * d / math.cos(math.radians(lat0)), 8), "hssYaricap": 150},
        ]


DURUM: Durum | None = None

TELEMETRI_ALANLARI = [
    "takim_numarasi", "iha_enlem", "iha_boylam", "iha_irtifa", "iha_dikilme",
    "iha_yonelme", "iha_yatis", "iha_hiz", "iha_batarya", "iha_otonom",
    "iha_kilitlenme", "hedef_merkez_X", "hedef_merkez_Y", "hedef_genislik",
    "hedef_yukseklik", "gps_saati",
]


class Istekci(BaseHTTPRequestHandler):
    server_version = "SavasanIhaSim/1.0"

    # ---- yardımcılar ----
    def _cevap(self, kod: int, govde=None, tip="application/json") -> None:
        veri = b""
        if govde is not None:
            veri = (json.dumps(govde, ensure_ascii=False) if not isinstance(govde, str) else govde).encode("utf-8")
        self.send_response(kod)
        self.send_header("Content-Type", tip + "; charset=utf-8")
        self.send_header("Content-Length", str(len(veri)))
        if getattr(self, "_yeni_cerez", None):
            self.send_header("Set-Cookie", f"oturum={self._yeni_cerez}; Path=/")
        self.end_headers()
        self.wfile.write(veri)

    def _govde(self):
        boy = int(self.headers.get("Content-Length", 0) or 0)
        if boy <= 0:
            return None
        try:
            return json.loads(self.rfile.read(boy).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def _oturum_var(self) -> bool:
        cerez = self.headers.get("Cookie", "")
        with KILIT:
            return any(
                parca.strip().startswith("oturum=") and parca.strip()[7:] in DURUM.oturumlar
                for parca in cerez.split(";")
            )

    def log_message(self, kalip, *args):  # noqa: N802
        print(f"[sim] {self.address_string()} {kalip % args}")

    # ---- GET ----
    def do_GET(self):  # noqa: N802
        self._yeni_cerez = None
        if self.path == "/api/sunucusaati":
            self._cevap(200, sunucu_saati())
        elif self.path == "/api/qr_koordinati":
            if not self._oturum_var():
                self._cevap(401, "Kimliksiz erişim")
                return
            self._cevap(200, {"qrEnlem": DURUM.args.qr[0], "qrBoylam": DURUM.args.qr[1]})
        elif self.path == "/api/hss_koordinatlari":
            if not self._oturum_var():
                self._cevap(401, "Kimliksiz erişim")
                return
            with KILIT:
                liste = DURUM.hss()
            self._cevap(200, {"sunucusaati": sunucu_saati(), "hss_koordinat_bilgileri": liste})
        elif self.path == "/":
            with KILIT:
                bilgi = {
                    "sunucu": "Savaşan İHA sim",
                    "oturum_sayisi": len(DURUM.oturumlar),
                    "hss_aktif": bool(DURUM.hss()),
                    "telemetri_paketi": DURUM.sayac,
                }
            self._cevap(200, bilgi)
        else:
            self._cevap(404, "Geçersiz URL")

    # ---- POST ----
    def do_POST(self):  # noqa: N802
        self._yeni_cerez = None
        govde = self._govde()
        if self.path == "/api/giris":
            if not isinstance(govde, dict) or "kadi" not in govde or "sifre" not in govde:
                self._cevap(204)
                return
            if govde["kadi"] == DURUM.args.kadi and govde["sifre"] == DURUM.args.sifre:
                jeton = f"j{time.time_ns()}"
                with KILIT:
                    DURUM.oturumlar.add(jeton)
                self._yeni_cerez = jeton
                self._cevap(200, str(DURUM.args.takim))
            else:
                self._cevap(400, "Kullanıcı adı veya şifre hatalı")
        elif self.path == "/api/telemetri_gonder":
            if not self._oturum_var():
                self._cevap(401, "Kimliksiz erişim")
                return
            if not isinstance(govde, dict) or any(alan not in govde for alan in TELEMETRI_ALANLARI):
                self._cevap(204)
                return
            hatali = (
                not (-90.0 <= float(govde["iha_dikilme"]) <= 90.0)
                or not (-90.0 <= float(govde["iha_yatis"]) <= 90.0)
                or not (0.0 <= float(govde["iha_yonelme"]) <= 360.0)
                or int(govde["iha_otonom"]) not in (0, 1)
                or int(govde["iha_kilitlenme"]) not in (0, 1)
            )
            if hatali:
                self._cevap(400, "1")
                return
            takim_no = int(govde["takim_numarasi"])
            simdi = time.monotonic()
            with KILIT:
                zamanlar = DURUM.telemetri_zamanlari.setdefault(takim_no, [])
                zamanlar[:] = [z for z in zamanlar if simdi - z < 1.0]
                if len(zamanlar) >= 2:  # saniyede en fazla 2 paket
                    self._cevap(400, "3")
                    return
                zamanlar.append(simdi)
                DURUM.sayac += 1
                DURUM.son_telemetri[takim_no] = govde
                rakip_listesi = [r for r in DURUM.rakipler() if r["takim_numarasi"] != takim_no]
            self._cevap(200, {"sunucusaati": sunucu_saati(), "konumBilgileri": rakip_listesi})
        elif self.path == "/api/kilitlenme_bilgisi":
            if not self._oturum_var():
                self._cevap(401, "Kimliksiz erişim")
                return
            if not isinstance(govde, dict) or "kilitlenmeBitisZamani" not in govde or "otonom_kilitlenme" not in govde:
                self._cevap(204)
                return
            print(f"[sim] KİLİTLENME paketi: {json.dumps(govde, ensure_ascii=False)}")
            self._cevap(200, "OK")
        elif self.path == "/api/kamikaze_bilgisi":
            if not self._oturum_var():
                self._cevap(401, "Kimliksiz erişim")
                return
            gerekli = ("kamikazeBaslangicZamani", "kamikazeBitisZamani", "qrMetni")
            if not isinstance(govde, dict) or any(a not in govde for a in gerekli):
                self._cevap(204)
                return
            print(f"[sim] KAMİKAZE paketi: {json.dumps(govde, ensure_ascii=False)}")
            self._cevap(200, "OK")
        else:
            self._cevap(404, "Geçersiz URL")


def sunucu_kur(args) -> ThreadingHTTPServer:
    global DURUM
    DURUM = Durum(args)
    return ThreadingHTTPServer((args.host, args.port), Istekci)


def argumanlar(argv=None):
    p = argparse.ArgumentParser(description="Savaşan İHA yarışma sunucusu benzetimi")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--kadi", default="takimkadi")
    p.add_argument("--sifre", default="takimsifresi")
    p.add_argument("--takim", type=int, default=1)
    p.add_argument("--merkez", nargs=2, type=float, default=[40.2306, 29.0060], metavar=("ENLEM", "BOYLAM"))
    p.add_argument("--qr", nargs=2, type=float, default=[40.2311, 29.0041], metavar=("ENLEM", "BOYLAM"))
    p.add_argument("--hss-baslangic", type=float, default=45.0, help="HSS'lerin aktifleşme gecikmesi (sn)")
    p.add_argument("--hss-sure", type=float, default=120.0, help="HSS'lerin aktif kalma süresi (sn)")
    return p.parse_args(argv)


if __name__ == "__main__":
    args = argumanlar()
    sunucu = sunucu_kur(args)
    print(f"[sim] Yarışma sunucusu benzetimi: http://{args.host}:{args.port}  (kadi={args.kadi})")
    print(f"[sim] HSS bölgeleri {args.hss_baslangic:.0f} sn sonra {args.hss_sure:.0f} sn boyunca aktif olacak (döngüsel)")
    try:
        sunucu.serve_forever()
    except KeyboardInterrupt:
        print("\n[sim] kapatılıyor")
