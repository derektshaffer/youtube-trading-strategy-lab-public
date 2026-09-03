"""Shared visual style for the production desktop shell."""

STYLESHEET = """
QMainWindow, QWidget { background: #07101c; color: #eef6ff; }
QFrame#Sidebar { background:#08131f; border-right:1px solid #22364c; }
QFrame#Card { background:#0d1929; border:1px solid #293e58; border-radius:14px; }
QFrame#Card[state="error"] { border-color:#874552; background:#281720; }
QFrame#Card[state="ready"] { border-color:#2d725b; }
QLabel#Brand { font-size:20px; font-weight:850; }
QLabel#BrandSub, QLabel#Subtle { color:#91a7bd; }
QLabel#Eyebrow, QLabel#MetricCaption { color:#70d7ff; font-size:10px; font-weight:850; }
QLabel#PageTitle { font-size:25px; font-weight:850; }
QLabel#SectionTitle, QLabel#BannerTitle { font-size:15px; font-weight:820; }
QLabel#MetricValue { font-size:22px; font-weight:850; }
QLabel#FormLabel { color:#9db0c4; font-weight:700; }
QPushButton { background:#101f32; color:#afc2d5; border:1px solid #2a425d; border-radius:8px; padding:8px 11px; font-weight:700; }
QPushButton:hover { border-color:#4c7396; color:#eef7ff; }
QPushButton:checked { background:#17344c; color:#8ee4ff; border-color:#4b7898; }
QPushButton#Primary { background:#58d9ae; color:#06140f; border:0; padding:10px 14px; font-weight:850; }
QPushButton:disabled { color:#536579; background:#0b1725; border-color:#1a2b3d; }
QPushButton#Primary:disabled { color:#536579; background:#0b1725; border:1px solid #1a2b3d; }
QLineEdit, QComboBox { background:#081521; border:1px solid #2a425d; border-radius:7px; padding:8px; color:#edf6ff; }
QTableWidget { background:#091522; alternate-background-color:#0c1a29; border:0; gridline-color:#1b3044; selection-background-color:#173e56; }
QHeaderView::section { background:#0b1b2b; color:#8fa5bb; border:0; border-bottom:1px solid #294057; padding:8px; font-weight:750; }
QProgressBar { border:1px solid #2a425d; border-radius:7px; background:#08131f; min-height:13px; text-align:center; }
QProgressBar::chunk { background:#58d9ae; border-radius:6px; }
"""
