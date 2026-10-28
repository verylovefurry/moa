"""Shared visual values for the native light desktop interface."""
GREEN = "#365f4c"
TEXT = "#26372e"
MUTED = "#5f7065"
LINE = "#dce4de"
PAGE_MARGINS = (20, 20, 20, 16)
PAGE_SPACING = 12
ICON_SIZE = 16

STYLE = f"""
QMainWindow, QDialog {{ background: #ffffff; }}
QPushButton {{ min-height: 26px; padding: 1px 10px; }}
QToolButton {{ min-height: 26px; padding: 1px 8px; }}
QComboBox, QLineEdit, QDateEdit, QTimeEdit, QSpinBox {{ min-height: 26px; }}
QLabel[muted="true"] {{ color: {MUTED}; }}
QWidget#navigationPane {{ background: #f4f7f5; border-right: 1px solid {LINE}; }}
QListWidget#navigation {{ border: 0; background: transparent; outline: 0; }}
QListWidget#navigation::item {{ padding: 7px 10px; border-left: 2px solid transparent; color: #46594c; }}
QListWidget#navigation::item:selected {{ background: #e3ece6; color: #244b35; border-left-color: {GREEN}; font-weight: 600; }}
QListWidget#navigation::item:hover:!selected {{ background: #eaf0ec; }}
QListWidget#navigation::item:selected:focus {{ border-left-color: #153a24; }}
QPushButton[primary="true"] {{ background: {GREEN}; color: #ffffff; border: 1px solid {GREEN}; border-radius: 4px; }}
QPushButton[primary="true"]:hover {{ background: #2c513e; }}
QPushButton[primary="true"]:pressed {{ background: #224331; }}
QPushButton[primary="true"]:focus {{ border: 2px solid #99b4a2; padding: 0px 9px; }}
QPushButton[primary="true"]:disabled {{ background: #dfe6e1; color: #7c8c81; border-color: #dfe6e1; }}
QPushButton[destructive="true"] {{ color: #99483f; }}
QPushButton[destructive="true"]:disabled {{ color: #9aa49d; }}
QPushButton[monthNavigation="true"] {{ padding: 1px 2px; }}
QPushButton#monthPicker {{ font-weight: 600; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; border: 0; border-bottom: 2px solid transparent; padding: 5px 11px; min-height: 22px; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom-color: {GREEN}; font-weight: 600; }}
QTabBar::tab:hover:!selected {{ background: #f2f6f3; }}
QTabBar::tab:focus {{ background: #eaf1ec; }}
QTabWidget::pane {{ border: 1px solid {LINE}; }}
QTableWidget {{ background: #ffffff; border: 1px solid {LINE}; selection-background-color: #e5eee8; selection-color: #203e2b; }}
QTableWidget:focus {{ border-color: #789683; }}
QHeaderView::section {{ background: #f6f8f6; color: {MUTED}; border: 0; border-bottom: 1px solid {LINE}; padding: 7px 9px; }}
QTableWidget#workCalendar QHeaderView::section {{ padding: 5px 0; }}
QTableWidget::item {{ padding-left: 9px; padding-right: 9px; }}
QLineEdit:read-only {{ background: #f7f9f7; color: {MUTED}; }}
QProgressBar {{ background: #e5ece7; border: 0; max-height: 4px; min-height: 4px; }}
QProgressBar::chunk {{ background: #729480; }}
QSplitter::handle:horizontal {{ background: #ffffff; width: 12px; }}
QSplitter::handle:vertical {{ background: #ffffff; height: 10px; }}
QStatusBar {{ background: #f6f8f6; border-top: 1px solid {LINE}; color: {MUTED}; }}
QStatusBar::item {{ border: 0; }}
QFrame[divider="true"] {{ color: {LINE}; }}
QToolTip {{ color: #ffffff; background: #2e4235; border: 0; padding: 5px 7px; }}
"""
