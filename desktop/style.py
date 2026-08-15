STYLE = """
QWidget { font-family: "Microsoft YaHei UI"; font-size: 14px; color: #3b2b3f; background: #fff8fb; }
#brandPanel { background: #3b2b3f; }
#brandLeaf { font-size: 48px; color: white; background: transparent; }
#brandTitle { font-size: 38px; font-weight: 800; color: white; background: transparent; }
#brandSubtitle { font-size: 17px; line-height: 1.6; color: #f1dce8; background: transparent; }
#formHeading { font-size: 30px; font-weight: 800; margin-bottom: 4px; }
QLineEdit { border: 1px solid #edbfd2; border-radius: 10px; padding: 0 14px; background: #fffdfd; selection-background-color: #c95f8d; }
QLineEdit:focus { border: 2px solid #c95f8d; }
QPushButton { min-height: 42px; border: 1px solid #e7b8cc; border-radius: 10px; padding: 0 16px; background: #fffdfd; font-weight: 650; }
QPushButton:hover { background: #ffeaf3; border-color: #db86aa; }
#primaryButton { min-height: 50px; color: white; background: #c95f8d; border: 0; }
#primaryButton:hover { background: #ae4d78; }
#primaryButton:pressed { background: #963b66; }
#recordingButton { min-height: 46px; color: white; background: #d94f76; border: 0; }
#recordingButton:hover { background: #bd3f65; }
#secondaryButton { color: #8d3f62; background: #fff0f6; border: 1px solid #e6abc5; }
#secondaryButton:hover { background: #fbdfe9; border-color: #d99ab6; }
#dangerButton { color: #9d2e3b; background: #fff1f1; border: 1px solid #e4a5aa; }
#dangerButton:hover { color: #7f1d2d; background: #ffe2e4; border-color: #d88790; }
#linkButton { border: 0; color: #a94f79; background: transparent; }
#sidebar { min-width: 230px; max-width: 230px; background: #3b2b3f; }
#sideLogo { font-size: 22px; font-weight: 800; color: white; background: transparent; margin-bottom: 16px; }
#navigation { border: 0; background: transparent; color: #f3dce8; outline: 0; }
#navigation::item { padding: 13px 14px; margin: 3px 0; border-radius: 9px; }
#navigation::item:hover { background: #50364d; color: white; }
#navigation::item:selected { background: #c95f8d; color: white; }
#accountBadge { color: #faeaf1; background: #533b54; border-radius: 10px; padding: 12px; }
#pageHeading { font-size: 28px; font-weight: 800; color: #3b2b3f; }
#pageSubtitle { color: #786572; }
#metricCard, #explanationCard { border: 1px solid #d9d3c8; border-radius: 15px; background: #fffdf8; padding: 18px; }
#overviewMetrics { border: 0; background: transparent; padding: 0; }
#overviewMetrics #metricCard { background: #fffdf8; border-color: #e4ddd1; }
#overviewMetrics #metricCard:nth-child(2) { background: #f3f8f4; }
#overviewMetrics #metricCard:nth-child(3) { background: #fff7ee; }
#overviewMetrics #metricCard:nth-child(4) { background: #fff0f5; border-color: #edc2d3; }
#overviewMetrics #metricCard:nth-child(4) #metricValue { color: #a94f79; }
#overviewTaskSection { border: 1px solid #edc2d3; border-radius: 18px; background: #fffdfb; padding: 18px; }
#sectionHeading { font-size: 18px; font-weight: 750; color: #8d3f62; }
#metricValue { font-size: 21px; font-weight: 800; color: #c95f8d; }
QTableWidget { border: 1px solid #edc2d3; border-radius: 12px; background: white; gridline-color: #f1dce5; }
QHeaderView::section { background: #fff0f5; color: #8d3f62; border: 0; padding: 10px; font-weight: 700; }
  #taskList, #overviewTaskList { border: 0; background: transparent; outline: 0; }
  #taskList::item, #overviewTaskList::item { background: transparent; border: 0; padding: 2px; margin: 2px 0; }
  #taskList::item:selected, #overviewTaskList::item:selected { background: transparent; color: #245443; }
  #taskCard QToolButton { min-height: 32px; padding: 0 10px; background: rgba(255,255,255,0.7); border: 1px solid #c8d3cd; border-radius: 8px; }
  #taskCard QToolButton:hover { background: #fff0f5; border-color: #edc2d3; }
#evidencePanel { min-width: 300px; background: #fffdf8; border: 1px solid #d9d3c8; border-radius: 12px; }
QComboBox { min-height: 42px; border: 1px solid #edbfd2; border-radius: 9px; padding: 0 12px; background: white; }
QComboBox:disabled, QSpinBox:disabled { color: #273a34; background: #f2efe8; }
QSpinBox { min-height: 42px; border: 1px solid #edbfd2; border-radius: 9px; padding: 0 12px; background: white; }
QPlainTextEdit { border: 1px solid #edbfd2; border-radius: 9px; padding: 10px; background: white; font-family: "Microsoft YaHei UI"; }
QProgressBar { border: 0; border-radius: 3px; background: #f7dce8; }
QProgressBar::chunk { border-radius: 3px; background: #c95f8d; }
#teacherStageWindow { background: #241c29; }
#teacherStagePanel { border: 1px solid #edc2d3; border-radius: 16px; background: #fff8fb; }
#teacherStagePanel #cardHeading { color: #8d3f62; font-size: 18px; font-weight: 800; }
"""
