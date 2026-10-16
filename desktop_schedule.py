"""Native monthly commission timeline and its ordinary list view."""
from __future__ import annotations

import calendar
from datetime import date, datetime

from PySide6.QtCore import Qt, QRect, QRectF, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHeaderView, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QStackedWidget, QStyledItemDelegate, QStyle, QTabBar,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from desktop_dialogs import AssignmentDialog, muted_label
from desktop_widgets import RecordDelegate, prepare_table
from schedules import STATUSES, STATUS_LABELS, STATUS_COLORS, fee_text
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
        painter.fillRect(rect, QColor("#f5f7f5"))
        painter.setPen(QColor("#66736b"))
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
                painter.drawText(QRectF(x, rect.bottom() - 21, step, 20), Qt.AlignmentFlag.AlignCenter, str(number))
        painter.setPen(QColor("#dfe4e1"))
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
        start = date.fromisoformat(item["start_date"])
        end = date.fromisoformat(item["due_date"]) if item["due_date"] else date(self.year, self.month, count)
        if not item["due_date"] and item.get("completed_at"):
            end = max(start, datetime.fromtimestamp(item["completed_at"], KST).date())
        left = max(0, (start - first).days)
        right = min(count, (end - first).days + 1)
        if right > left:
            bar = QRectF(rect.left() + left * step + 2, rect.top() + 18, max(3, (right - left) * step - 4), 29)
            color = QColor(STATUS_COLORS[item["status"]])
            fill = QColor(color)
            fill.setAlpha(62 if item["status"] != "completed" else 40)
            painter.setBrush(fill)
            painter.setPen(QPen(color, 1, Qt.PenStyle.DashLine if not item["due_date"] and not item.get("completed_at") else Qt.PenStyle.SolidLine))
            painter.drawRoundedRect(bar, 3, 3)
            painter.fillRect(QRectF(bar.left(), bar.top() + 1, min(3, bar.width()), bar.height() - 2), color)
            font = QFont(option.font)
            font.setPointSizeF(8)
            painter.setFont(font)
            painter.setPen(QColor("#35473c"))
            label = f"{start.month}.{start.day} — {end.month}.{end.day}" if item["due_date"] else "마감 없음" if not item.get("completed_at") else "완료"
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
        layout.setContentsMargins(23, 20, 23, 17)
        layout.setSpacing(14)
        heading = QHBoxLayout()
        heading.addWidget(title_label("일정 관리", 16))
        heading.addStretch()
        self.add_button = QPushButton("새 일정…")
        self.add_button.setToolTip("새 의뢰·할 일 · Ctrl+N")
        self.add_button.clicked.connect(self.add_assignment)
        heading.addWidget(self.add_button)
        layout.addLayout(heading)
        toolbar = QHBoxLayout()
        previous = QPushButton("‹")
        previous.setFixedWidth(29)
        previous.setAccessibleName("일정 이전 달")
        previous.clicked.connect(lambda: self.move_month(-1))
        toolbar.addWidget(previous)
        self.month_button = QPushButton()
        self.month_button.setMinimumWidth(136)
        self.month_button.setToolTip("연·월을 지정해 바로 이동")
        self.month_button.clicked.connect(lambda: self.choose_month_requested.emit())
        toolbar.addWidget(self.month_button)
        following = QPushButton("›")
        following.setFixedWidth(29)
        following.setAccessibleName("일정 다음 달")
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
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.addTab("타임라인")
        self.tabs.addTab("목록")
        toolbar.addWidget(self.tabs)
        layout.addLayout(toolbar)
        self.count_label = muted_label("")
        layout.addWidget(self.count_label)
        self.views = QStackedWidget()
        self.timeline = QTableWidget()
        prepare_table(self.timeline, ["의뢰·할 일 / 의뢰인", "진행 상태", "보수", "기간"])
        self.timeline.setAccessibleName("의뢰와 할 일 월간 타임라인")
        self.timeline.setObjectName("assignmentTimeline")
        self.timeline.setColumnWidth(0, 230)
        self.timeline.setColumnWidth(1, 116)
        self.timeline.setColumnWidth(2, 112)
        self.timeline_header = TimelineHeader(self.timeline)
        self.timeline.setHorizontalHeader(self.timeline_header)
        self.timeline.setColumnWidth(0, 230)
        self.timeline.setColumnWidth(1, 116)
        self.timeline.setColumnWidth(2, 112)
        self.timeline.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.timeline.setItemDelegateForColumn(0, RecordDelegate(self.timeline))
        self.timeline_delegate = TimelineDelegate(self.timeline)
        self.timeline.setItemDelegateForColumn(3, self.timeline_delegate)
        self.timeline.verticalHeader().setDefaultSectionSize(66)
        self.timeline.itemSelectionChanged.connect(lambda: self.select_row(self.timeline))
        self.timeline.itemDoubleClicked.connect(lambda item: self.edit_assignment() if item.column() != 1 else None)
        self.views.addWidget(self.timeline)
        self.list = QTableWidget()
        prepare_table(self.list, ["의뢰·할 일", "의뢰인", "시작일", "마감일", "보수", "진행 상태"])
        self.list.setAccessibleName("의뢰와 할 일 목록")
        self.list.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 130), (2, 108), (3, 108), (4, 112), (5, 116)):
            self.list.setColumnWidth(column, width)
        self.list.itemSelectionChanged.connect(lambda: self.select_row(self.list))
        self.list.itemDoubleClicked.connect(lambda item: self.edit_assignment() if item.column() != 5 else None)
        self.views.addWidget(self.list)
        self.tabs.currentChanged.connect(self.views.setCurrentIndex)
        layout.addWidget(self.views, 1)
        self.empty = muted_label("이 달에는 등록된 일정이 없습니다. ‘새 일정’을 눌러 의뢰나 할 일을 추가하세요.")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty)
        row = QHBoxLayout()
        row.addWidget(muted_label("점선 막대는 마감일이 없는 일정입니다. 상태는 목록에서 바로 변경할 수 있습니다."), 1)
        self.edit_button = QPushButton("편집…")
        self.edit_button.clicked.connect(self.edit_assignment)
        self.delete_button = QPushButton("삭제…")
        self.delete_button.clicked.connect(self.delete_assignment)
        row.addWidget(self.edit_button)
        row.addWidget(self.delete_button)
        layout.addLayout(row)
        self.detail_title = title_label("일정을 선택하면 상세 정보가 표시됩니다.", 11)
        self.detail_title.setWordWrap(True)
        self.detail_title.setMaximumHeight(48)
        self.detail_meta = muted_label("")
        self.detail_note = muted_label("")
        self.detail_note.setMaximumHeight(48)
        self.detail_history = muted_label("")
        for label in (self.detail_title, self.detail_meta, self.detail_note, self.detail_history):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label)
        self.update_data(self.snapshot, self.year, self.month, force=True)

    def move_month(self, amount):
        value = min(9999 * 12 + 11, max(1900 * 12, self.year * 12 + self.month - 1 + amount))
        year, month = divmod(value, 12)
        self.month_requested.emit(year, month + 1)

    def go_today(self):
        today = date.fromisoformat(self.snapshot["today"])
        self.month_requested.emit(today.year, today.month)

    def visible_assignments(self):
        first = date(self.year, self.month, 1).isoformat()
        last = date(self.year, self.month, calendar.monthrange(self.year, self.month)[1]).isoformat()
        status_filter = self.status_filter.currentData()
        result = []
        for item in self.snapshot["assignments"]:
            end = item["due_date"]
            if not end and item.get("completed_at"):
                end = max(item["start_date"], datetime.fromtimestamp(item["completed_at"], KST).date().isoformat())
            if item["start_date"] > last or (end and end < first):
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
            tooltip = f"{item['title']}\n의뢰인: {item['client'] or '개인 할 일'}\n{item['start_date']} → {deadline}\n보수: {fee_text(item['fee'])}\n{STATUS_LABELS[item['status']]}"
            title = QTableWidgetItem(f"{item['title']}\n{item['client'] or '개인 할 일'}")
            title.setToolTip(tooltip)
            self.timeline.setItem(row, 0, title)
            self.timeline.setItem(row, 1, QTableWidgetItem())
            self.timeline.setItem(row, 2, QTableWidgetItem(fee_text(item["fee"])))
            period = QTableWidgetItem()
            period.setData(SCHEDULE_ROLE, item)
            period.setToolTip(tooltip)
            period.setData(Qt.ItemDataRole.AccessibleTextRole, f"{item['start_date']}부터 {deadline}까지")
            self.timeline.setItem(row, 3, period)
            for column, text in enumerate((item["title"], item["client"] or "개인 할 일", item["start_date"], deadline, fee_text(item["fee"]), "")):
                entry = QTableWidgetItem(text)
                entry.setToolTip(tooltip)
                self.list.setItem(row, column, entry)
            for table, column in ((self.timeline, 1), (self.list, 5)):
                combo = QComboBox()
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
        self.empty.setVisible(not values)
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
        if not item:
            self.detail_title.setText("일정을 선택하면 상세 정보가 표시됩니다.")
            for label in (self.detail_meta, self.detail_note, self.detail_history):
                label.clear()
                label.hide()
            return
        self.detail_title.setText(item["title"])
        self.detail_title.setToolTip(item["title"])
        self.detail_meta.show()
        self.detail_meta.setText(f"의뢰인: {item['client'] or '개인 할 일'}    시작: {item['start_date']}    마감: {item['due_date'] or '없음'}    보수: {fee_text(item['fee'])}")
        self.detail_note.setVisible(bool(item["note"]))
        self.detail_note.setText(item["note"][:300] + ("…" if len(item["note"]) > 300 else ""))
        self.detail_note.setToolTip(item["note"])
        history = [entry for entry in self.snapshot["assignment_history"] if entry["assignment_id"] == item["id"]]
        texts = []
        for entry in history:
            stamp = datetime.fromtimestamp(entry["changed_at"], KST).strftime("%m.%d %H:%M")
            change = (STATUS_LABELS[entry["from_status"]] + " → " if entry["from_status"] else "등록: ") + STATUS_LABELS[entry["to_status"]]
            texts.append(f"{stamp} {change}")
        self.detail_history.setVisible(bool(texts))
        self.detail_history.setText("상태 이력  ·  " + "  /  ".join(texts[-3:]))
        self.detail_history.setToolTip("\n".join(texts))

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
