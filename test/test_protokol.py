"""Haberleşme protokolü ve kural mantığı birim testleri (arayüz gerektirmez).

Çalıştırma (proje kökünden):
    python3 -m unittest discover -s test -v
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

import sim_sunucu  # noqa: E402
from yki import geo  # noqa: E402
from yki.ayarlar import Ayarlar  # noqa: E402
from yki.durum import DurumModeli, mono  # noqa: E402
from yki.sunucu_istemci import SunucuIstemci, SunucuSaati, dict_ms, saat_dict_ms  # noqa: E402


def sunucu_baslat():
    args = sim_sunucu.argumanlar(["--host", "127.0.0.1", "--port", "0",
                                  "--hss-baslangic", "0", "--hss-sure", "99999"])
    sunucu = sim_sunucu.sunucu_kur(args)
    threading.Thread(target=sunucu.serve_forever, daemon=True).start()
    return sunucu, args, f"http://127.0.0.1:{sunucu.server_address[1]}"


class ProtokolTesti(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sunucu, cls.args, cls.url = sunucu_baslat()

    @classmethod
    def tearDownClass(cls):
        cls.sunucu.shutdown()

    def _giris(self) -> requests.Session:
        oturum = requests.Session()
        cevap = oturum.post(self.url + "/api/giris",
                            json={"kadi": self.args.kadi, "sifre": self.args.sifre}, timeout=2)
        self.assertEqual(cevap.status_code, 200)
        return oturum

    def _paket(self, **degisiklik) -> dict:
        paket = {
            "takim_numarasi": 1, "iha_enlem": 40.23, "iha_boylam": 29.0,
            "iha_irtifa": 90.0, "iha_dikilme": 5.0, "iha_yonelme": 180.0,
            "iha_yatis": -10.0, "iha_hiz": 20.0, "iha_batarya": 80,
            "iha_otonom": 1, "iha_kilitlenme": 0,
            "hedef_merkez_X": 0, "hedef_merkez_Y": 0,
            "hedef_genislik": 0, "hedef_yukseklik": 0,
            "gps_saati": {"saat": 11, "dakika": 30, "saniye": 0, "milisaniye": 0},
        }
        paket.update(degisiklik)
        return paket

    def test_giris_hatali_sifre_400(self):
        cevap = requests.post(self.url + "/api/giris",
                              json={"kadi": "x", "sifre": "y"}, timeout=2)
        self.assertEqual(cevap.status_code, 400)

    def test_sunucu_saati_formati(self):
        cevap = requests.get(self.url + "/api/sunucusaati", timeout=2)
        self.assertEqual(cevap.status_code, 200)
        veri = cevap.json()
        for alan in ("gun", "saat", "dakika", "saniye", "milisaniye"):
            self.assertIn(alan, veri)

    def test_yetkisiz_telemetri_401(self):
        cevap = requests.post(self.url + "/api/telemetri_gonder", json=self._paket(), timeout=2)
        self.assertEqual(cevap.status_code, 401)

    def test_telemetri_ve_rakipler(self):
        oturum = self._giris()
        cevap = oturum.post(self.url + "/api/telemetri_gonder", json=self._paket(), timeout=2)
        self.assertEqual(cevap.status_code, 200)
        veri = cevap.json()
        self.assertIn("sunucusaati", veri)
        self.assertGreaterEqual(len(veri["konumBilgileri"]), 3)
        ilk = veri["konumBilgileri"][0]
        for alan in ("takim_numarasi", "iha_enlem", "iha_boylam", "iha_irtifa", "iha_hizi", "zaman_farki"):
            self.assertIn(alan, ilk)

    def test_eksik_alan_204(self):
        oturum = self._giris()
        paket = self._paket()
        del paket["gps_saati"]
        cevap = oturum.post(self.url + "/api/telemetri_gonder", json=paket, timeout=2)
        self.assertEqual(cevap.status_code, 204)

    def test_aralik_disi_400(self):
        oturum = self._giris()
        cevap = oturum.post(self.url + "/api/telemetri_gonder",
                            json=self._paket(iha_dikilme=120.0), timeout=2)
        self.assertEqual(cevap.status_code, 400)

    def test_hiz_limiti_400_3(self):
        oturum = self._giris()
        paket = self._paket(takim_numarasi=7)
        kodlar = [oturum.post(self.url + "/api/telemetri_gonder", json=paket, timeout=2)
                  for _ in range(3)]
        self.assertEqual(kodlar[-1].status_code, 400)
        self.assertEqual(kodlar[-1].text.strip(), "3")

    def test_kilitlenme_ve_kamikaze(self):
        oturum = self._giris()
        kilit = {"kilitlenmeBitisZamani": {"saat": 11, "dakika": 41, "saniye": 3, "milisaniye": 141},
                 "otonom_kilitlenme": 1}
        self.assertEqual(oturum.post(self.url + "/api/kilitlenme_bilgisi", json=kilit, timeout=2).status_code, 200)
        kamikaze = {
            "kamikazeBaslangicZamani": {"saat": 11, "dakika": 44, "saniye": 13, "milisaniye": 361},
            "kamikazeBitisZamani": {"saat": 11, "dakika": 44, "saniye": 27, "milisaniye": 874},
            "qrMetni": "teknofest2026",
        }
        self.assertEqual(oturum.post(self.url + "/api/kamikaze_bilgisi", json=kamikaze, timeout=2).status_code, 200)

    def test_qr_ve_hss(self):
        oturum = self._giris()
        qr = oturum.get(self.url + "/api/qr_koordinati", timeout=2).json()
        self.assertIn("qrEnlem", qr)
        self.assertIn("qrBoylam", qr)
        hss = oturum.get(self.url + "/api/hss_koordinatlari", timeout=2).json()
        self.assertIn("hss_koordinat_bilgileri", hss)
        self.assertEqual(len(hss["hss_koordinat_bilgileri"]), 3)
        for alan in ("id", "hssEnlem", "hssBoylam", "hssYaricap"):
            self.assertIn(alan, hss["hss_koordinat_bilgileri"][0])


class IstemciTesti(unittest.TestCase):
    """SunucuIstemci'nin uçtan uca davranışı (sahte sunucu ile)."""

    def test_istemci_dongusu(self):
        sunucu, args, url = sunucu_baslat()
        try:
            ayarlar = Ayarlar(None)
            ayarlar.veri["sunucu"]["url"] = url
            ayarlar.veri["sunucu"]["telemetri_hz"] = 2.0
            durum = DurumModeli()
            saat = SunucuSaati()
            istemci = SunucuIstemci(ayarlar, durum, saat)
            dur = threading.Event()

            def besle():
                while not dur.is_set():
                    durum.iha_guncelle(kaynak="TEST", enlem=40.23, boylam=29.0, irtifa=90.0,
                                       dikilme=2.0, yatis=1.0, yonelme=90.0, hiz=20.0,
                                       batarya=75.0, mod="AUTO", otonom=True, armed=True,
                                       gps_utc_ms=1000, gps_utc_kaynak_mono=mono())
                    time.sleep(0.2)

            threading.Thread(target=besle, daemon=True).start()
            istemci.start()
            istemci.giris_yap()
            istemci.telemetri_baslat(True)
            time.sleep(3.0)
            istemci.kilitlenme_gonder(saat.simdi_ms(), True)
            istemci.qr_sorgula()
            istemci.hss_sorgula()
            time.sleep(1.0)
            with durum.kilitli():
                self.assertTrue(durum.sunucu.giris_ok)
                self.assertGreaterEqual(durum.sunucu.gonderilen_telemetri, 3)
                self.assertEqual(durum.sunucu.telemetri_hata, 0)
                self.assertGreaterEqual(len(durum.rakipler), 3)
                self.assertEqual(durum.sunucu.kilit_paket_sayisi, 1)
                self.assertTrue(durum.sunucu.qr_var)
                self.assertEqual(len(durum.hss_listesi), 3)
            self.assertTrue(saat.senkron)
            istemci.durdur()
            dur.set()
        finally:
            sunucu.shutdown()

    def test_telemetri_hizi_2hz_asilmaz(self):
        """Gönderim planlayıcısı ard arda paketler arasında >= 0.5 sn bırakmalı."""
        sunucu, args, url = sunucu_baslat()
        try:
            ayarlar = Ayarlar(None)
            ayarlar.veri["sunucu"]["url"] = url
            ayarlar.veri["sunucu"]["telemetri_hz"] = 5.0  # kasıtlı hatalı ayar
            self.assertGreaterEqual(ayarlar.telemetri_araligi_sn, 0.5)  # 2 Hz'e kırpılır
        finally:
            sunucu.shutdown()


class MavlinkGorevTesti(unittest.TestCase):
    """MAVLink görev (waypoint) yükleme/okuma el sıkışması — sahte otopilotla."""

    def test_gorev_yukle_ve_oku(self):
        try:
            from pymavlink import mavutil
        except ImportError:
            self.skipTest("pymavlink kurulu değil")
        from yki.telemetri_kaynagi import MavlinkKaynak

        port = 14562
        ayarlar = Ayarlar(None)
        ayarlar.veri["baglanti"]["mavlink_url"] = f"udpin:127.0.0.1:{port}"
        durum = DurumModeli()
        olaylar = []
        orjinal = durum.olay
        durum.olay = lambda s, m: (olaylar.append(m), orjinal(s, m))
        kaynak = MavlinkKaynak(ayarlar, durum)
        kaynak.start()
        time.sleep(0.4)

        ap = mavutil.mavlink_connection(f"udpout:127.0.0.1:{port}", source_system=1, source_component=1)
        depo = []
        dur = threading.Event()

        def fake_ap():
            son_hb = 0.0
            beklenen, alinan = 0, {}
            while not dur.is_set():
                if time.time() - son_hb > 0.4:
                    son_hb = time.time()
                    ap.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_FIXED_WING,
                                          mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA, 0, 10, 4)
                msg = ap.recv_match(blocking=True, timeout=0.1)
                if msg is None:
                    continue
                t = msg.get_type()
                if t == "MISSION_COUNT":
                    beklenen, alinan = msg.count, {}
                    ap.mav.mission_request_int_send(255, 0, 0)
                elif t == "MISSION_ITEM_INT" and beklenen:
                    alinan[msg.seq] = (msg.command, msg.x / 1e7, msg.y / 1e7, msg.z)
                    if len(alinan) < beklenen:
                        ap.mav.mission_request_int_send(255, 0, len(alinan))
                    else:
                        depo[:] = [alinan[s] for s in sorted(alinan)]
                        beklenen = 0
                        ap.mav.mission_ack_send(255, 0, 0)
                elif t == "MISSION_REQUEST_LIST":
                    ap.mav.mission_count_send(255, 0, len(depo))
                elif t in ("MISSION_REQUEST_INT", "MISSION_REQUEST") and msg.seq < len(depo):
                    c, la, lo, al = depo[msg.seq]
                    ap.mav.mission_item_int_send(255, 0, msg.seq, 3, c, 0, 1,
                                                 0, 0, 0, 0, int(la * 1e7), int(lo * 1e7), al)

        threading.Thread(target=fake_ap, daemon=True).start()
        try:
            son = time.time() + 4
            while time.time() < son and not kaynak.bagli:
                time.sleep(0.1)
            self.assertTrue(kaynak.bagli)
            durum.iha_guncelle(kaynak="MAVLINK", enlem=40.23, boylam=29.005)

            kaynak.gorev_yukle([
                {"enlem": 40.2310, "boylam": 29.0060, "irtifa": 80.0, "kalkis": True},
                {"enlem": 40.2320, "boylam": 29.0080, "irtifa": 100.0, "kalkis": False},
            ])
            son = time.time() + 5
            while time.time() < son and not any("GÖREV YÜKLENDİ" in m for m in olaylar):
                time.sleep(0.1)
            self.assertTrue(any("GÖREV YÜKLENDİ" in m for m in olaylar))
            self.assertEqual(len(depo), 3)                    # ev + 2 WP
            self.assertEqual(depo[1][0], 22)                  # NAV_TAKEOFF
            self.assertAlmostEqual(depo[2][1], 40.2320, places=6)

            kaynak.gorev_oku()
            son = time.time() + 5
            while time.time() < son and durum.gorev_surumu == 0:
                time.sleep(0.1)
            self.assertEqual(len(durum.gorev_wps), 2)         # ev atlanır
            self.assertAlmostEqual(durum.gorev_wps[0][0], 40.2310, places=6)
        finally:
            dur.set()
            kaynak.durdur()


class MantikTesti(unittest.TestCase):
    def test_saat_donusumu(self):
        d = {"saat": 11, "dakika": 41, "saniye": 3, "milisaniye": 141}
        self.assertEqual(saat_dict_ms(dict_ms(d)), d)

    def test_poligon(self):
        kare = [(40.0, 29.0), (40.0, 29.01), (40.01, 29.01), (40.01, 29.0)]
        self.assertTrue(geo.nokta_poligon_icinde(40.005, 29.005, kare))
        self.assertFalse(geo.nokta_poligon_icinde(40.02, 29.005, kare))
        self.assertFalse(geo.nokta_poligon_icinde(39.995, 28.995, kare))

    def test_mesafe(self):
        # 1 derece enlem ~ 111 km
        m = geo.haversine_m(40.0, 29.0, 41.0, 29.0)
        self.assertAlmostEqual(m, 111_000, delta=1500)

    def test_kilit_takip(self):
        from yki.gorev_alici import KilitTakip
        ayarlar = Ayarlar(None)
        ayarlar.veri["gorev"]["kilitlenme_esigi_sn"] = 0.4
        ayarlar.veri["gorev"]["kilit_bosluk_toleransi_sn"] = 0.08
        takip = KilitTakip(ayarlar)
        olay = None
        son = time.monotonic() + 0.55
        while time.monotonic() < son:
            olay = takip.guncelle(True, 5555) or olay
            time.sleep(0.02)
        son = time.monotonic() + 0.25
        while time.monotonic() < son:
            olay = takip.guncelle(False, 9999) or olay
            time.sleep(0.02)
        self.assertIsNotNone(olay)
        self.assertEqual(olay[0], "tamam")
        self.assertEqual(olay[1], 5555)  # bitiş zamanı: hedefin SON görüldüğü an

    def test_kisa_kilit_paket_uretmez(self):
        from yki.gorev_alici import KilitTakip
        ayarlar = Ayarlar(None)
        ayarlar.veri["gorev"]["kilitlenme_esigi_sn"] = 0.4
        ayarlar.veri["gorev"]["kilit_bosluk_toleransi_sn"] = 0.08
        takip = KilitTakip(ayarlar)
        olay = None
        son = time.monotonic() + 0.15   # eşikten kısa oturum
        while time.monotonic() < son:
            olay = takip.guncelle(True, 5555) or olay
            time.sleep(0.02)
        son = time.monotonic() + 0.25
        while time.monotonic() < son:
            olay = takip.guncelle(False, 9999) or olay
            time.sleep(0.02)
        self.assertIsNone(olay)


if __name__ == "__main__":
    unittest.main(verbosity=2)
