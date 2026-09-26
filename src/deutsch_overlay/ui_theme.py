"""Scoped visual language for the settings window."""

SETTINGS_STYLE = """
QWidget#settingsRoot { background: #F6F7FB; color: #172033; font-family: 'Segoe UI Variable', 'Segoe UI'; font-size: 13px; }
QWidget#sidebar { background: #202631; }
QWidget#contentPane, QWidget#pageBody { background: #F6F7FB; }
QWidget#footer { background: #FFFFFF; border-top: 1px solid #E8EBF0; }
QLabel { color: #182234; background: transparent; }
QLabel#brand { color: #FFFFFF; font-size: 17px; font-weight: 700; }
QLabel#brandSubtitle { color: #A8B2C3; font-size: 11px; }
QLabel#sectionLabel { color: #667184; font-size: 11px; font-weight: 700; }
QLabel#pageTitle { color: #152033; font-size: 26px; font-weight: 700; }
QLabel#pageDescription { color: #69758A; font-size: 12px; }
QLabel#cardTitle { color: #1A2638; font-size: 15px; font-weight: 700; }
QLabel#hint { color: #6D7889; font-size: 11px; }
QLabel#fieldLabel { color: #344256; font-weight: 600; }
QLabel#statusLabel { color: #536176; font-size: 11px; }
QLabel#sidebarHint { color: #9AA6B8; font-size: 11px; }
QFrame#card { background: #FFFFFF; border: 1px solid #E7EAF0; border-radius: 15px; }
QPushButton { background: #FFFFFF; border: 1px solid #DDE3EB; border-radius: 8px; color: #26364C; min-height: 34px; padding: 0 12px; font-weight: 600; }
QPushButton:hover { background: #F4F7FC; border-color: #B7C5D8; }
QPushButton:focus { border: 2px solid #4E91EC; }
QPushButton#primaryButton { background: #2878E4; color: #FFFFFF; border: 1px solid #2878E4; }
QPushButton#primaryButton:hover { background: #1766D3; }
QPushButton#navButton { background: transparent; color: #BCC7D8; border: none; border-radius: 9px; text-align: left; padding-left: 14px; min-height: 40px; }
QPushButton#navButton:hover { background: #313A49; color: #FFFFFF; }
QPushButton#navButton:checked { background: #354C70; color: #FFFFFF; }
QPushButton#sideAction { background: transparent; color: #D1DBE9; border: 1px solid #445064; text-align: left; }
QPushButton#sideAction:hover { background: #313A49; }
QPushButton#choiceButton { background: #F8FAFD; border: 1px solid #E0E6EF; color: #4B5B71; min-height: 38px; }
QPushButton#choiceButton:hover { background: #EDF4FD; border-color: #A8C5ED; }
QPushButton#choiceButton:checked { background: #E7F0FE; border: 1px solid #3E84E5; color: #145DB8; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { background: #F8FAFC; color: #1E2A3B; border: 1px solid #DCE3EB; border-radius: 8px; min-height: 34px; padding: 0 10px; selection-background-color: #CEE2FF; }
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover { border-color: #AFC3DD; }
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus { border: 2px solid #4E91EC; }
QComboBox QAbstractItemView { background: #FFFFFF; color: #1E2A3B; border: 1px solid #DCE3EB; selection-background-color: #E7F0FE; selection-color: #145DB8; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 0; }
QScrollBar::handle:vertical { background: #CDD5E0; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""
