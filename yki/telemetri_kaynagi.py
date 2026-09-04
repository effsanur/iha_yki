"""Kendi hava aracımızın telemetri kaynakları ve komut gönderimi.

Üç kaynak desteklenir:
  MAVLINK   : pymavlink ile doğrudan MAVLink akışı + komut gönderimi
              (ARM/DISARM, mod değişimi, GUIDED "buraya uç", irtifa/hız).
              Jetson/RPi üzerinde MAVROS'un gcs_url yönlendirmesi (veya
              mavlink-router) YKİ bilgisayarına UDP ile paket taşır; aynı
              kanal komutları Pixhawk'a geri götürür.
  ROSBRIDGE : roslibpy ile MAVROS konularına abone olur; mod ve ARM
              komutlarını MAVROS servisleriyle gönderebilir.
  SIM       : donanımsız geliştirme için benzetim uçuşu (komutlara uyar).

"otomatik" kipte MAVLink ve rosbridge birlikte başlatılır; MAVLink akışı
tazeyken rosbridge verisi yazılmaz (öncelik MAVLink'tedir).

Şartname 6.1.7: YKİ üzerinden komut göndermek (varış noktası, irtifa,
seyir hızı, iniş) otonomiyi BOZMAZ.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from datetime import datetime, timezone

from . import geo
from .durum import DurumModeli, mono

try:
    from pymavlink import mavutil
except ImportError:
    mavutil = None

try:
    import roslibpy
except ImportError:
    roslibpy = None

# ArduPlane custom_mode fallback eşlemesi (heartbeat'ten mapping alınamazsa)
PLANE_MODLARI = {
    "MANUAL": 0, "CIRCLE": 1, "STABILIZE": 2, "TRAINING": 3, "ACRO": 4,
    "FBWA": 5, "FBWB": 6, "CRUISE": 7, "AUTOTUNE": 8, "AUTO": 10,
    "RTL": 11, "LOITER": 12, "TAKEOFF": 13, "AVOID_ADSB": 14, "GUIDED": 15,
    "QSTABILIZE": 17, "QHOVER": 18, "QLOITER": 19, "QLAND": 20, "QRTL": 21,
    "QAUTOTUNE": 22, "QACRO": 23, "THERMAL": 24, "LOITER_ALT_QLAND": 25,
    "AUTOLAND": 26,
}

MAV_SONUC_TR = {
    0: "KABUL EDİLDİ",
    1: "geçici olarak reddedildi",
    2: "REDDEDİLDİ",
    3: "desteklenmiyor",
    4: "BAŞARISIZ",
    5: "sürüyor",
    6: "iptal edildi",
}


class TelemetriKaynagi(threading.Thread):
    AD = "-"

    def __init__(self, ayarlar, durum: DurumModeli, oncelik_dusuk: bool = False):
        super().__init__(daemon=True, name=f"kaynak-{self.AD}")
        self.ayarlar = ayarlar
        self.durum = durum
        self.oncelik_dusuk = oncelik_dusuk
        self._dur = threading.Event()
        self.bagli = False

    def durdur(self) -> None:
        self._dur.set()

    def _yazabilir(self) -> bool:
        """Düşük öncelikli kaynak, birincil kaynak tazeyken yazmaz."""
        if not self.oncelik_dusuk:
            return True
        iha = self.durum.iha
        return not (iha.kaynak != self.AD and iha.taze(1.5))

    def _otonom_mu(self, mod: str) -> bool:
        return mod.upper() in self.ayarlar.otonom_modlar

    # ---- komut arayüzü (Mission Planner "Actions" karşılığı) ----
    def komut_destegi(self) -> bool:
        return False

    def _komut_yok(self) -> None:
        self.durum.olay("HATA", f"{self.AD} kaynağı bu komutu desteklemiyor")

    def mod_ayarla(self, mod: str) -> None:      # noqa: ARG002
        self._komut_yok()

    def arm(self, aktif: bool) -> None:          # noqa: ARG002
        self._komut_yok()

    def git(self, enlem: float, boylam: float, irtifa: float) -> None:  # noqa: ARG002
        self._komut_yok()

    def irtifa_degistir(self, irtifa: float) -> None:  # noqa: ARG002
        self._komut_yok()

    def hiz_degistir(self, hiz: float) -> None:  # noqa: ARG002
        self._komut_yok()

    def gorev_yukle(self, wps: list) -> None:  # noqa: ARG002
        """Waypoint görevini araca yükler. wps: [{"enlem","boylam","irtifa","kalkis"}]"""
        self._komut_yok()

    def gorev_oku(self) -> None:
        self._komut_yok()


# --------------------------------------------------------------------------
class MavlinkKaynak(TelemetriKaynagi):
    AD = "MAVLINK"

    def __init__(self, ayarlar, durum: DurumModeli, oncelik_dusuk: bool = False):
        super().__init__(ayarlar, durum, oncelik_dusuk)
        self._komutlar: deque = deque()
        self._baglanti = None
        self._hedef_sistem = 1
        self._yukleme: dict | None = None   # görev yükleme el sıkışması durumu
        self._okuma: dict | None = None     # görev okuma durumu

    # ---- komutlar (kuyruklanır, dinleme iş parçacığı gönderir) ----
    def komut_destegi(self) -> bool:
        return True

    def _kuyrukla(self, *komut) -> None:
        if not self.bagli or self._baglanti is None:
            self.durum.olay("HATA", "MAVLink bağlı değil — komut gönderilemedi")
            return
        self._komutlar.append(komut)

    def mod_ayarla(self, mod: str) -> None:
        self._kuyrukla("mod", mod.upper())

    def arm(self, aktif: bool) -> None:
        self._kuyrukla("arm", bool(aktif))

    def git(self, enlem: float, boylam: float, irtifa: float) -> None:
        self._kuyrukla("git", float(enlem), float(boylam), float(irtifa))

    def irtifa_degistir(self, irtifa: float) -> None:
        self._kuyrukla("irtifa", float(irtifa))

    def hiz_degistir(self, hiz: float) -> None:
        self._kuyrukla("hiz", float(hiz))

    def gorev_yukle(self, wps: list) -> None:
        self._kuyrukla("gorev_yukle", list(wps))

    def gorev_oku(self) -> None:
        self._kuyrukla("gorev_oku")

    # ---- ana döngü ----
    def run(self) -> None:
        if mavutil is None:
            self.durum.olay("HATA", "pymavlink kurulu değil (pip install pymavlink) — MAVLink kaynağı kapalı")
            return
        url = str(self.ayarlar.al("baglanti", "mavlink_url", varsayilan="udpin:0.0.0.0:14550"))
        baud = int(self.ayarlar.al("baglanti", "mavlink_baud", varsayilan=57600) or 57600)
        while not self._dur.is_set():
            try:
                self.durum.olay("BILGI", f"MAVLink dinleniyor: {url}")
                self._baglanti = mavutil.mavlink_connection(url, baud=baud, source_system=255, autoreconnect=True)
                self._dinle()
            except Exception as e:  # noqa: BLE001 - kaynak asla çökmemeli
                self.durum.olay("HATA", f"MAVLink bağlantı hatası: {e}")
                self._dur.wait(3.0)
            finally:
                self.bagli = False
                try:
                    if self._baglanti:
                        self._baglanti.close()
                except Exception:  # noqa: BLE001
                    pass
                self._baglanti = None

    def _dinle(self) -> None:
        son_paket = mono()
        while not self._dur.is_set():
            msg = self._baglanti.recv_match(blocking=True, timeout=0.2)
            self._komutlari_gonder()
            self._gorev_zaman_asimi()
            if msg is None:
                if self.bagli and mono() - son_paket > 3.0:
                    self.bagli = False
                    self.durum.olay("UYARI", "MAVLink akışı kesildi (3 sn paket yok)")
                continue
            tip = msg.get_type()
            if tip == "BAD_DATA":
                continue
            son_paket = mono()
            if not self.bagli:
                self.bagli = True
                self.durum.olay("BASARI", f"MAVLink akışı alındı (sistem {msg.get_srcSystem()})")
            if not self._yazabilir():
                continue

            if tip == "HEARTBEAT":
                # yalnızca otopilot heartbeat'i işlenir; MAVROS/YKİ gibi
                # bileşenlerin heartbeat'leri mod bilgisini kirletmesin
                if (msg.type == mavutil.mavlink.MAV_TYPE_GCS
                        or msg.autopilot == mavutil.mavlink.MAV_AUTOPILOT_INVALID):
                    continue
                self._hedef_sistem = msg.get_srcSystem()
                mod = mavutil.mode_string_v10(msg)
                armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                self.durum.iha_guncelle(kaynak=self.AD, mod=mod, armed=armed, otonom=self._otonom_mu(mod))
            elif tip == "GLOBAL_POSITION_INT":
                alanlar = dict(
                    kaynak=self.AD,
                    enlem=msg.lat / 1e7,
                    boylam=msg.lon / 1e7,
                    irtifa=msg.relative_alt / 1000.0,
                    irtifa_msl=msg.alt / 1000.0,
                    hiz=math.hypot(msg.vx / 100.0, msg.vy / 100.0),
                )
                if msg.hdg != 65535:
                    alanlar["yonelme"] = (msg.hdg / 100.0) % 360.0
                self.durum.iha_guncelle(**alanlar)
            elif tip == "ATTITUDE":
                self.durum.iha_guncelle(
                    kaynak=self.AD,
                    yatis=math.degrees(msg.roll),
                    dikilme=math.degrees(msg.pitch),
                )
            elif tip == "VFR_HUD":
                self.durum.iha_guncelle(kaynak=self.AD, hiz=float(msg.groundspeed),
                                        hava_hizi=float(msg.airspeed))
            elif tip == "SYS_STATUS":
                if msg.battery_remaining >= 0:
                    self.durum.iha_guncelle(kaynak=self.AD, batarya=float(msg.battery_remaining))
            elif tip == "GPS_RAW_INT":
                self.durum.iha_guncelle(kaynak=self.AD, gps_fix=int(msg.fix_type), gps_uydu=int(msg.satellites_visible))
            elif tip == "SYSTEM_TIME":
                if msg.time_unix_usec > 0:
                    ms_gun = (msg.time_unix_usec // 1000) % (24 * 3600 * 1000)
                    self.durum.iha_guncelle(kaynak=self.AD, gps_utc_ms=int(ms_gun), gps_utc_kaynak_mono=mono())
            elif tip == "COMMAND_ACK":
                sonuc = MAV_SONUC_TR.get(msg.result, str(msg.result))
                seviye = "BASARI" if msg.result == 0 else "HATA"
                self.durum.olay(seviye, f"Komut cevabı (id {msg.command}): {sonuc}")
            elif tip == "STATUSTEXT":
                metin = msg.text.decode("utf-8", "replace") if isinstance(msg.text, bytes) else str(msg.text)
                seviye = "HATA" if msg.severity <= 3 else ("UYARI" if msg.severity == 4 else "BILGI")
                self.durum.olay(seviye, f"FCU: {metin}")
            elif tip in ("MISSION_REQUEST", "MISSION_REQUEST_INT"):
                self._gorev_istek(msg.seq, tip == "MISSION_REQUEST_INT")
            elif tip == "MISSION_ACK":
                self._gorev_ack(msg)
            elif tip == "MISSION_COUNT":
                self._gorev_sayi(msg.count)
            elif tip in ("MISSION_ITEM_INT", "MISSION_ITEM"):
                self._gorev_oge(msg, tip == "MISSION_ITEM_INT")

    # ---- komut gönderimi (dinleme iş parçacığında çalışır) ----
    def _komutlari_gonder(self) -> None:
        while self._komutlar:
            komut = self._komutlar.popleft()
            try:
                self._komut_isle(komut)
            except Exception as e:  # noqa: BLE001
                self.durum.olay("HATA", f"Komut gönderilemedi ({komut[0]}): {e}")

    def _mod_numarasi(self, mod: str) -> int | None:
        try:
            eslesme = self._baglanti.mode_mapping() or {}
        except Exception:  # noqa: BLE001
            eslesme = {}
        if mod in eslesme:
            return int(eslesme[mod])
        return PLANE_MODLARI.get(mod)

    def _komut_isle(self, komut) -> None:
        mav = self._baglanti.mav
        ts = self._hedef_sistem
        if komut[0] == "mod":
            numara = self._mod_numarasi(komut[1])
            if numara is None:
                self.durum.olay("HATA", f"Bilinmeyen uçuş modu: {komut[1]}")
                return
            mav.set_mode_send(ts, mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, numara)
            self.durum.olay("BILGI", f"Mod komutu gönderildi: {komut[1]}")
        elif komut[0] == "arm":
            mav.command_long_send(ts, 1, mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
                                  1.0 if komut[1] else 0.0, 0, 0, 0, 0, 0, 0)
            self.durum.olay("BILGI", "ARM komutu gönderildi" if komut[1] else "DISARM komutu gönderildi")
        elif komut[0] == "git":
            enlem, boylam, irtifa = komut[1], komut[2], komut[3]
            if str(self.durum.iha.mod).upper() != "GUIDED":
                numara = self._mod_numarasi("GUIDED")
                if numara is not None:
                    mav.set_mode_send(ts, mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, numara)
            # Mission Planner'ın "Fly To Here" yöntemi: current=2 guided hedefi
            mav.mission_item_int_send(
                ts, 1, 0,
                mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
                2, 1, 0, 0, 0, 0,
                int(enlem * 1e7), int(boylam * 1e7), float(irtifa))
            self.durum.hedef_ayarla(enlem, boylam, irtifa)
            self.durum.olay("BASARI", f"BURAYA UÇ gönderildi: {enlem:.6f}, {boylam:.6f} @ {irtifa:.0f} m (GUIDED)")
        elif komut[0] == "irtifa":
            mav.command_int_send(
                ts, 1, mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
                mavutil.mavlink.MAV_CMD_GUIDED_CHANGE_ALTITUDE,
                0, 0, 0, 0, 0, 0, 0, 0, float(komut[1]))
            self.durum.olay("BILGI", f"İrtifa değişikliği gönderildi: {komut[1]:.0f} m (GUIDED)")
        elif komut[0] == "hiz":
            mav.command_long_send(ts, 1, mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED, 0,
                                  0, float(komut[1]), -1, 0, 0, 0, 0)
            self.durum.olay("BILGI", f"Hız değişikliği gönderildi: {komut[1]:.0f} m/s")
        elif komut[0] == "gorev_yukle":
            wps = komut[1]
            if not wps:
                self.durum.olay("HATA", "Görev yüklenemedi: waypoint listesi boş")
                return
            self._yukleme = {"wps": wps, "zaman": mono(), "istek_geldi": False, "tekrar": False}
            mav.mission_count_send(ts, 1, len(wps) + 1)  # +1: seq 0 = ev
            self.durum.olay("BILGI", f"Görev yükleme başladı: {len(wps)} WP (MISSION_COUNT gönderildi)")
        elif komut[0] == "gorev_oku":
            self._okuma = {"beklenen": -1, "ogeler": {}, "zaman": mono()}
            mav.mission_request_list_send(ts, 1)
            self.durum.olay("BILGI", "Görev okuma başladı (MISSION_REQUEST_LIST gönderildi)")

    # ---- görev (waypoint) el sıkışmaları ----
    def _gorev_istek(self, seq: int, int_mi: bool) -> None:
        """Araç yükleme sırasında sıradaki öğeyi istedi."""
        if self._yukleme is None:
            return
        self._yukleme["istek_geldi"] = True
        self._yukleme["zaman"] = mono()
        wps = self._yukleme["wps"]
        mav = self._baglanti.mav
        ts = self._hedef_sistem
        if seq == 0:
            # seq 0 = ev/kalkış noktası (araç kendi ev noktasını korur; konum dolgu değeridir)
            iha = self.durum.iha
            if self.durum.ev_var:
                lat, lon = self.durum.ev_enlem, self.durum.ev_boylam
            else:
                lat, lon = iha.enlem, iha.boylam
            komut_id, p1, frame, irtifa = mavutil.mavlink.MAV_CMD_NAV_WAYPOINT, 0.0, mavutil.mavlink.MAV_FRAME_GLOBAL, 0.0
        else:
            if seq - 1 >= len(wps):
                return
            wp = wps[seq - 1]
            lat, lon, irtifa = float(wp["enlem"]), float(wp["boylam"]), float(wp["irtifa"])
            if wp.get("kalkis"):
                komut_id, p1 = mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, 15.0  # min tırmanış açısı
            else:
                komut_id, p1 = mavutil.mavlink.MAV_CMD_NAV_WAYPOINT, 0.0
            frame = mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT
        if int_mi:
            mav.mission_item_int_send(ts, 1, seq, frame, komut_id, 0, 1,
                                      p1, 0, 0, 0, int(lat * 1e7), int(lon * 1e7), irtifa)
        else:
            mav.mission_item_send(ts, 1, seq, frame, komut_id, 0, 1,
                                  p1, 0, 0, 0, lat, lon, irtifa)

    def _gorev_ack(self, msg) -> None:
        if self._yukleme is None:
            return
        adet = len(self._yukleme["wps"])
        self._yukleme = None
        if msg.type == 0:  # MAV_MISSION_ACCEPTED
            self.durum.olay("BASARI", f"GÖREV YÜKLENDİ: {adet} WP araç tarafından kabul edildi")
        else:
            self.durum.olay("HATA", f"Görev yükleme reddedildi (MAV_MISSION_RESULT={msg.type})")

    def _gorev_sayi(self, adet: int) -> None:
        """Okuma: araç görevindeki öğe sayısını bildirdi."""
        if self._okuma is None:
            return
        self._okuma["beklenen"] = adet
        self._okuma["zaman"] = mono()
        if adet <= 0:
            self._baglanti.mav.mission_ack_send(self._hedef_sistem, 1, 0)
            self.durum.gorev_wps_yaz([])
            self.durum.olay("UYARI", "Araçta kayıtlı görev yok (0 öğe)")
            self._okuma = None
            return
        self._baglanti.mav.mission_request_int_send(self._hedef_sistem, 1, 0)

    def _gorev_oge(self, msg, int_mi: bool) -> None:
        if self._okuma is None:
            return
        self._okuma["zaman"] = mono()
        lat = msg.x / 1e7 if int_mi else float(msg.x)
        lon = msg.y / 1e7 if int_mi else float(msg.y)
        self._okuma["ogeler"][int(msg.seq)] = (int(msg.command), lat, lon, float(msg.z))
        beklenen = self._okuma["beklenen"]
        sonraki = len(self._okuma["ogeler"])
        if sonraki < beklenen:
            self._baglanti.mav.mission_request_int_send(self._hedef_sistem, 1, sonraki)
            return
        # tamamlandı
        self._baglanti.mav.mission_ack_send(self._hedef_sistem, 1, 0)
        wps = []
        for seq in sorted(self._okuma["ogeler"]):
            if seq == 0:
                continue  # ev
            komut_id, lat, lon, irtifa = self._okuma["ogeler"][seq]
            if komut_id in (mavutil.mavlink.MAV_CMD_NAV_WAYPOINT, mavutil.mavlink.MAV_CMD_NAV_TAKEOFF) and (lat or lon):
                wps.append((lat, lon, irtifa))
        self._okuma = None
        self.durum.gorev_wps_yaz(wps)
        self.durum.olay("BASARI", f"GÖREV OKUNDU: {len(wps)} WP araçtan alındı")

    def _gorev_zaman_asimi(self) -> None:
        simdi = mono()
        if self._yukleme is not None and simdi - self._yukleme["zaman"] > 3.0:
            if not self._yukleme["tekrar"]:
                self._yukleme["tekrar"] = True
                self._yukleme["zaman"] = simdi
                self._baglanti.mav.mission_count_send(self._hedef_sistem, 1, len(self._yukleme["wps"]) + 1)
                self.durum.olay("UYARI", "Görev yükleme cevapsız — MISSION_COUNT yeniden gönderildi")
            else:
                self._yukleme = None
                self.durum.olay("HATA", "Görev yükleme ZAMAN AŞIMI (araç cevap vermedi)")
        if self._okuma is not None and simdi - self._okuma["zaman"] > 4.0:
            self._okuma = None
            self.durum.olay("HATA", "Görev okuma ZAMAN AŞIMI (araç cevap vermedi)")


# --------------------------------------------------------------------------
def _quat_euler_derece(x: float, y: float, z: float, w: float) -> tuple[float, float, float]:
    """Kuaterniyon -> (yatış/roll, dikilme/pitch, yönelme/yaw) derece."""
    sinr = 2.0 * (w * x + y * z)
    cosr = 1.0 - 2.0 * (x * x + y * y)
    roll = math.degrees(math.atan2(sinr, cosr))
    sinp = 2.0 * (w * y - z * x)
    sinp = max(-1.0, min(1.0, sinp))
    pitch = math.degrees(math.asin(sinp))
    siny = 2.0 * (w * z + x * y)
    cosy = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.degrees(math.atan2(siny, cosy))
    return roll, pitch, yaw


class RosbridgeKaynak(TelemetriKaynagi):
    AD = "ROS"

    def __init__(self, ayarlar, durum: DurumModeli, oncelik_dusuk: bool = False):
        super().__init__(ayarlar, durum, oncelik_dusuk)
        self._ros = None

    def run(self) -> None:
        if roslibpy is None:
            if not self.oncelik_dusuk:
                self.durum.olay("HATA", "roslibpy kurulu değil (pip install roslibpy) — rosbridge kaynağı kapalı")
            return
        host = str(self.ayarlar.al("baglanti", "rosbridge_host", varsayilan="127.0.0.1"))
        port = int(self.ayarlar.al("baglanti", "rosbridge_port", varsayilan=9090) or 9090)
        while not self._dur.is_set():
            ros = None
            try:
                ros = roslibpy.Ros(host=host, port=port)
                ros.run(timeout=5)
                if not ros.is_connected:
                    raise ConnectionError(f"rosbridge bağlantısı kurulamadı: ws://{host}:{port}")
                self._ros = ros
                self.bagli = True
                self.durum.olay("BASARI", f"rosbridge bağlandı: ws://{host}:{port}")
                self._abonelikler(ros)
                while not self._dur.is_set() and ros.is_connected:
                    self._dur.wait(0.2)
                if not ros.is_connected:
                    self.durum.olay("UYARI", "rosbridge bağlantısı koptu — yeniden denenecek")
            except Exception as e:  # noqa: BLE001
                if not self.oncelik_dusuk:
                    self.durum.olay("HATA", f"rosbridge hata: {e}")
                self._dur.wait(3.0)
            finally:
                self.bagli = False
                self._ros = None
                try:
                    if ros is not None:
                        ros.terminate()
                except Exception:  # noqa: BLE001
                    pass

    # ---- komutlar: MAVROS servisleri üzerinden mod + ARM ----
    def komut_destegi(self) -> bool:
        return True

    def _servis(self, ad: str, tip: str, istek: dict, aciklama: str) -> None:
        if not self.bagli or self._ros is None:
            self.durum.olay("HATA", "rosbridge bağlı değil — komut gönderilemedi")
            return
        try:
            servis = roslibpy.Service(self._ros, ad, tip)
            servis.call(roslibpy.ServiceRequest(istek),
                        lambda sonuc: self.durum.olay("BILGI", f"{aciklama}: {sonuc}"),
                        lambda hata: self.durum.olay("HATA", f"{aciklama} başarısız: {hata}"))
        except Exception as e:  # noqa: BLE001
            self.durum.olay("HATA", f"{aciklama} gönderilemedi: {e}")

    def mod_ayarla(self, mod: str) -> None:
        self._servis("/mavros/set_mode", "mavros_msgs/srv/SetMode",
                     {"base_mode": 0, "custom_mode": mod.upper()}, f"Mod komutu ({mod})")

    def arm(self, aktif: bool) -> None:
        self._servis("/mavros/cmd/arming", "mavros_msgs/srv/CommandBool",
                     {"value": bool(aktif)}, "ARM" if aktif else "DISARM")

    def git(self, enlem: float, boylam: float, irtifa: float) -> None:  # noqa: ARG002
        self.durum.olay("HATA", "'Buraya uç' rosbridge üzerinden desteklenmiyor — MAVLink bağlantısı kullanın")

    def irtifa_degistir(self, irtifa: float) -> None:  # noqa: ARG002
        self.durum.olay("HATA", "İrtifa komutu rosbridge üzerinden desteklenmiyor — MAVLink bağlantısı kullanın")

    def hiz_degistir(self, hiz: float) -> None:  # noqa: ARG002
        self.durum.olay("HATA", "Hız komutu rosbridge üzerinden desteklenmiyor — MAVLink bağlantısı kullanın")

    def _abonelikler(self, ros) -> None:
        def abone(konu: str, tip: str, geri_cagri) -> None:
            roslibpy.Topic(ros, konu, tip, throttle_rate=100).subscribe(geri_cagri)

        def state_cb(m):
            if not self._yazabilir():
                return
            mod = str(m.get("mode", "---")).upper()
            self.durum.iha_guncelle(kaynak=self.AD, mod=mod, armed=bool(m.get("armed", False)), otonom=self._otonom_mu(mod))

        def fix_cb(m):
            if not self._yazabilir():
                return
            self.durum.iha_guncelle(
                kaynak=self.AD,
                enlem=float(m.get("latitude", 0.0) or 0.0),
                boylam=float(m.get("longitude", 0.0) or 0.0),
                irtifa_msl=float(m.get("altitude", 0.0) or 0.0),
            )

        def rel_alt_cb(m):
            if not self._yazabilir():
                return
            self.durum.iha_guncelle(kaynak=self.AD, irtifa=float(m.get("data", 0.0) or 0.0))

        def vfr_cb(m):
            if not self._yazabilir():
                return
            self.durum.iha_guncelle(
                kaynak=self.AD,
                hiz=float(m.get("groundspeed", 0.0) or 0.0),
                hava_hizi=float(m.get("airspeed", 0.0) or 0.0),
                yonelme=float(m.get("heading", 0.0) or 0.0) % 360.0,
            )

        def imu_cb(m):
            if not self._yazabilir():
                return
            q = m.get("orientation") or {}
            roll, pitch, _ = _quat_euler_derece(
                float(q.get("x", 0.0)), float(q.get("y", 0.0)), float(q.get("z", 0.0)), float(q.get("w", 1.0)))
            self.durum.iha_guncelle(kaynak=self.AD, yatis=roll, dikilme=pitch)

        def bat_cb(m):
            if not self._yazabilir():
                return
            yuzde = m.get("percentage")
            if yuzde is not None and yuzde >= 0:
                self.durum.iha_guncelle(kaynak=self.AD, batarya=float(yuzde) * 100.0)

        def zaman_cb(m):
            if not self._yazabilir():
                return
            ref = m.get("time_ref") or {}
            saniye = float(ref.get("sec", 0) or 0)
            if saniye > 1e9:  # geçerli UTC (GPS) zamanı
                ms_gun = int(saniye * 1000 + float(ref.get("nanosec", 0) or 0) / 1e6) % (24 * 3600 * 1000)
                self.durum.iha_guncelle(kaynak=self.AD, gps_utc_ms=ms_gun, gps_utc_kaynak_mono=mono())

        def gps_cb(m):
            if not self._yazabilir():
                return
            durum_alani = m.get("status") or {}
            fix = int(durum_alani.get("status", -1))
            self.durum.iha_guncelle(kaynak=self.AD, gps_fix=3 if fix >= 0 else 0)

        def statustext_cb(m):
            seviye_no = int(m.get("severity", 6))
            seviye = "HATA" if seviye_no <= 3 else ("UYARI" if seviye_no == 4 else "BILGI")
            self.durum.olay(seviye, f"FCU: {m.get('text', '')}")

        abone("/mavros/state", "mavros_msgs/msg/State", state_cb)
        abone("/mavros/global_position/global", "sensor_msgs/msg/NavSatFix", fix_cb)
        abone("/mavros/global_position/rel_alt", "std_msgs/msg/Float64", rel_alt_cb)
        abone("/mavros/vfr_hud", "mavros_msgs/msg/VfrHud", vfr_cb)
        abone("/mavros/imu/data", "sensor_msgs/msg/Imu", imu_cb)
        abone("/mavros/battery", "sensor_msgs/msg/BatteryState", bat_cb)
        abone("/mavros/time_reference", "sensor_msgs/msg/TimeReference", zaman_cb)
        abone("/mavros/global_position/raw/fix", "sensor_msgs/msg/NavSatFix", gps_cb)
        abone("/mavros/statustext/recv", "mavros_msgs/msg/StatusText", statustext_cb)


# --------------------------------------------------------------------------
class SimKaynak(TelemetriKaynagi):
    """Donanımsız geliştirme/benzetim: komutlara uyan sanal sabit kanat.

    Verilen modda uçar; GUIDED hedefine gider ve üzerinde tur atar,
    RTL'de merkeze döner, TAKEOFF'ta tırmanır, ARM/DISARM'a uyar.
    """

    AD = "SIM"

    def __init__(self, ayarlar, durum: DurumModeli, oncelik_dusuk: bool = False):
        super().__init__(ayarlar, durum, oncelik_dusuk)
        self.mod = "AUTO"
        self.otonom = True
        self.armed = True
        self._hedef: tuple[float, float] | None = None
        self._hedef_irtifa = 100.0
        self._hedef_hiz = 22.0
        self._kilit_sim = False
        self._gorev: list = []            # [(enlem, boylam, irtifa)]
        self._gorev_indeks = 0

    # ---- komut arayüzü ----
    def komut_destegi(self) -> bool:
        return True

    def mod_ayarla(self, mod: str) -> None:
        self.mod = mod.upper()
        self.otonom = self._otonom_mu(self.mod)
        if self.mod != "GUIDED":
            self._hedef = None
        self.durum.olay("BASARI", f"SİM: mod {self.mod} olarak ayarlandı")

    def arm(self, aktif: bool) -> None:
        self.armed = bool(aktif)
        self.durum.olay("BASARI", "SİM: ARM" if aktif else "SİM: DISARM")

    def git(self, enlem: float, boylam: float, irtifa: float) -> None:
        self.mod = "GUIDED"
        self.otonom = self._otonom_mu("GUIDED")
        self._hedef = (float(enlem), float(boylam))
        self._hedef_irtifa = float(irtifa)
        self.durum.hedef_ayarla(enlem, boylam, irtifa)
        self.durum.olay("BASARI", f"SİM: BURAYA UÇ {enlem:.6f}, {boylam:.6f} @ {irtifa:.0f} m")

    def irtifa_degistir(self, irtifa: float) -> None:
        self._hedef_irtifa = float(irtifa)
        self.durum.olay("BASARI", f"SİM: hedef irtifa {irtifa:.0f} m")

    def hiz_degistir(self, hiz: float) -> None:
        self._hedef_hiz = float(hiz)
        self.durum.olay("BASARI", f"SİM: hedef hız {hiz:.0f} m/s")

    def gorev_yukle(self, wps: list) -> None:
        self._gorev = [(float(w["enlem"]), float(w["boylam"]), float(w["irtifa"])) for w in wps]
        self._gorev_indeks = 0
        self.durum.olay("BASARI", f"SİM: görev yüklendi ({len(self._gorev)} WP) — AUTO modda uçulur")

    def gorev_oku(self) -> None:
        self.durum.gorev_wps_yaz(self._gorev)
        self.durum.olay("BASARI", f"SİM: görev okundu ({len(self._gorev)} WP)")

    # eski araç çubuğu düğmeleriyle uyumluluk
    def mod_degistir(self, otonom: bool) -> None:
        self.mod_ayarla("AUTO" if otonom else "FBWA")

    def kilit_sim_degistir(self, aktif: bool) -> None:
        self._kilit_sim = aktif

    # ---- benzetim döngüsü ----
    def run(self) -> None:
        merkez = self.ayarlar.al("harita", "merkez", varsayilan=[40.2306, 29.0060])
        merkez_lat, merkez_lon = float(merkez[0]), float(merkez[1])
        lat, lon = merkez_lat, merkez_lon - 260.0 / (111319.49 * math.cos(math.radians(merkez_lat)))
        yon = 0.0
        hiz = 22.0
        irtifa = 100.0
        batarya = 100.0
        t = 0.0
        dt = 0.1
        self.bagli = True
        self.durum.olay("BILGI", "SİM kaynağı çalışıyor (benzetim uçuşu — Eylemler sekmesi komutlarına uyar)")
        while not self._dur.is_set():
            t += dt

            # --- istenen rota ---
            if self.mod == "GUIDED" and self._hedef:
                mesafe = geo.haversine_m(lat, lon, self._hedef[0], self._hedef[1])
                kerteriz = geo.kerteriz_derece(lat, lon, self._hedef[0], self._hedef[1])
                istenen_yon = kerteriz if mesafe > 90.0 else (kerteriz + 85.0) % 360.0  # varınca tur at
            elif self.mod == "RTL":
                mesafe = geo.haversine_m(lat, lon, merkez_lat, merkez_lon)
                kerteriz = geo.kerteriz_derece(lat, lon, merkez_lat, merkez_lon)
                istenen_yon = kerteriz if mesafe > 90.0 else (kerteriz + 85.0) % 360.0
            elif self.mod in ("MANUAL", "FBWA", "FBWB", "STABILIZE", "CRUISE", "ACRO"):
                istenen_yon = (yon + 6.0 * math.sin(t / 6.0)) % 360.0  # hafif dolaşma
            elif self.mod == "AUTO" and self._gorev:
                wp = self._gorev[self._gorev_indeks]
                mesafe = geo.haversine_m(lat, lon, wp[0], wp[1])
                istenen_yon = geo.kerteriz_derece(lat, lon, wp[0], wp[1])
                self._hedef_irtifa = wp[2]
                if mesafe < 70.0:
                    self._gorev_indeks = (self._gorev_indeks + 1) % len(self._gorev)
            else:  # AUTO(görevsiz)/LOITER/CIRCLE/TAKEOFF...: merkez etrafında 260 m tur
                mesafe = geo.haversine_m(lat, lon, merkez_lat, merkez_lon)
                kerteriz = geo.kerteriz_derece(lat, lon, merkez_lat, merkez_lon)
                duzeltme = max(-45.0, min(45.0, (mesafe - 260.0) * 0.35))
                istenen_yon = (kerteriz + 90.0 - duzeltme) % 360.0

            # --- dinamikler ---
            if self.armed:
                fark = (istenen_yon - yon + 540.0) % 360.0 - 180.0
                donus_hizi = max(-24.0, min(24.0, fark * 1.2))   # derece/sn (dönüş limiti)
                donus = donus_hizi * dt
                yon = (yon + donus) % 360.0
                hiz += max(-2.0 * dt, min(2.0 * dt, self._hedef_hiz - hiz))
                hedef_irt = self._hedef_irtifa if self.mod != "TAKEOFF" else max(self._hedef_irtifa, 120.0)
                tirmanma = max(-3.0 * dt, min(3.0 * dt, hedef_irt - irtifa))
                irtifa += tirmanma
                batarya = max(5.0, batarya - dt * 0.04)
                m_per_deg_lon = 111319.49 * math.cos(math.radians(lat))
                lat += hiz * math.cos(math.radians(yon)) * dt / 111319.49
                lon += hiz * math.sin(math.radians(yon)) * dt / m_per_deg_lon
                yatis = max(-45.0, min(45.0, donus_hizi * 1.8))
                dikilme = max(-20.0, min(20.0, (tirmanma / dt) * 5.0)) if dt else 0.0
            else:
                hiz = max(0.0, hiz - 6.0 * dt)
                irtifa = max(0.0, irtifa - 4.0 * dt)
                yatis = 0.0
                dikilme = 0.0

            simdi = datetime.now(timezone.utc)
            ms_gun = ((simdi.hour * 60 + simdi.minute) * 60 + simdi.second) * 1000 + simdi.microsecond // 1000
            self.durum.iha_guncelle(
                kaynak=self.AD,
                enlem=lat, boylam=lon,
                irtifa=irtifa, irtifa_msl=irtifa + 80.0,
                dikilme=dikilme + 2.0 * math.sin(t / 5.0),
                yatis=yatis + 4.0 * math.sin(t / 7.0),
                yonelme=yon,
                hiz=hiz, hava_hizi=hiz + 1.5 + 0.8 * math.sin(t / 3.0),
                batarya=batarya,
                mod=self.mod, otonom=self.otonom, armed=self.armed,
                gps_fix=3, gps_uydu=14,
                gps_utc_ms=ms_gun, gps_utc_kaynak_mono=mono(),
            )
            if self._kilit_sim:
                self.durum.kilit_guncelle(
                    aktif=True,
                    merkez_x=int(320 + 60 * math.sin(t * 1.7)),
                    merkez_y=int(240 + 40 * math.cos(t * 1.3)),
                    genislik=int(60 + 12 * math.sin(t)),
                    yukseklik=int(44 + 8 * math.cos(t)),
                    video_genislik=640, video_yukseklik=480,
                )
            self._dur.wait(dt)


# --------------------------------------------------------------------------
def kaynaklari_olustur(ayarlar, durum: DurumModeli) -> list[TelemetriKaynagi]:
    secim = str(ayarlar.al("baglanti", "kaynak", varsayilan="otomatik")).lower()
    if secim == "sim":
        return [SimKaynak(ayarlar, durum)]
    if secim == "mavlink":
        return [MavlinkKaynak(ayarlar, durum)]
    if secim == "rosbridge":
        return [RosbridgeKaynak(ayarlar, durum)]
    # otomatik: MAVLink birincil, rosbridge yedek (MAVLink tazeyken yazmaz)
    return [MavlinkKaynak(ayarlar, durum), RosbridgeKaynak(ayarlar, durum, oncelik_dusuk=True)]


__all__ = [
    "TelemetriKaynagi", "MavlinkKaynak", "RosbridgeKaynak", "SimKaynak",
    "kaynaklari_olustur", "PLANE_MODLARI",
]
