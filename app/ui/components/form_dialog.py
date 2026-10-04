"""高频表单的滚动正文、固定页脚与主题反馈。保留原控件和信号。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialogButtonBox, QFormLayout, QLabel, QPlainTextEdit,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from .settings_controls import feedback
from ..design import spacing, typography


def _move_item(layout, item, stretch=0):
    widget = item.widget()
    if widget is not None:
        if isinstance(widget, QDialogButtonBox):
            layout.addWidget(widget, stretch, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute)
        else:
            layout.addWidget(widget, stretch)
    elif item.layout() is not None:
        child = item.layout()
        child.setParent(None)
        layout.addLayout(child, stretch)
    else:
        layout.addItem(item)


def _buttons(box):
    confirm = box.button(QDialogButtonBox.StandardButton.Ok)
    cancel = box.button(QDialogButtonBox.StandardButton.Cancel)
    if confirm is not None:
        confirm.setObjectName("PrimaryButton")
        confirm.setDefault(True)
    if cancel is not None:
        cancel.setObjectName("SecondaryButton")
        cancel.setAutoDefault(False)
    # Inspect the current platform order rather than assuming Windows/Linux agree.
    if confirm is not None and cancel is not None:
        box.resize(box.sizeHint())
        box.layout().activate()
        if confirm.x() < cancel.x():
            box.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    box.setCenterButtons(False)


def finish_form(dialog, *, scroll_body=True):
    root = dialog.layout()
    root.setContentsMargins(spacing.LG, spacing.LG, spacing.LG, spacing.LG)
    root.setSpacing(spacing.SM)
    if scroll_body:
        footer = root.takeAt(root.count() - 1)
        body = QWidget()
        body.setObjectName("FormDialogBody")
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, spacing.SM, 0)
        content.setSpacing(spacing.SM)
        pinned_feedback = None
        label = getattr(dialog, "error_label", getattr(dialog, "feedback", None))
        while root.count():
            stretch = root.stretch(0)
            item = root.takeAt(0)
            if item.widget() is label and label is not None:
                pinned_feedback = label
            else:
                _move_item(content, item, stretch)
        scroll = QScrollArea(dialog)
        scroll.setObjectName("FormDialogScroll")
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        if pinned_feedback is not None:
            root.addWidget(pinned_feedback)
        _move_item(root, footer)
        dialog.form_scroll = scroll
    for form in dialog.findChildren(QFormLayout):
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setHorizontalSpacing(spacing.MD)
        form.setVerticalSpacing(spacing.SM)
    for combo in dialog.findChildren(QComboBox):
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(12)
        combo.setMinimumWidth(0)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    for label in dialog.findChildren(QLabel):
        label.setWordWrap(True)
    for editor in dialog.findChildren(QPlainTextEdit):
        editor.setMinimumHeight(80)
        editor.setMaximumHeight(16777215 if editor.isReadOnly() else 240)
        if editor.isReadOnly():
            editor.setFont(typography.font_for(typography.MONOSPACE))
    for box in dialog.findChildren(QDialogButtonBox):
        _buttons(box)
    for name in ("error_label", "feedback"):
        label = getattr(dialog, name, None)
        if label is not None:
            label.setStyleSheet("")
            feedback(label, label.text(), "error")
    screen = dialog.screen()
    if screen is not None:
        available = screen.availableGeometry()
        dialog.resize(min(dialog.width(), int(available.width() * 0.9)),
                      min(dialog.height(), int(available.height() * 0.9)))
