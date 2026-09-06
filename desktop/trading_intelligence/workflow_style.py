"""Restrained native adaptation of trading_glass_theme.py's web product palette."""
STYLESHEET = """
QWidget { font-family: "Helvetica Neue"; font-size: 13px; color: #f2f6fb; background: #07111d; }
QLabel { background: transparent; }
QFrame#Sidebar { background: #050c15; border-right: 1px solid #1c2b3b; }
QFrame#Card { background: #0a1726; border: 1px solid #1a2c3f; border-radius: 10px; }
QLabel#PageTitle { font-size: 25px; font-weight: 700; }
QLabel#SectionTitle, QLabel#BannerTitle { font-size: 16px; font-weight: 600; }
QLabel#Subtle, QLabel#BrandSub { color: #8ea2b9; }
QLabel#Eyebrow, QLabel#MetricCaption { color: #8ea2b9; font-size: 11px; font-weight: 600; }
QLabel#MetricValue { font-size: 23px; font-weight: 700; }
QLabel#ContextTicker { font-size: 20px; font-weight: 700; }
QLabel#ContextStrategy { color: #d7e1eb; font-size: 13px; }
QLabel#ValidationVerdict { font-size: 24px; font-weight: 700; color: #e96775; }
QLabel#ValidationStrength { font-size: 20px; font-weight: 700; color: #dcb667; }
QLabel#EvidenceSummary { font-size: 15px; color: #d7e1eb; padding: 10px 0; }
QPushButton { padding: 8px 12px; min-height: 22px; border: 1px solid #294057; border-radius: 7px; }
QPushButton#WorkflowNav { text-align: left; border: 0; background: transparent; color: #8ea2b9; padding: 8px 10px; }
QPushButton#WorkflowNav[active="true"] { color: #65dba0; background: #102c26; }
QPushButton#Primary { color: #06140f; background: #43d17d; border: 0; font-weight: 700; }
QPushButton:disabled, QPushButton#Primary:disabled { background: #0a1726; color: #607287; border: 1px solid #1a2c3f; }
QToolButton { text-align: left; color: #8ea2b9; border: 0; padding: 8px 2px; min-height: 22px; }
QToolButton:hover { color: #f2f6fb; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { min-height: 24px; padding: 6px; background: #050c15; border: 1px solid #294057; border-radius: 6px; }
QTableWidget { background: #0a1726; alternate-background-color: #0d1d2e; border: 0; selection-background-color: #164438; selection-color: #f2f6fb; }
QHeaderView::section { background: #102033; color: #d7e1eb; padding: 8px; border: 0; font-weight: 600; }
QScrollBar:vertical { background: #07111d; width: 14px; margin: 0; }
QScrollBar::handle:vertical { background: #405770; min-height: 38px; border-radius: 6px; margin: 2px; }
QScrollBar::handle:vertical:hover { background: #6e8ca9; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QTabBar::tab { padding: 9px 14px; border: 0; color: #8ea2b9; background: #07111d; }
QTabBar::tab:selected { color: #65dba0; border-bottom: 2px solid #43d17d; }
"""
