"""Görev verisi alıcısı ve kilitlenme oturum takibi.

Jetson/RPi üzerindeki görüntü işleme yazılımı, YKİ'ye UDP üzerinden JSON
paketleri gönderir (bkz. ornekler/jetson_gorev_ornek.py):

  {"kilitlenme": 1, "merkez_x": 320, "merkez_y": 240,
   "genislik": 64, "yukseklik": 48,
   "video_genislik": 640, "video_yukseklik": 480}

  {"qr_metni": "teknofest2026"}          # kamikaze QR okununca

Bu veriler telemetri paketindeki hedef_* alanlarını besler; KilitTakip ise
kesintisiz 4 sn kuralını izleyip kilit bittiğinde otomatik paket tetikler.
"""

from __future__ import annotations

import json
import socket
import threading

from .durum import DurumModeli, mono


class GorevAlici(threading.Thread):
    """UDP JSON dinleyicisi (görüntü işleme -> YKİ)."""

    def __init__(self, ayarlar, durum: DurumModeli, qr_geri_cagri=None):
        super().__init__(daemon=True, name="gorev-alici")
        self.ayarlar = ayarlar
        self.durum = durum
        self.qr_geri_cagri = qr_geri_cagri
        self._dur = threading.Event()
        self._soket: socket.socket | None = None
        self.paket_sayisi = 0

    def durdur(self) -> None:
        self._dur.set()
        try:
            if self._soket:
                self._soket.close()
        except OSError:
            pass

    def run(self) -> None:
        port = int(self.ayarlar.al("gorev", "kilitlenme_udp_port", varsayilan=42000) or 42000)
        try:
            self._soket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._soket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._soket.bind(("0.0.0.0", port))
            self._soket.settimeout(0.5)
        except OSError as e:
            self.durum.olay("HATA", f"Görev UDP portu ({port}) açılamadı: {e}")
            return
        self.durum.olay("BILGI", f"Görev alıcısı dinliyor: UDP {port} (kilitlenme/QR)")
        while not self._dur.is_set():
            try:
                veri, _ = self._soket.recvfrom(65535)
            except socket.timeout:
                # veri gelmiyorsa kilit durumunu pasif yap (bayatlama KilitDurum.taze ile)
                continue
            except OSError:
                break
            try:
                mesaj = json.loads(veri.decode("utf-8", errors="replace"))
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(mesaj, dict):
                continue
            self.paket_sayisi += 1
            self._isle(mesaj)

    def _isle(self, mesaj: dict) -> None:
        if any(k in mesaj for k in ("kilitlenme", "kilit", "merkez_x")):
            aktif = bool(int(mesaj.get("kilitlenme", mesaj.get("kilit", 0)) or 0))
            alanlar = dict(aktif=aktif)
            for kaynak_ad, hedef_ad in (
                ("merkez_x", "merkez_x"), ("merkez_y", "merkez_y"),
                ("genislik", "genislik"), ("yukseklik", "yukseklik"),
                ("video_genislik", "video_genislik"), ("video_yukseklik", "video_yukseklik"),
            ):
                if kaynak_ad in mesaj:
                    try:
                        alanlar[hedef_ad] = int(mesaj[kaynak_ad])
                    except (TypeError, ValueError):
                        pass
            self.durum.kilit_guncelle(**alanlar)
        qr = mesaj.get("qr_metni")
        if qr:
            qr = str(qr).strip()
            with self.durum.kilitli():
                yeni = qr != self.durum.qr_metni
                self.durum.qr_metni = qr
            if yeni:
                self.durum.olay("BASARI", f"QR OKUNDU: '{qr}'")
            if self.qr_geri_cagri:
                try:
                    self.qr_geri_cagri(qr)
                except Exception:  # noqa: BLE001
                    pass


class KilitTakip:
    """Kesintisiz kilitlenme oturumu takibi (şartname 6.1.1).

    - Kısa kare kayıpları (varsayılan 0.25 sn) oturumu bozmaz.
    - Oturum bittiğinde süre >= eşik (4 sn) ise 'tamamlandı' olayı üretir;
      bitiş zamanı, hedefin son görüldüğü andaki SUNUCU saatidir.
    """

    def __init__(self, ayarlar):
        self.ayarlar = ayarlar
        self._basla_mono: float | None = None
        self._son_aktif_mono: float = 0.0
        self._son_aktif_sunucu_ms: int = 0
        self._tetiklendi = False
        self.anlik_sure = 0.0

    @property
    def esik_sn(self) -> float:
        return float(self.ayarlar.al("gorev", "kilitlenme_esigi_sn", varsayilan=4.0) or 4.0)

    @property
    def bosluk_toleransi(self) -> float:
        return float(self.ayarlar.al("gorev", "kilit_bosluk_toleransi_sn", varsayilan=0.25) or 0.25)

    def guncelle(self, kilit_aktif: bool, sunucu_simdi_ms: int) -> tuple[str, int] | None:
        """~10 Hz çağrılır. Oturum tamamlandığında ('tamam', bitis_ms) döndürür."""
        simdi = mono()
        sonuc = None
        if kilit_aktif:
            if self._basla_mono is None or (simdi - self._son_aktif_mono) > self.bosluk_toleransi:
                self._basla_mono = simdi          # yeni oturum
                self._tetiklendi = False
            self._son_aktif_mono = simdi
            self._son_aktif_sunucu_ms = sunucu_simdi_ms
            self.anlik_sure = simdi - self._basla_mono
        else:
            if self._basla_mono is not None and (simdi - self._son_aktif_mono) > self.bosluk_toleransi:
                sure = self._son_aktif_mono - self._basla_mono
                if sure >= self.esik_sn and not self._tetiklendi:
                    self._tetiklendi = True
                    sonuc = ("tamam", self._son_aktif_sunucu_ms)
                self._basla_mono = None
                self.anlik_sure = 0.0
            elif self._basla_mono is None:
                self.anlik_sure = 0.0
        return sonuc
