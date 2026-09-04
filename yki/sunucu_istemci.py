"""TEKNOFEST Savaşan İHA yarışma sunucusu istemcisi.

Haberleşme Dokümanı 2026'ya birebir uyar:
  POST /api/giris               {"kadi", "sifre"} -> 200 + takım numarası
  GET  /api/sunucusaati         {"gun","saat","dakika","saniye","milisaniye"}
  POST /api/telemetri_gonder    telemetri paketi -> {"sunucusaati", "konumBilgileri":[...]}
  POST /api/kilitlenme_bilgisi  {"kilitlenmeBitisZamani":{...}, "otonom_kilitlenme":0/1}
  POST /api/kamikaze_bilgisi    {"kamikazeBaslangicZamani","kamikazeBitisZamani","qrMetni"}
  GET  /api/qr_koordinati       {"qrEnlem","qrBoylam"}
  GET  /api/hss_koordinatlari   {"sunucusaati","hss_koordinat_bilgileri":[...]}

Telemetri en az 1 Hz, en fazla 2 Hz gönderilir (üstü 400 + hata kodu 3).
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime, timezone

import requests

from .durum import DurumModeli, mono

GUN_MS = 24 * 3600 * 1000

DURUM_KODU_METNI = {
    200: "OK",
    204: "Paket formatı yanlış (204)",
    400: "İstek hatalı/geçersiz (400)",
    401: "Oturum açılmamış (401)",
    403: "Yetkisiz erişim (403)",
    404: "Geçersiz URL (404)",
    500: "Sunucu içi hata (500)",
}


def saat_dict_ms(ms_of_day: int) -> dict:
    """Günün milisaniyesini haberleşme dokümanı saat sözlüğüne çevirir."""
    ms_of_day = int(ms_of_day) % GUN_MS
    saat, kalan = divmod(ms_of_day, 3600 * 1000)
    dakika, kalan = divmod(kalan, 60 * 1000)
    saniye, milisaniye = divmod(kalan, 1000)
    return {"saat": int(saat), "dakika": int(dakika), "saniye": int(saniye), "milisaniye": int(milisaniye)}


def dict_ms(d: dict) -> int:
    """Sunucu saat sözlüğünü günün milisaniyesine çevirir."""
    return (int(d.get("saat", 0)) * 3600 + int(d.get("dakika", 0)) * 60 + int(d.get("saniye", 0))) * 1000 + int(d.get("milisaniye", 0))


class SunucuSaati:
    """Sunucu saati ile yerel monotonic saat arasındaki eşlemeyi tutar."""

    def __init__(self):
        self._lock = threading.Lock()
        self._ref_mono: float | None = None
        self._ref_ms: int = 0
        self._gun: int = 0
        self.gecikme_ms: float = -1.0

    @property
    def senkron(self) -> bool:
        return self._ref_mono is not None

    def guncelle(self, saat_sozlugu: dict, istek_basi_mono: float, istek_sonu_mono: float) -> None:
        """Sunucudan gelen saat ile eşle. Cevabın yol süresinin yarısı telafi edilir."""
        rtt = max(0.0, istek_sonu_mono - istek_basi_mono)
        with self._lock:
            self._ref_mono = istek_sonu_mono - rtt / 2.0
            self._ref_ms = dict_ms(saat_sozlugu)
            self._gun = int(saat_sozlugu.get("gun", self._gun) or self._gun)
            self.gecikme_ms = rtt * 1000.0

    def simdi_ms(self) -> int:
        """Tahmini anlık sunucu saati (günün milisaniyesi). Senkron yoksa yerel UTC."""
        with self._lock:
            if self._ref_mono is None:
                simdi = datetime.now(timezone.utc)
                return ((simdi.hour * 60 + simdi.minute) * 60 + simdi.second) * 1000 + simdi.microsecond // 1000
            return int(self._ref_ms + (mono() - self._ref_mono) * 1000.0) % GUN_MS

    def simdi_dict(self) -> dict:
        return saat_dict_ms(self.simdi_ms())

    @property
    def gun(self) -> int:
        return self._gun


def _sinirla(deger: float, alt: float, ust: float) -> float:
    return max(alt, min(ust, deger))


class SunucuIstemci(threading.Thread):
    """Yarışma sunucusu ile tüm haberleşmeyi yürüten iş parçacığı."""

    def __init__(self, ayarlar, durum: DurumModeli, saat: SunucuSaati):
        super().__init__(daemon=True, name="sunucu-istemci")
        self.ayarlar = ayarlar
        self.durum = durum
        self.saat = saat
        self._dur = threading.Event()
        self._eylemler: deque = deque()
        self._oturum = requests.Session()
        self._oturum.trust_env = False       # sistem proxy ayarlarını yok say
        self._giris_istendi = False
        self._son_giris_deneme = 0.0
        self._son_saat_senk = 0.0
        self._son_hss_sorgu = 0.0
        self.hss_sorgu_aktif = False
        self._telemetri_sonraki = 0.0
        self._ardisik_hata = 0
        self._eksik_veri_uyarildi = False
        self._gps_saat_uyarildi = False
        with durum.kilitli():
            durum.sunucu.url = self.url

    # ---------------- genel yardımcılar ----------------
    @property
    def url(self) -> str:
        return str(self.ayarlar.al("sunucu", "url", varsayilan="http://127.0.0.1:5000")).rstrip("/")

    @property
    def _zaman_asimi(self) -> float:
        return float(self.ayarlar.al("sunucu", "zaman_asimi_sn", varsayilan=2.0) or 2.0)

    def durdur(self) -> None:
        self._dur.set()

    # Arayüzden çağrılan istekler (kuyruklanır, istemci iş parçacığı işler)
    def giris_yap(self) -> None:
        self._eylemler.append(("giris",))

    def telemetri_baslat(self, aktif: bool) -> None:
        with self.durum.kilitli():
            self.durum.sunucu.telemetri_aktif = aktif
        self.durum.olay("BILGI", "Telemetri gönderimi " + ("BAŞLATILDI" if aktif else "DURDURULDU"))

    def kilitlenme_gonder(self, bitis_ms: int, otonom: bool) -> None:
        self._eylemler.append(("kilit", int(bitis_ms), 1 if otonom else 0))

    def kamikaze_gonder(self, baslangic_ms: int, bitis_ms: int, qr_metni: str) -> None:
        self._eylemler.append(("kamikaze", int(baslangic_ms), int(bitis_ms), str(qr_metni)))

    def qr_sorgula(self) -> None:
        self._eylemler.append(("qr",))

    def hss_sorgula(self) -> None:
        self._eylemler.append(("hss",))

    # ---------------- HTTP katmanı ----------------
    def _istek(self, metod: str, yol: str, govde: dict | None = None):
        """(durum_kodu, json|metin, rtt_çifti) döndürür; ağ hatasında (0, hata_metni, None)."""
        adres = self.url + yol
        t0 = mono()
        try:
            if metod == "GET":
                cevap = self._oturum.get(adres, timeout=self._zaman_asimi)
            else:
                cevap = self._oturum.post(adres, json=govde, timeout=self._zaman_asimi)
            t1 = mono()
        except requests.RequestException as e:
            self._ag_hatasi(f"{yol}: {e.__class__.__name__}")
            return 0, str(e), None
        self._ardisik_hata = 0
        with self.durum.kilitli():
            self.durum.sunucu.son_durum_kodu = cevap.status_code
            self.durum.sunucu.son_hata = "" if cevap.ok else DURUM_KODU_METNI.get(cevap.status_code, str(cevap.status_code))
        try:
            icerik = cevap.json()
        except ValueError:
            icerik = cevap.text
        return cevap.status_code, icerik, (t0, t1)

    def _ag_hatasi(self, metin: str) -> None:
        self._ardisik_hata += 1
        with self.durum.kilitli():
            self.durum.sunucu.son_durum_kodu = 0
            self.durum.sunucu.son_hata = "AĞ HATASI: " + metin
        if self._ardisik_hata in (1, 5) or self._ardisik_hata % 20 == 0:
            self.durum.olay("HATA", f"Sunucuya ulaşılamıyor ({self._ardisik_hata}. deneme): {metin}")

    # ---------------- uç noktalar ----------------
    def _giris(self) -> bool:
        kadi = str(self.ayarlar.al("sunucu", "kadi", varsayilan=""))
        sifre = str(self.ayarlar.al("sunucu", "sifre", varsayilan=""))
        kod, icerik, _ = self._istek("POST", "/api/giris", {"kadi": kadi, "sifre": sifre})
        if kod == 200:
            takim_no = 0
            if isinstance(icerik, (int, float)):
                takim_no = int(icerik)
            elif isinstance(icerik, str):
                rakamlar = "".join(c for c in icerik if c.isdigit())
                takim_no = int(rakamlar) if rakamlar else 0
            elif isinstance(icerik, dict):
                for anahtar in ("takim_numarasi", "takimNumarasi", "takim_no"):
                    if anahtar in icerik:
                        takim_no = int(icerik[anahtar])
                        break
            if takim_no <= 0:
                takim_no = int(self.ayarlar.al("sunucu", "takim_numarasi", varsayilan=1) or 1)
                self.durum.olay("UYARI", f"Giriş OK fakat takım numarası çözülemedi; config değeri kullanılıyor: {takim_no}")
            with self.durum.kilitli():
                self.durum.sunucu.giris_ok = True
                self.durum.sunucu.takim_numarasi = takim_no
            self.durum.olay("BASARI", f"Sunucuya giriş yapıldı. Takım numarası: {takim_no}")
            self._saat_senkronla()
            return True
        if kod == 400:
            self.durum.olay("HATA", "Giriş reddedildi: kullanıcı adı/şifre geçersiz (400)")
        elif kod != 0:
            self.durum.olay("HATA", f"Giriş başarısız: {DURUM_KODU_METNI.get(kod, kod)}")
        with self.durum.kilitli():
            self.durum.sunucu.giris_ok = False
        return False

    def _saat_senkronla(self) -> None:
        kod, icerik, zamanlar = self._istek("GET", "/api/sunucusaati")
        if kod == 200 and isinstance(icerik, dict) and zamanlar:
            self.saat.guncelle(icerik, *zamanlar)
            with self.durum.kilitli():
                self.durum.sunucu.saat_ofset_ok = True
                self.durum.sunucu.gecikme_ms = self.saat.gecikme_ms
                self.durum.sunucu.sunucu_gun = self.saat.gun
        self._son_saat_senk = mono()

    def _gps_saati_dict(self, iha) -> dict:
        """Uçağın GPS saatinden (UTC) telemetri gps_saati alanını üretir."""
        if iha.gps_utc_ms >= 0 and iha.gps_utc_kaynak_mono > 0:
            ms = int(iha.gps_utc_ms + (mono() - iha.gps_utc_kaynak_mono) * 1000.0) % GUN_MS
            self._gps_saat_uyarildi = False
            return saat_dict_ms(ms)
        if not self._gps_saat_uyarildi:
            self._gps_saat_uyarildi = True
            self.durum.olay("UYARI", "Uçaktan GPS saati alınamıyor — gps_saati alanında YKİ sistem saati (UTC) kullanılıyor!")
        simdi = datetime.now(timezone.utc)
        ms = ((simdi.hour * 60 + simdi.minute) * 60 + simdi.second) * 1000 + simdi.microsecond // 1000
        return saat_dict_ms(ms)

    def _telemetri_paketi(self) -> dict | None:
        anlik = self.durum.anlik()
        iha, kilit = anlik.iha, anlik.kilit
        if not iha.taze(2.5):
            if not self._eksik_veri_uyarildi:
                self._eksik_veri_uyarildi = True
                self.durum.olay("HATA", "FCU telemetrisi TAZE DEĞİL — sunucuya gönderim duraklatıldı (donuk veri cezalıdır)")
            return None
        if self._eksik_veri_uyarildi:
            self._eksik_veri_uyarildi = False
            self.durum.olay("BASARI", "FCU telemetrisi geri geldi — sunucu gönderimi sürüyor")
        kilit_var = 1 if (kilit.aktif and kilit.taze(1.0)) else 0
        with self.durum.kilitli():
            takim_no = self.durum.sunucu.takim_numarasi or int(self.ayarlar.al("sunucu", "takim_numarasi", varsayilan=1) or 1)
        # Şartname: aralık dışı tek bir alan tüm paketi geçersiz kılar -> gönderim öncesi sınırla
        return {
            "takim_numarasi": takim_no,
            "iha_enlem": round(float(iha.enlem), 7),
            "iha_boylam": round(float(iha.boylam), 7),
            "iha_irtifa": round(float(iha.irtifa), 2),
            "iha_dikilme": round(_sinirla(float(iha.dikilme), -90.0, 90.0), 2),
            "iha_yonelme": round(float(iha.yonelme) % 360.0, 2),
            "iha_yatis": round(_sinirla(float(iha.yatis), -90.0, 90.0), 2),
            "iha_hiz": round(max(0.0, float(iha.hiz)), 2),
            "iha_batarya": int(_sinirla(float(iha.batarya), 0.0, 100.0)),
            "iha_otonom": 1 if iha.otonom else 0,
            "iha_kilitlenme": kilit_var,
            "hedef_merkez_X": int(kilit.merkez_x) if kilit_var else 0,
            "hedef_merkez_Y": int(kilit.merkez_y) if kilit_var else 0,
            "hedef_genislik": int(kilit.genislik) if kilit_var else 0,
            "hedef_yukseklik": int(kilit.yukseklik) if kilit_var else 0,
            "gps_saati": self._gps_saati_dict(iha),
        }

    def _telemetri_gonder(self) -> None:
        paket = self._telemetri_paketi()
        if paket is None:
            return
        kod, icerik, zamanlar = self._istek("POST", "/api/telemetri_gonder", paket)
        with self.durum.kilitli():
            if kod == 200:
                self.durum.sunucu.gonderilen_telemetri += 1
                self.durum.sunucu.son_telemetri_mono = mono()
            elif kod != 0:
                self.durum.sunucu.telemetri_hata += 1
        if kod == 200 and isinstance(icerik, dict):
            saat_sozlugu = icerik.get("sunucusaati")
            if isinstance(saat_sozlugu, dict) and zamanlar:
                self.saat.guncelle(saat_sozlugu, *zamanlar)
                with self.durum.kilitli():
                    self.durum.sunucu.saat_ofset_ok = True
                    self.durum.sunucu.gecikme_ms = self.saat.gecikme_ms
                    self.durum.sunucu.sunucu_gun = self.saat.gun
            konumlar = icerik.get("konumBilgileri")
            if isinstance(konumlar, list):
                with self.durum.kilitli():
                    takim_no = self.durum.sunucu.takim_numarasi
                self.durum.rakipleri_guncelle(konumlar, takim_no)
        elif kod == 401:
            with self.durum.kilitli():
                self.durum.sunucu.giris_ok = False
            self.durum.olay("UYARI", "Oturum düştü (401) — yeniden giriş denenecek")
        elif kod == 400:
            hata_kodu = str(icerik).strip()
            if hata_kodu == "3":
                self.durum.olay("HATA", "Telemetri hız aşımı (2 Hz üstü) — sunucu 400/3 döndürdü; aralık denetleyin!")
            else:
                self.durum.olay("HATA", f"Telemetri reddedildi (400): {hata_kodu[:120]}")
        elif kod == 204:
            self.durum.olay("HATA", "Telemetri paket formatı yanlış (204)")

    def _kilit_gonder(self, bitis_ms: int, otonom: int) -> None:
        paket = {
            "kilitlenmeBitisZamani": saat_dict_ms(bitis_ms),
            "otonom_kilitlenme": otonom,
        }
        kod, icerik, _ = self._istek("POST", "/api/kilitlenme_bilgisi", paket)
        if kod == 200:
            with self.durum.kilitli():
                self.durum.sunucu.kilit_paket_sayisi += 1
            t = paket["kilitlenmeBitisZamani"]
            self.durum.olay("BASARI", f"KİLİTLENME paketi gönderildi (bitiş {t['saat']:02d}:{t['dakika']:02d}:{t['saniye']:02d}.{t['milisaniye']:03d}, otonom={otonom})")
        else:
            self.durum.olay("HATA", f"Kilitlenme paketi GÖNDERİLEMEDİ: {DURUM_KODU_METNI.get(kod, icerik)}")

    def _kamikaze_gonder(self, baslangic_ms: int, bitis_ms: int, qr_metni: str) -> None:
        paket = {
            "kamikazeBaslangicZamani": saat_dict_ms(baslangic_ms),
            "kamikazeBitisZamani": saat_dict_ms(bitis_ms),
            "qrMetni": qr_metni,
        }
        kod, icerik, _ = self._istek("POST", "/api/kamikaze_bilgisi", paket)
        if kod == 200:
            with self.durum.kilitli():
                self.durum.sunucu.kamikaze_paket_sayisi += 1
            self.durum.olay("BASARI", f"KAMİKAZE paketi gönderildi (qrMetni='{qr_metni}')")
        else:
            self.durum.olay("HATA", f"Kamikaze paketi GÖNDERİLEMEDİ: {DURUM_KODU_METNI.get(kod, icerik)}")

    def _qr_sorgula(self) -> None:
        kod, icerik, _ = self._istek("GET", "/api/qr_koordinati")
        if kod == 200 and isinstance(icerik, dict):
            try:
                enlem = float(icerik.get("qrEnlem", 0.0))
                boylam = float(icerik.get("qrBoylam", 0.0))
            except (TypeError, ValueError):
                self.durum.olay("HATA", f"QR koordinatı çözülemedi: {icerik}")
                return
            with self.durum.kilitli():
                self.durum.sunucu.qr_enlem = enlem
                self.durum.sunucu.qr_boylam = boylam
                self.durum.sunucu.qr_var = True
            self.durum.olay("BASARI", f"QR koordinatı alındı: {enlem:.7f}, {boylam:.7f}")
        else:
            self.durum.olay("HATA", f"QR koordinatı alınamadı: {DURUM_KODU_METNI.get(kod, icerik)}")

    def _hss_sorgula(self, elle: bool = False) -> None:
        kod, icerik, zamanlar = self._istek("GET", "/api/hss_koordinatlari")
        if kod == 200 and isinstance(icerik, dict):
            saat_sozlugu = icerik.get("sunucusaati")
            if isinstance(saat_sozlugu, dict) and zamanlar:
                self.saat.guncelle(saat_sozlugu, *zamanlar)
            liste = icerik.get("hss_koordinat_bilgileri") or []
            onceki = len(self.durum.anlik().hss_listesi)
            self.durum.hss_guncelle(liste if isinstance(liste, list) else [])
            simdiki = len(liste) if isinstance(liste, list) else 0
            if simdiki != onceki:
                if simdiki:
                    self.durum.olay("UYARI", f"HAVA SAVUNMA SİSTEMİ AKTİF: {simdiki} bölge alındı!")
                else:
                    self.durum.olay("BILGI", "HSS listesi boş (bölgeler kapalı)")
            elif elle:
                self.durum.olay("BILGI", f"HSS sorgusu: {simdiki} bölge")
        elif elle:
            self.durum.olay("HATA", f"HSS sorgusu başarısız: {DURUM_KODU_METNI.get(kod, icerik)}")
        self._son_hss_sorgu = mono()

    # ---------------- ana döngü ----------------
    def run(self) -> None:
        while not self._dur.is_set():
            simdi = mono()

            # kuyruktaki eylemler (giriş, kilit, kamikaze, qr, hss)
            while self._eylemler:
                eylem = self._eylemler.popleft()
                if eylem[0] == "giris":
                    self._giris_istendi = True
                    self._son_giris_deneme = 0.0
                elif eylem[0] == "kilit":
                    self._kilit_gonder(eylem[1], eylem[2])
                elif eylem[0] == "kamikaze":
                    self._kamikaze_gonder(eylem[1], eylem[2], eylem[3])
                elif eylem[0] == "qr":
                    self._qr_sorgula()
                elif eylem[0] == "hss":
                    self._hss_sorgula(elle=True)

            with self.durum.kilitli():
                giris_ok = self.durum.sunucu.giris_ok
                telemetri_aktif = self.durum.sunucu.telemetri_aktif

            # otomatik (yeniden) giriş
            if self._giris_istendi and not giris_ok and simdi - self._son_giris_deneme > 3.0:
                self._son_giris_deneme = simdi
                self._giris()
                with self.durum.kilitli():
                    giris_ok = self.durum.sunucu.giris_ok

            if giris_ok:
                # periyodik saat senkronu
                senk_araligi = float(self.ayarlar.al("sunucu", "saat_senk_sn", varsayilan=10.0) or 10.0)
                if simdi - self._son_saat_senk > senk_araligi:
                    self._saat_senkronla()

                # telemetri: kesinlikle 2 Hz üstüne çıkma — monotonic plana göre gönder.
                # Ağ gecikmesi titreşimi, sunucu tarafındaki 1 sn'lik pencereyi
                # sıkıştırabildiğinden alt sınıra %6 emniyet payı eklenir.
                if telemetri_aktif:
                    aralik = max(0.53, self.ayarlar.telemetri_araligi_sn)
                    if simdi >= self._telemetri_sonraki:
                        # asla plandan erken gönderme; geride kalındıysa da tam aralık bekle
                        # (ard arda gönderimler arası süre daima >= aralik >= 0.5 sn kalır)
                        self._telemetri_sonraki = simdi + aralik
                        self._telemetri_gonder()

                # HSS periyodik sorgu
                if self.hss_sorgu_aktif:
                    hss_araligi = max(1.0, float(self.ayarlar.al("sunucu", "hss_sorgu_sn", varsayilan=2.0) or 2.0))
                    if simdi - self._son_hss_sorgu > hss_araligi:
                        self._hss_sorgula()

            self._dur.wait(0.02)


__all__ = ["SunucuIstemci", "SunucuSaati", "saat_dict_ms", "dict_ms", "DURUM_KODU_METNI", "GUN_MS"]
