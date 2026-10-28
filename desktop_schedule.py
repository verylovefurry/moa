"""Native monthly commission timeline and its ordinary list view."""
from __future__ import annotations

import calendar
from datetime import date, datetime

from PySide6.QtCore import Qt, QPointF, QRectF, QSize, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QFrame, QHeaderView, QHBoxLayout,
    QLabel, QLayout, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QStackedWidget, QStyledItemDelegate, QStyle,
    QTabBar, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from desktop_dialogs import AssignmentDialog, muted_label
from desktop_finances import FeeSummary
from desktop_icons import ui_icon
from desktop_theme import PAGE_MARGINS, PAGE_SPACING, ICON_SIZE, MUTED, LINE
from desktop_widgets import NoWheelComboBox, RecordDelegate, prepare_table
from schedules import STATUSES, STATUS_LABELS, STATUS_COLORS, assignment_period, fee_text
from tracker import KST

SCHEDULE_ROLE = Qt.ItemDataRole.UserRole + 20


def title_label(text, size=12):
    label = QLabel(text)
    label.setFont(QFont(QApplication.font().family(), size, QFont.Weight.DemiBold))
    return label


class TimelineHeader(QHeaderView):
    def __init__(self, parent):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.year, self.month = date.today().year, date.today().month
        self.today = ""
        self.setFixedHeight(48)
        self.setHighlightSections(False)

    def paintSection(self, painter, rect, index):
        if index != 3:
            super().paintSection(painter, rect, index)
            return
        painter.save()
        painter.fillRect(rect, QColor("#f6f8f6"))
        painter.setPen(QColor(MUTED))
        font = QFont(self.font())
        font.setPointSizeF(8)
        painter.setFont(font)
        count = calendar.monthrange(self.year, self.month)[1]
        step = rect.width() / count
        painter.drawText(rect.adjusted(8, 1, -5, -24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{self.year}년 {self.month:02d}월")
        interval = 1 if step >= 23 else 2 if step >= 15 else 3
        for number in range(1, count + 1):
            key = date(self.year, self.month, number).isoformat()
            x = rect.left() + (number - 1) * step
            if key == self.today:
                painter.fillRect(QRectF(x, rect.bottom() - 21, step, 21), QColor("#e0ebe3"))
            if number == 1 or number == count or (number - 1) % interval == 0:
                if number not in (1, count) and count - number < interval:
                    continue
                width = max(step, painter.fontMetrics().horizontalAdvance(str(number)) + 2)
                left = max(rect.left(), min(x + (step - width) / 2, rect.right() + 1 - width))
                painter.drawText(QRectF(left, rect.bottom() - 21, width, 20), Qt.AlignmentFlag.AlignCenter, str(number))
        painter.setPen(QColor(LINE))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.restore()


class TimelineDelegate(QStyledItemDelegate):
    def __init__(self, parent):
        super().__init__(parent)
        self.year, self.month = date.today().year, date.today().month
        self.today = ""

    def paint(self, painter, option, index):
        item = index.data(SCHEDULE_ROLE)
        if not item:
            return super().paint(painter, option, index)
        painter.save()
        painter.setClipRect(option.rect)
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.fillRect(rect, QColor("#e8f0ea" if selected else "#ffffff"))
        first = date(self.year, self.month, 1)
        count = calendar.monthrange(self.year, self.month)[1]
        step = rect.width() / count
        for number in range(1, count + 1):
            day = date(self.year, self.month, number)
            x = rect.left() + (number - 1) * step
            if day.weekday() >= 5 and not selected:
                painter.fillRect(QRectF(x, rect.top(), step, rect.height()), QColor("#f6f8f6"))
            if day.weekday() == 0:
                painter.setPen(QColor("#e7ece8"))
                painter.drawLine(int(x), rect.top(), int(x), rect.bottom())
            if day.isoformat() == self.today:
                painter.setPen(QPen(QColor("#a5b6aa"), 1))
                painter.drawLine(int(x + step / 2), rect.top(), int(x + step / 2), rect.bottom())
        start, end = assignment_period(item)
        left = max(0, (start - first).days)
        right = min(count, (end - first).days + 1)
        if right > left:
            color = QColor(STATUS_COLORS[item["status"]])
            if not item["due_date"]:
                x = rect.left() + (left + .5) * step
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(QPointF(x, rect.center().y()), 4.5, 4.5)
                font = QFont(option.font)
                font.setPointSizeF(8)
                painter.setFont(font)
                painter.setPen(QColor("#35473c"))
                label = f"{start.month}.{start.day}"
                width = painter.fontMetrics().horizontalAdvance(label) + 2
                label_x = x + 10 if rect.right() - x >= width + 12 else x - width - 10
                painter.drawText(QRectF(label_x, rect.top(), width, rect.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
            else:
                bar = QRectF(rect.left() + left * step + 2, rect.top() + 18, max(3, (right - left) * step - 4), 29)
                fill = QColor(color)
                fill.setAlpha(62 if item["status"] != "completed" else 40)
                painter.setBrush(fill)
                painter.setPen(QPen(color, 1))
                painter.drawRoundedRect(bar, 3, 3)
                painter.fillRect(QRectF(bar.left(), bar.top() + 1, min(3, bar.width()), bar.height() - 2), color)
                font = QFont(option.font)
                font.setPointSizeF(8)
                painter.setFont(font)
                painter.setPen(QColor("#35473c"))
                label = f"{start.month}.{start.day} — {end.month}.{end.day}"
                if bar.width() > 60:
                    painter.drawText(bar.adjusted(8, 0, -5, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, int(bar.width() - 13)))
        painter.setPen(QColor("#e7ece8"))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.restore()


class SchedulePage(QWidget):
    changed = Signal()
    month_requested = Signal(int, int)
    choose_month_requested = Signal()

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.snapshot = backend.state()
        today = date.fromisoformat(self.snapshot["today"])
        self.year, self.month = today.year, today.month
        self.ids = []
        self.selected_id = None
        self._data_signature = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PAGE_MARGINS)
        layout.setSpacing(PAGE_SPACING)
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.addTab("타임라인")
        self.tabs.addTab("목록")
        self.tabs.addTab("금액 합계")
        heading = QHBoxLayout()
        heading.addWidget(title_label("일정 관리", 16))
        heading.addSpacing(16)
        heading.addWidget(self.tabs)
        heading.addStretch()
        self.add_button = QPushButton("새 일정…")
        self.add_button.setProperty("primary", True)
        self.add_button.setToolTip("Ctrl+N")
        self.add_button.clicked.connect(self.add_assignment)
        heading.addWidget(self.add_button)
        layout.addLayout(heading)
        toolbar = QHBoxLayout()
        previous = QPushButton()
        previous.setIcon(ui_icon("previous"))
        previous.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        previous.setProperty("monthNavigation", True)
        previous.setFixedWidth(30)
        previous.setAccessibleName("일정 이전 달")
        previous.setToolTip("이전 달 · Alt+Left")
        previous.clicked.connect(lambda: self.move_month(-1))
        toolbar.addWidget(previous)
        self.month_button = QPushButton()
        self.month_button.setMinimumWidth(128)
        self.month_button.setObjectName("monthPicker")
        self.month_button.setAccessibleName("연·월 바로 이동")
        self.month_button.setToolTip("Ctrl+L")
        self.month_button.clicked.connect(lambda: self.choose_month_requested.emit())
        toolbar.addWidget(self.month_button)
        following = QPushButton()
        following.setIcon(ui_icon("next"))
        following.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        following.setProperty("monthNavigation", True)
        following.setFixedWidth(30)
        following.setAccessibleName("일정 다음 달")
        following.setToolTip("다음 달 · Alt+Right")
        following.clicked.connect(lambda: self.move_month(1))
        toolbar.addWidget(following)
        today_button = QPushButton("이번 달")
        today_button.clicked.connect(self.go_today)
        toolbar.addWidget(today_button)
        toolbar.addStretch()
        self.status_filter = QComboBox()
        self.status_filter.addItem("전체 상태", "all")
        self.status_filter.addItem("미완료 일정", "open")
        for status, label in STATUSES:
            self.status_filter.addItem(label, status)
        self.status_filter.currentIndexChanged.connect(lambda: self.update_data(self.snapshot, self.year, self.month, force=True))
        toolbar.addWidget(self.status_filter)
        self.calendar_controls = (previous, self.month_button, following, today_button)
        layout.addLayout(toolbar)
        self.count_label = muted_label("")
        layout.addWidget(self.count_label)
        self.views = QStackedWidget()
        self.timeline = QTableWidget()
        prepare_table(self.timeline, ["의뢰·할 일 / 의뢰인", "진행 상태", "의뢰비 / 실수령액", "기간"])
        self.timeline.setAccessibleName("의뢰와 할 일 월간 타임라인")
        self.timeline.setObjectName("assignmentTimeline")
        self.timeline.setColumnWidth(0, 230)
        self.timeline.setColumnWidth(1, 116)
        self.timeline.setColumnWidth(2, 164)
        self.timeline_header = TimelineHeader(self.timeline)
        self.timeline.setHorizontalHeader(self.timeline_header)
        self.timeline.setColumnWidth(0, 230)
        self.timeline.setColumnWidth(1, 116)
        self.timeline.setColumnWidth(2, 164)
        self.timeline.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.timeline.setItemDelegateForColumn(0, RecordDelegate(self.timeline))
        self.timeline.setItemDelegateForColumn(2, RecordDelegate(self.timeline))
        self.timeline_delegate = TimelineDelegate(self.timeline)
        self.timeline.setItemDelegateForColumn(3, self.timeline_delegate)
        self.timeline.verticalHeader().setDefaultSectionSize(66)
        self.timeline.itemSelectionChanged.connect(lambda: self.select_row(self.timeline))
        self.timeline.itemDoubleClicked.connect(lambda item: self.edit_assignment() if item.column() != 1 else None)
        self.views.addWidget(self.timeline)
        self.list = QTableWidget()
        prepare_table(self.list, ["의뢰·할 일", "의뢰인", "시작일", "마감일", "의뢰비", "실수령액", "진행 상태"])
        self.list.setAccessibleName("의뢰와 할 일 목록")
        self.list.horizontalHeader().setMinimumSectionSize(108)
        for column in (4, 5):
            self.list.horizontalHeaderItem(column).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.list.setColumnWidth(0, 200)
        for column, width in ((1, 112), (2, 108), (3, 108), (4, 148), (5, 148), (6, 116)):
            self.list.setColumnWidth(column, width)
        self.list.itemSelectionChanged.connect(lambda: self.select_row(self.list))
        self.list.itemDoubleClicked.connect(lambda item: self.edit_assignment() if item.column() != 6 else None)
        self.views.addWidget(self.list)
        self.fee_summary = FeeSummary(self.snapshot["today"])
        self.views.addWidget(self.fee_summary)
        self.empty = muted_label("이달 일정 없음")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.views.addWidget(self.empty)
        self.tabs.currentChanged.connect(self.update_view)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.views.setMinimumHeight(170)
        self.splitter.addWidget(self.views)
        self.detail_panel = QWidget()
        self.detail_panel.setMinimumHeight(240)
        detail = QVBoxLayout(self.detail_panel)
        detail.setContentsMargins(0, 6, 0, 0)
        detail.setSpacing(10)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        line.setProperty("divider", True)
        detail.addWidget(line)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.detail_title = title_label("", 11)
        self.detail_title.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_title.setWordWrap(True)
        self.detail_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.detail_title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.detail_title, 1)
        self.detail_status = QLabel()
        row.addWidget(self.detail_status)
        self.edit_button = QPushButton("편집…")
        self.edit_button.clicked.connect(self.edit_assignment)
        self.delete_button = QPushButton("삭제…")
        self.delete_button.setProperty("destructive", True)
        self.delete_button.clicked.connect(self.delete_assignment)
        row.addWidget(self.edit_button)
        row.addWidget(self.delete_button)
        detail.addLayout(row)
        self.detail_tabs = QTabWidget()
        info = QWidget()
        info_layout = QHBoxLayout(info)
        info_layout.setContentsMargins(12, 12, 12, 12)
        info_layout.setSpacing(24)
        info_layout.setSizeConstraint(QLayout.SizeConstraint.SetDefaultConstraint)
        fields = QWidget()
        form = QFormLayout(fields)
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        self.detail_fields = {}
        for key, caption in (("client", "의뢰인"), ("start_date", "시작일"), ("due_date", "마감일"), ("fee", "의뢰비"), ("net_fee", "실수령액")):
            value = QLabel()
            value.setWordWrap(True)
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setAccessibleName(caption)
            form.addRow(muted_label(caption), value)
            self.detail_fields[key] = value
        info_layout.addWidget(fields, 2, Qt.AlignmentFlag.AlignTop)
        note_column = QVBoxLayout()
        note_column.setSpacing(6)
        note_column.addWidget(muted_label("메모"))
        self.detail_note = QPlainTextEdit()
        self.detail_note.setReadOnly(True)
        self.detail_note.setAccessibleName("일정 메모")
        self.detail_note.setMinimumHeight(70)
        note_column.addWidget(self.detail_note, 1)
        info_layout.addLayout(note_column, 3)
        info_scroll = QScrollArea()
        info_scroll.setFrameShape(QFrame.Shape.NoFrame)
        info_scroll.setWidgetResizable(True)
        info_scroll.setWidget(info)
        self.detail_tabs.addTab(info_scroll, "상세 정보")
        self.detail_history = QTableWidget()
        prepare_table(self.detail_history, ["변경 일시", "변경 내용"])
        self.detail_history.setAccessibleName("일정 상태 변경 이력")
        self.detail_history.setColumnWidth(0, 154)
        self.detail_history.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.detail_history.verticalHeader().setDefaultSectionSize(32)
        self.detail_tabs.addTab(self.detail_history, "상태 이력")
        detail.addWidget(self.detail_tabs, 1)
        self.splitter.addWidget(self.detail_panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([340, 260])
        layout.addWidget(self.splitter, 1)
        self.update_data(self.snapshot, self.year, self.month, force=True)

    def move_month(self, amount):
        value = min(9999 * 12 + 11, max(1900 * 12, self.year * 12 + self.month - 1 + amount))
        year, month = divmod(value, 12)
        self.month_requested.emit(year, month + 1)

    def go_today(self):
        today = date.fromisoformat(self.snapshot["today"])
        self.month_requested.emit(today.year, today.month)

    def update_view(self):
        summary = self.tabs.currentIndex() == 2
        for control in (*self.calendar_controls, self.status_filter, self.count_label):
            control.setVisible(not summary)
        self.detail_panel.setVisible(not summary and self.selected_id is not None)
        view = (self.timeline, self.list, self.fee_summary)[self.tabs.currentIndex()]
        self.views.setCurrentWidget(view if summary or self.ids else self.empty)

    def visible_assignments(self):
        first = date(self.year, self.month, 1)
        last = date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])
        status_filter = self.status_filter.currentData()
        result = []
        for item in self.snapshot["assignments"]:
            start, end = assignment_period(item)
            if start > last or end < first:
                continue
            if status_filter == "open" and item["status"] == "completed":
                continue
            if status_filter not in ("all", "open", item["status"]):
                continue
            result.append(item)
        return result

    def update_data(self, snapshot, year, month, force=False):
        self.snapshot, self.year, self.month = snapshot, year, month
        self.month_button.setText(f"{year}년 {month:02d}월")
        signature = repr((snapshot["assignments"], snapshot["assignment_history"], snapshot["today"], year, month, self.status_filter.currentData()))
        if not force and signature == self._data_signature:
            return
        self._data_signature = signature
        self.fee_summary.set_assignments(snapshot["assignments"])
        values = self.visible_assignments()
        self.ids = [item["id"] for item in values]
        if self.selected_id not in self.ids:
            self.selected_id = self.ids[0] if self.ids else None
        self.timeline_header.year = self.timeline_delegate.year = year
        self.timeline_header.month = self.timeline_delegate.month = month
        self.timeline_header.today = self.timeline_delegate.today = snapshot["today"]
        self.timeline_header.viewport().update()
        for table in (self.timeline, self.list):
            table.blockSignals(True)
            table.setRowCount(len(values))
        for row, item in enumerate(values):
            deadline = item["due_date"] or "마감 없음"
            net_fee = item.get("net_fee")
            tooltip = f"{item['title']}\n의뢰인: {item['client'] or '개인 할 일'}\n{item['start_date']} → {deadline}\n의뢰비: {fee_text(item['fee'])}\n실수령액: {fee_text(net_fee)}\n{STATUS_LABELS[item['status']]}"
            title = QTableWidgetItem(f"{item['title']}\n{item['client'] or '개인 할 일'}")
            title.setToolTip(tooltip)
            self.timeline.setItem(row, 0, title)
            self.timeline.setItem(row, 1, QTableWidgetItem())
            amounts = QTableWidgetItem(f"{fee_text(item['fee'])}\n{fee_text(net_fee) if net_fee is not None else '미입력'}")
            amounts.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            amounts.setToolTip(tooltip)
            amounts.setData(Qt.ItemDataRole.AccessibleTextRole, f"의뢰비 {fee_text(item['fee'])}, 실수령액 {fee_text(net_fee)}")
            self.timeline.setItem(row, 2, amounts)
            period = QTableWidgetItem()
            period.setData(SCHEDULE_ROLE, item)
            period.setToolTip(tooltip)
            period.setData(Qt.ItemDataRole.AccessibleTextRole, f"{item['start_date']}부터 {deadline}까지" if item["due_date"] else f"시작일 {item['start_date']} · 마감 없음")
            self.timeline.setItem(row, 3, period)
            for column, text in enumerate((item["title"], item["client"] or "개인 할 일", item["start_date"], deadline, fee_text(item["fee"]), fee_text(net_fee), "")):
                entry = QTableWidgetItem(text)
                entry.setToolTip(tooltip)
                if column in (4, 5):
                    entry.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.list.setItem(row, column, entry)
            for table, column in ((self.timeline, 1), (self.list, 6)):
                combo = NoWheelComboBox()
                combo.setAccessibleName(f"{item['title']} 진행 상태")
                for status, label in STATUSES:
                    combo.addItem(label, status)
                combo.setCurrentIndex(combo.findData(item["status"]))
                combo.activated.connect(lambda index, tid=item["id"], control=combo: self.change_status(tid, control.itemData(index)))
                combo.setMaximumHeight(30)
                cell = QWidget()
                cell_layout = QHBoxLayout(cell)
                cell_layout.setContentsMargins(4, 0, 4, 0)
                cell_layout.addWidget(combo, 1, Qt.AlignmentFlag.AlignVCenter)
                table.setCellWidget(row, column, cell)
            if item["id"] == self.selected_id:
                self.timeline.selectRow(row)
                self.list.selectRow(row)
        for table in (self.timeline, self.list):
            table.blockSignals(False)
        self.count_label.setText(f"이달 일정 {len(values)}개 · 미완료 {sum(item['status'] != 'completed' for item in values)}개")
        self.update_view()
        self.edit_button.setEnabled(bool(values))
        self.delete_button.setEnabled(bool(values))
        self.update_detail()

    def select_row(self, table):
        row = table.currentRow()
        if 0 <= row < len(self.ids):
            self.selected_id = self.ids[row]
            other = self.list if table is self.timeline else self.timeline
            other.blockSignals(True)
            other.selectRow(row)
            other.blockSignals(False)
            self.update_detail()

    def selected_assignment(self):
        return next((item for item in self.snapshot["assignments"] if item["id"] == self.selected_id), None)

    def update_detail(self):
        item = self.selected_assignment()
        self.edit_button.setEnabled(item is not None)
        self.delete_button.setEnabled(item is not None)
        self.detail_panel.setVisible(item is not None and self.tabs.currentIndex() != 2)
        if not item:
            self.detail_title.clear()
            self.detail_status.clear()
            for value in self.detail_fields.values():
                value.clear()
            self.detail_note.clear()
            self.detail_history.setRowCount(0)
            return
        self.detail_title.setText(item["title"])
        self.detail_title.setToolTip(item["title"])
        self.detail_status.setText(STATUS_LABELS[item["status"]])
        values = {"client": item["client"] or "개인 할 일", "start_date": item["start_date"],
                  "due_date": item["due_date"] or "없음", "fee": fee_text(item["fee"]) if item["fee"] is not None else "없음",
                  "net_fee": fee_text(item["net_fee"]) if item.get("net_fee") is not None else "미입력"}
        for key, value in values.items():
            self.detail_fields[key].setText(value)
        note = item["note"] or "없음"
        if self.detail_note.toPlainText() != note:
            self.detail_note.setPlainText(note)
        history = [entry for entry in self.snapshot["assignment_history"] if entry["assignment_id"] == item["id"]]
        self.detail_history.setRowCount(len(history))
        for row, entry in enumerate(reversed(history)):
            stamp = datetime.fromtimestamp(entry["changed_at"], KST).strftime("%Y-%m-%d %H:%M")
            change = (STATUS_LABELS[entry["from_status"]] + " → " if entry["from_status"] else "등록: ") + STATUS_LABELS[entry["to_status"]]
            self.detail_history.setItem(row, 0, QTableWidgetItem(stamp))
            self.detail_history.setItem(row, 1, QTableWidgetItem(change))

    def add_assignment(self):
        dialog = AssignmentDialog(self, self.backend)
        if dialog.exec() == dialog.DialogCode.Accepted:
            start = dialog.start.date()
            self.month_requested.emit(start.year(), start.month())
            self.changed.emit()

    def edit_assignment(self):
        assignment = self.selected_assignment()
        if assignment:
            dialog = AssignmentDialog(self, self.backend, assignment)
            if dialog.exec() == dialog.DialogCode.Accepted:
                start = dialog.start.date()
                self.month_requested.emit(start.year(), start.month())
                self.changed.emit()

    def change_status(self, assignment_id, status):
        try:
            self.backend.command("assignment-status", {"id": assignment_id, "status": status})
        except (ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "일정 상태 변경", str(exc))
        self.changed.emit()

    def delete_assignment(self):
        assignment = self.selected_assignment()
        if not assignment:
            return
        answer = QMessageBox.question(self, "일정 삭제", f"‘{assignment['title']}’을 일정 목록에서 삭제할까요?\n자동 측정한 작업 시간에는 영향을 주지 않습니다.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Yes:
            try:
                self.backend.command("assignment-delete", {"id": assignment["id"]})
            except (ValueError, RuntimeError) as exc:
                QMessageBox.warning(self, "일정 삭제", str(exc))
            self.changed.emit()
