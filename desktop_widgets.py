"""Purpose-built calendar and chart inside a native Qt Widgets interface."""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from PySide6.QtCore import Qt, QRect, QRectF, QSize, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHeaderView, QStyle, QStyledItemDelegate,
    QTableWidget, QTableWidgetItem, QWidget,
)

from desktop_backend import format_duration

GREEN = "#365f4c"
TEXT = "#26332e"
MUTED = "#66736b"
LINE = "#dfe4e1"
DAY_ROLE = Qt.ItemDataRole.UserRole + 1
PAYLOAD_ROLE = Qt.ItemDataRole.UserRole + 2


class NoWheelComboBox(QComboBox):
    """Keep choices explicit while allowing wheel events to reach the parent."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        event.ignore()


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(size / 64, size / 64)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(GREEN))
        painter.drawRoundedRect(QRectF(2, 2, 60, 60), 12, 12)
        painter.setPen(QPen(QColor("#ffffff"), 3.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(14, 14, 36, 36))
        painter.drawLine(32, 21, 32, 33)
        painter.drawLine(32, 33, 41, 38)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def color_icon(color: str) -> QIcon:
    pixmap = QPixmap(14, 14)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(3, 3, 8, 8, 2, 2)
    painter.end()
    return QIcon(pixmap)


def prepare_table(table: QTableWidget, headers: list[str]):
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().hide()
    table.horizontalHeader().setHighlightSections(False)
    table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setShowGrid(False)
    table.setAlternatingRowColors(False)
    table.setWordWrap(False)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.verticalHeader().setDefaultSectionSize(39)
    table.horizontalHeader().setStretchLastSection(False)
    table.setAccessibleName("작업 기록 목록")


class CalendarDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        payload = index.data(PAYLOAD_ROLE)
        if not payload:
            return
        painter.save()
        painter.setClipRect(option.rect)
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        outside = payload["outside"]
        painter.fillRect(rect, QColor("#edf4ef" if selected else "#f8f9f9" if outside else "#ffffff"))
        painter.setPen(QPen(QColor(LINE), 1))
        painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if selected:
            painter.setPen(QPen(QColor("#719580"), 1.5))
            painter.drawRect(rect.adjusted(1, 1, -1, -1))

        left = rect.left() + 10
        top = rect.top() + 9
        number_rect = QRect(left - 3, top - 2, 26, 24)
        font = QFont(option.font)
        font.setPointSizeF(9.3)
        font.setWeight(QFont.Weight.DemiBold if payload["today"] else QFont.Weight.Normal)
        painter.setFont(font)
        if payload["today"]:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(GREEN))
            painter.drawRoundedRect(number_rect, 3, 3)
            painter.setPen(QColor("white"))
        else:
            painter.setPen(QColor("#a3aba7" if outside else "#9a655d" if payload["weekday"] == 6 else TEXT))
        painter.drawText(number_rect, Qt.AlignmentFlag.AlignCenter, str(payload["number"]))
        if payload["today"] and rect.width() > 86:
            font.setPointSizeF(8)
            font.setWeight(QFont.Weight.Normal)
            painter.setFont(font)
            painter.setPen(QColor(GREEN))
            painter.drawText(QRect(left + 28, top, rect.width() - 46, 20), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "오늘")

        day = payload.get("day")
        if day and day["total"] > 0:
            font.setPointSizeF(9.3)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(TEXT if not outside else MUTED))
            total_rect = QRect(left, top + 31, rect.width() - 20, 21)
            total_text = format_duration(day["total"])
            painter.drawText(total_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(total_text, Qt.TextElideMode.ElideRight, total_rect.width()))
            if rect.height() >= 92:
                font.setPointSizeF(8)
                font.setWeight(QFont.Weight.Normal)
                painter.setFont(font)
                painter.setPen(QColor(MUTED))
                names_rect = QRect(left, top + 55, rect.width() - 20, 18)
                text = payload["target_text"]
                painter.drawText(names_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, names_rect.width()))
            available = max(1, rect.width() - 20)
            x = float(left)
            y = rect.bottom() - 11
            for color, seconds in payload["segments"]:
                width = max(1, available * seconds / day["total"])
                painter.fillRect(QRectF(x, y, max(1, width - 2), 3), QColor(color))
                x += width
        painter.restore()


class WorkCalendar(QTableWidget):
    date_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(0, 7, parent)
        self.setObjectName("workCalendar")
        self.setAccessibleName("월별 작업 시간 달력")
        self.setHorizontalHeaderLabels(["월", "화", "수", "목", "금", "토", "일"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setMinimumHeight(31)
        self.horizontalHeader().setHighlightSections(False)
        self.verticalHeader().hide()
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.setItemDelegate(CalendarDelegate(self))
        self.setShowGrid(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumSize(470, 370)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.currentCellChanged.connect(self._selected)

    def _selected(self, row, column, *_):
        item = self.item(row, column)
        if item and item.data(DAY_ROLE):
            self.date_selected.emit(item.data(DAY_ROLE))

    def display_month(self, year, month, selected, today, daily, targets):
        try:
            weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
        except (OverflowError, ValueError):
            first = date(year, month, 1)
            start = first.toordinal() - first.weekday()
            count = len(calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)) * 7
            days = [date.fromordinal(number) if 1 <= number <= date.max.toordinal() else None for number in range(start, start + count)]
            weeks = [days[index:index + 7] for index in range(0, count, 7)]
        names = {t["id"]: t for t in targets}
        self.blockSignals(True)
        self.clearContents()
        self.setRowCount(len(weeks))
        for row, week in enumerate(weeks):
            for col, day in enumerate(week):
                if day is None:
                    item = QTableWidgetItem("")
                    item.setFlags(Qt.ItemFlag.NoItemFlags)
                    self.setItem(row, col, item)
                    continue
                key = day.isoformat()
                record = daily.get(key)
                parts = sorted((record or {}).get("targets", {}).items(), key=lambda pair: -pair[1])
                main = names.get(parts[0][0], {}).get("name", "작업") if parts else ""
                caption = main + (f" 외 {len(parts) - 1}개" if len(parts) > 1 else "")
                data = {
                    "number": day.day, "outside": day.month != month, "today": key == today,
                    "weekday": day.weekday(), "day": record, "target_text": caption,
                    "segments": [(names.get(tid, {}).get("color", "#84939d"), sec) for tid, sec in parts],
                }
                item = QTableWidgetItem(f"{day.day}\n{format_duration(record['total']) if record else ''}")
                item.setData(DAY_ROLE, key)
                item.setData(PAYLOAD_ROLE, data)
                spoken = f"{month if day.month == month else day.month}월 {day.day}일, {format_duration(record['total']) if record else '기록 없음'}"
                item.setData(Qt.ItemDataRole.AccessibleTextRole, spoken + (", 오늘" if key == today else ""))
                item.setToolTip(spoken + (f"\n{caption}" if caption else ""))
                self.setItem(row, col, item)
                if key == selected:
                    self.setCurrentCell(row, col)
        self.blockSignals(False)
        self.viewport().update()


class RecordDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        style = option.widget.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, option.widget)
        painter.save()
        painter.setClipRect(option.rect)
        text, _, secondary = str(index.data() or "").partition("\n")
        rect = option.rect.adjusted(10, 5, -8, -4)
        color = index.data(Qt.ItemDataRole.ForegroundRole)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setPen(QColor(TEXT) if not selected else option.palette.highlightedText().color())
        font = QFont(option.font)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.drawText(QRect(rect.left(), rect.top(), rect.width(), 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, rect.width()))
        font.setPointSizeF(8)
        painter.setFont(font)
        painter.setPen(QColor(MUTED) if not selected else option.palette.highlightedText().color())
        painter.drawText(QRect(rect.left(), rect.top() + 23, rect.width(), 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(secondary, Qt.TextElideMode.ElideRight, rect.width()))
        painter.setPen(QColor("#edf0ee"))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        painter.restore()


class DailyChart(QWidget):
    date_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self.setAccessibleName("일별 작업 시간 막대 그래프")
        self.values = []
        self.today = ""
        self.maximum = 8 * 3600
        self._bars = []
        self.setMouseTracking(True)

    def set_data(self, year, month, daily, today):
        self.values = [(date(year, month, number).isoformat(), daily.get(date(year, month, number).isoformat(), {}).get("total", 0)) for number in range(1, calendar.monthrange(year, month)[1] + 1)]
        self.maximum = max(8 * 3600, *(seconds for _, seconds in self.values))
        self.today = today
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self.rect().adjusted(42, 17, -17, -31)
        font = QFont(self.font())
        font.setPointSizeF(8)
        painter.setFont(font)
        for fraction in (0, .25, .5, .75, 1):
            y = int(plot.bottom() - plot.height() * fraction)
            painter.setPen(QColor("#e9edea"))
            painter.drawLine(plot.left(), y, plot.right(), y)
            painter.setPen(QColor(MUTED))
            painter.drawText(QRect(0, y - 9, 33, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{self.maximum * fraction / 3600:g}h")
        self._bars = []
        if not self.values:
            return
        step = plot.width() / len(self.values)
        for i, (day, seconds) in enumerate(self.values):
            height = plot.height() * seconds / self.maximum
            x = plot.left() + step * i + step * .2
            rect = QRectF(x, plot.bottom() - height, max(3, step * .6), height)
            painter.fillRect(rect, QColor(GREEN if day == self.today else "#8da894"))
            self._bars.append((QRectF(x - 2, plot.top(), step * .9, plot.height()), day, seconds))
            if step >= 22 or i % 2 == 0:
                painter.setPen(QColor(MUTED))
                painter.drawText(QRectF(x - 3, plot.bottom() + 9, step * .9, 18), Qt.AlignmentFlag.AlignCenter, str(i + 1))
        painter.end()

    def mouseMoveEvent(self, event):
        for rect, day, seconds in self._bars:
            if rect.contains(event.position()):
                self.setToolTip(f"{day} · {format_duration(seconds)}")
                return

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            for rect, day, _ in self._bars:
                if rect.contains(event.position()):
                    self.date_selected.emit(day)
                    return
        super().mousePressEvent(event)
