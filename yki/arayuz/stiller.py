"""Koyu tema renkleri ve uygulama stil sayfası.

Palet göz yormayacak şekilde yumuşatıldı: doygunluğu düşük vurgu renkleri,
ince kenarlıklar, neon yerine pastel tonlar. Alarm renkleri yine belirgindir.
"""

ARKA = "#12151a"
PANEL = "#181d24"
PANEL_ACIK = "#20262f"
KENAR = "#323b46"
YAZI = "#d5dce3"
YAZI_SOLUK = "#8b96a2"

VURGU = "#5aa7d6"      # kendi İHA / başlıklar (yumuşak çelik mavi)
IYI = "#5fae78"
UYARI = "#c9a45c"
HATA = "#d4655f"
RAKIP = "#d98f56"
HSS = "#cf5b5b"
QR = "#79b884"
SINIR = "#d3ba5e"

STIL = f"""
QMainWindow, QWidget {{ background: {ARKA}; color: {YAZI}; font-size: 13px; }}
QGroupBox {{
    background: {PANEL}; border: 1px solid {KENAR}; border-radius: 8px;
    margin-top: 12px; padding: 8px 6px 6px 6px; font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {VURGU};
}}
QLabel {{ background: transparent; }}
QLabel[rol="deger"] {{ font-family: Consolas, 'DejaVu Sans Mono', monospace; font-size: 14px; font-weight: bold; }}
QLabel[rol="soluk"] {{ color: {YAZI_SOLUK}; }}
QPushButton {{
    background: {PANEL_ACIK}; border: 1px solid {KENAR}; border-radius: 6px;
    padding: 6px 10px; font-weight: bold;
}}
QPushButton:hover {{ border-color: {VURGU}; }}
QPushButton:pressed {{ background: #262e39; }}
QPushButton:disabled {{ color: #5a6672; }}
QPushButton[rol="tehlike"] {{ background: #34211f; border-color: {HATA}; color: #e8b7b3; }}
QPushButton[rol="onay"] {{ background: #1d2b22; border-color: {IYI}; color: #bcd9c5; }}
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {{
    background: {PANEL_ACIK}; border: 1px solid {KENAR}; border-radius: 5px; padding: 4px 6px;
    selection-background-color: {VURGU}; selection-color: black;
}}
QCheckBox::indicator {{ width: 16px; height: 16px; }}
QTabWidget::pane {{ border: 1px solid {KENAR}; border-radius: 6px; background: {PANEL}; }}
QTabBar::tab {{
    background: {PANEL}; border: 1px solid {KENAR}; padding: 6px 12px;
    border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; font-weight: bold;
    color: {YAZI_SOLUK};
}}
QTabBar::tab:selected {{ background: {PANEL_ACIK}; color: {YAZI}; }}
QTableWidget {{
    background: {PANEL}; gridline-color: {KENAR}; border: none;
    alternate-background-color: {PANEL_ACIK};
}}
QHeaderView::section {{
    background: {PANEL_ACIK}; color: {YAZI_SOLUK}; border: none; padding: 4px; font-weight: bold;
}}
QPlainTextEdit {{
    background: #101318; border: 1px solid {KENAR}; border-radius: 6px;
    font-family: Consolas, 'DejaVu Sans Mono', monospace; font-size: 12px;
}}
QProgressBar {{
    background: {PANEL_ACIK}; border: 1px solid {KENAR}; border-radius: 5px;
    text-align: center; color: {YAZI}; font-weight: bold;
}}
QProgressBar::chunk {{ background: {VURGU}; border-radius: 4px; }}
QToolBar {{ background: {PANEL}; border-bottom: 1px solid {KENAR}; spacing: 6px; padding: 4px; }}
QStatusBar {{ background: {PANEL}; border-top: 1px solid {KENAR}; color: {YAZI_SOLUK}; }}
QToolTip {{ background: {PANEL_ACIK}; color: {YAZI}; border: 1px solid {VURGU}; }}
QScrollBar:vertical {{ background: {PANEL}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {KENAR}; border-radius: 5px; min-height: 30px; }}
QMenu {{ background: {PANEL_ACIK}; border: 1px solid {KENAR}; }}
QMenu::item:selected {{ background: {KENAR}; }}
"""
