"""Şartname kurallarının canlı takibi.

- Müsabaka süresi (15 dk) ve otonomi yüzdesi (puan için >= %75)
- Manuel moda geçiş sayacı (tur başına limit 3, aşan her geçiş -10)
- Yarışma sınırı ihlali (tek seferde 10 sn = eleme/-200; 4. kısa çıkış da ceza)
- Min/maks irtifa ihlali (sınır dışı sayılır)
- HSS bölge ihlali (saniye başına -5; toplam 30 sn = iniş çağrısı)
- FCU ve sunucu telemetri sağlık göstergeleri (10 sn kopukluk = ihraç riski)

Saf mantık — arayüzden bağımsız, test edilebilir.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import geo
from .durum import DurumModeli, mono


@dataclass
class KuralOzeti:
    musabaka_aktif: bool = False
    gecen_sn: float = 0.0
    kalan_sn: float = 0.0
    otonom_sn: float = 0.0
    otonomi_yuzde: float = 100.0
    otonomi_yeterli: bool = True
    manuel_gecis: int = 0
    manuel_limit: int = 3
    sinir_icinde: bool = True
    sinir_disi_sure: float = 0.0          # anlık kesintisiz dışarıda kalma süresi
    sinir_disi_sayisi: int = 0            # kısa süreli dışarı çıkış adedi
    sinir_eleme_riski: bool = False
    irtifa_ihlal: str = ""                # "" | "ALÇAK" | "YÜKSEK"
    hss_icinde: bool = False
    hss_aktif_id: int = -1
    hss_ihlal_toplam_sn: float = 0.0
    hss_ceza_tahmini: float = 0.0
    fcu_yas_sn: float = 999.0
    fcu_ok: bool = False
    sunucu_telemetri_yas_sn: float = 999.0
    sunucu_kopuk_riski: bool = False


class KuralTakip:
    def __init__(self, ayarlar):
        self.ayarlar = ayarlar
        self.ozet = KuralOzeti()
        self._son_tick: float | None = None
        self._onceki_otonom: bool | None = None
        self._onceki_icinde: bool = True
        self._basla_mono: float = 0.0

    # ---- müsabaka kontrolü ----
    def musabaka_baslat(self) -> None:
        self.ozet = KuralOzeti(musabaka_aktif=True, manuel_limit=int(self.ayarlar.al("musabaka", "manuel_gecis_limiti", varsayilan=3)))
        self._son_tick = None
        self._onceki_otonom = None
        self._onceki_icinde = True
        self._basla_mono = mono()

    def musabaka_durdur(self) -> None:
        self.ozet.musabaka_aktif = False

    def sifirla(self) -> None:
        self.ozet = KuralOzeti()
        self._son_tick = None
        self._onceki_otonom = None

    # ---- periyodik güncelleme ----
    def tick(self, durum: DurumModeli) -> KuralOzeti:
        """Arayüz zamanlayıcısından ~10 Hz çağrılır; anlık durum kopyası bekler."""
        simdi = mono()
        dt = 0.0
        if self._son_tick is not None:
            dt = max(0.0, min(1.0, simdi - self._son_tick))
        self._son_tick = simdi

        o = self.ozet
        iha = durum.iha
        fcu_taze = iha.taze(2.0)

        # --- FCU / sunucu sağlık ---
        o.fcu_yas_sn = (simdi - iha.guncelleme_mono) if iha.guncelleme_mono else 999.0
        o.fcu_ok = fcu_taze
        son_tel = durum.sunucu.son_telemetri_mono
        o.sunucu_telemetri_yas_sn = (simdi - son_tel) if son_tel else 999.0
        o.sunucu_kopuk_riski = durum.sunucu.telemetri_aktif and o.sunucu_telemetri_yas_sn > 5.0

        # --- müsabaka sayaçları ---
        sure_dk = float(self.ayarlar.al("musabaka", "sure_dk", varsayilan=15.0) or 15.0)
        if o.musabaka_aktif:
            o.gecen_sn += dt
            o.kalan_sn = max(0.0, sure_dk * 60.0 - o.gecen_sn)
            if fcu_taze and iha.otonom:
                o.otonom_sn += dt
            if o.gecen_sn > 0:
                o.otonomi_yuzde = 100.0 * o.otonom_sn / o.gecen_sn
            hedef = float(self.ayarlar.al("musabaka", "otonomi_hedef_yuzde", varsayilan=75.0) or 75.0)
            o.otonomi_yeterli = o.otonomi_yuzde >= hedef
        else:
            o.kalan_sn = max(0.0, sure_dk * 60.0 - o.gecen_sn)

        # --- manuel geçiş sayacı (yalnız müsabaka sırasında ve FCU tazeyken) ---
        if fcu_taze:
            if self._onceki_otonom is not None and o.musabaka_aktif:
                if self._onceki_otonom and not iha.otonom:
                    o.manuel_gecis += 1
            self._onceki_otonom = iha.otonom

        # --- yarışma sınırı ---
        sinir = self.ayarlar.sinir
        if fcu_taze and (iha.enlem or iha.boylam):
            icinde = geo.nokta_poligon_icinde(iha.enlem, iha.boylam, sinir)
            # irtifa bandı da sınırın parçası (şartname 7.2.2)
            min_irt = float(self.ayarlar.al("harita", "min_irtifa", varsayilan=0.0) or 0.0)
            maks_irt = float(self.ayarlar.al("harita", "maks_irtifa", varsayilan=100000.0) or 100000.0)
            o.irtifa_ihlal = ""
            if iha.armed and iha.irtifa > 5.0:  # yerdeyken irtifa alarmı üretme
                if iha.irtifa < min_irt:
                    o.irtifa_ihlal = "ALÇAK"
                elif iha.irtifa > maks_irt:
                    o.irtifa_ihlal = "YÜKSEK"
            ihlal_var = (not icinde) or bool(o.irtifa_ihlal)
            o.sinir_icinde = not ihlal_var
            if ihlal_var:
                o.sinir_disi_sure += dt
            else:
                if not self._onceki_icinde and o.sinir_disi_sure > 0.3:
                    o.sinir_disi_sayisi += 1  # kısa süreli çıkış tamamlandı
                o.sinir_disi_sure = 0.0
            self._onceki_icinde = not ihlal_var
            limit = float(self.ayarlar.al("musabaka", "sinir_disi_limit_sn", varsayilan=10.0) or 10.0)
            o.sinir_eleme_riski = o.sinir_disi_sure >= limit or o.sinir_disi_sayisi >= 4

        # --- HSS ihlali ---
        o.hss_icinde = False
        o.hss_aktif_id = -1
        if fcu_taze and durum.hss_listesi and (iha.enlem or iha.boylam):
            for bolge in durum.hss_listesi:
                if geo.daire_icinde(iha.enlem, iha.boylam, bolge.enlem, bolge.boylam, bolge.yaricap):
                    o.hss_icinde = True
                    o.hss_aktif_id = bolge.id
                    break
            if o.hss_icinde:
                o.hss_ihlal_toplam_sn += dt
                o.hss_ceza_tahmini = -5.0 * int(o.hss_ihlal_toplam_sn)
        return o
