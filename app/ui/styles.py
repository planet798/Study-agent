"""全局 QSS 样式。简洁、中文友好、信息层级清晰。"""

APP_STYLE = """
/* ---- 全局 ---- */
QWidget {
    font-family: "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
    font-size: 14px;
    color: #2c3e50;
}
QMainWindow, QDialog {
    background-color: #f5f7fa;
}

/* ---- 标题 ---- */
#AppTitle {
    font-size: 22px;
    font-weight: bold;
    color: #1f3a5f;
}
#AppDate {
    font-size: 13px;
    color: #7f8c8d;
}
#SectionTitle {
    font-size: 17px;
    font-weight: bold;
    color: #1f3a5f;
    padding-top: 8px;
}
#EmptyHint {
    color: #95a5a6;
    font-size: 15px;
    padding: 30px;
}

/* ---- 任务卡片 ---- */
QFrame#TaskCard {
    background-color: #ffffff;
    border: 1px solid #e0e4e8;
    border-radius: 8px;
    margin: 3px 0px;
}
QFrame#TaskCard[done="true"] {
    background-color: #eef7ee;
    border-color: #c8e6c9;
}
QFrame#TaskCard[postponing="true"] {
    background-color: #fffbea;
    border-color: #f3e4a3;
}
#TaskTitle {
    font-size: 15px;
    font-weight: bold;
}
#TaskDesc {
    color: #5d6d7e;
    font-size: 13px;
}
#TaskMeta {
    color: #7f8c8d;
    font-size: 12px;
}
#TaskReason {
    color: #a04000;
    font-size: 12px;
}
#PostponeWarning {
    color: #b7791f;
    font-size: 12px;
    font-weight: bold;
}
#DoneBadge {
    color: #27ae60;
    font-weight: bold;
}

/* ---- 按钮 ---- */
QPushButton {
    background-color: #eef2f6;
    border: 1px solid #d5dbe2;
    border-radius: 6px;
    padding: 6px 14px;
}
QPushButton:hover {
    background-color: #e2e8f0;
}
QPushButton:disabled {
    color: #b0b7bf;
}
QPushButton#PrimaryButton {
    background-color: #2c6fbb;
    border: 1px solid #2c6fbb;
    color: white;
    font-weight: bold;
}
QPushButton#PrimaryButton:hover {
    background-color: #255e9e;
}
QPushButton#DangerButton {
    border-color: #e74c3c;
    color: #e74c3c;
}
QPushButton#DangerButton:hover {
    background-color: #fdecea;
}
QPushButton#PostponeButton {
    border-color: #b7791f;
    color: #b7791f;
}
QPushButton#PostponeButton:hover {
    background-color: #fff8e6;
}

/* ---- 统计栏 ---- */
QFrame#StatsBar {
    background-color: #ffffff;
    border: 1px solid #e0e4e8;
    border-radius: 8px;
}
#StatsTitle {
    font-weight: bold;
    color: #1f3a5f;
    font-size: 14px;
}
#StatsValue {
    font-weight: bold;
    color: #2c6fbb;
    font-size: 14px;
}

/* ---- 输入 ---- */
QTextEdit, QLineEdit {
    background-color: #ffffff;
    border: 1px solid #d5dbe2;
    border-radius: 6px;
    padding: 6px;
    selection-background-color: #cfe0f5;
}
QTextEdit:focus, QLineEdit:focus {
    border-color: #2c6fbb;
}
QErrorMessage {
    color: #e74c3c;
}

/* ---- 状态栏 / 托盘消息 ---- */
QStatusBar {
    background-color: #eef2f6;
    color: #5d6d7e;
}
"""
