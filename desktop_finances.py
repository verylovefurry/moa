"""Native period selectors and commission/receipt totals for schedules."""
from __future__ import annotations

import calendar
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QDateEdit, QHeaderView, QHBoxLayout, QLabel, QSpinBox,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from desktop_dialogs import muted_label
from desktop_widgets import prepare_table
from schedules import assignment_totals, fee_text


class FeeSummary(QWidget):
    def __init__(self, today, parent=None):
        super().__init__(parent)
        self.assignments = []
        current = date.fromisoformat(today)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(16)
        controls = QHBoxLayout()
        controls.setSpacing(10)
        controls.addWidget(QLabel("조회 기간"))
        self.mode = QComboBox()
        self.mode.setAccessibleName("금액 합계 조회 기간")
        for caption, value in (("월별", "month"), ("연간", "year"), ("지정 기간", "range"), ("누적", "all")):
            self.mode.addItem(caption, value)
        self.mode.setMinimumWidth(112)
        controls.addWidget(self.mode)
        self.period_controls = QStackedWidget()
        self.period_controls.setSizePolicy(self.mode.sizePolicy())
        monthly = QWidget()
        monthly_layout = QHBoxLayout(monthly)
        monthly_layout.setContentsMargins(0, 0, 0, 0)
        self.month_year = self.year_input(current.year, "월별 합계 연도")
        self.month = QComboBox()
        self.month.setAccessibleName("금액 합계 월")
        for number in range(1, 13):
            self.month.addItem(f"{number:02d}월", number)
        self.month.setCurrentIndex(current.month - 1)
        monthly_layout.addWidget(self.month_year)
        monthly_layout.addWidget(self.month)
        monthly_layout.addStretch()
        self.period_controls.addWidget(monthly)
        annual = QWidget()
        annual_layout = QHBoxLayout(annual)
        annual_layout.setContentsMargins(0, 0, 0, 0)
        self.year = self.year_input(current.year, "연간 합계 연도")
        annual_layout.addWidget(self.year)
        annual_layout.addStretch()
        self.period_controls.addWidget(annual)
        custom = QWidget()
        custom_layout = QHBoxLayout(custom)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        self.start = self.date_input(QDate(current.year, current.month, 1), "금액 합계 시작일")
        self.end = self.date_input(QDate(current.year, current.month, calendar.monthrange(current.year, current.month)[1]), "금액 합계 종료일")
        custom_layout.addWidget(self.start)
        custom_layout.addWidget(QLabel("~"))
        custom_layout.addWidget(self.end)
        custom_layout.addStretch()
        self.period_controls.addWidget(custom)
        self.period_controls.addWidget(QWidget())
        controls.addWidget(self.period_controls, 1)
        controls.addWidget(muted_label("시작일 기준"))
        layout.addLayout(controls)
        self.period_label = muted_label("")
        self.period_label.setAccessibleName("금액 합계 대상 기간과 일정 수")
        layout.addWidget(self.period_label)
        self.table = QTableWidget()
        prepare_table(self.table, ["항목", "합계", "의뢰비로 계산", "금액 없음"])
        self.table.setAccessibleName("의뢰비와 실수령액 합계")
        self.table.setRowCount(2)
        self.table.setColumnWidth(0, 160)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(2, 112)
        self.table.setColumnWidth(3, 112)
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.setFixedHeight(150)
        layout.addWidget(self.table)
        layout.addStretch()
        self.mode.currentIndexChanged.connect(self.change_mode)
        self.month_year.valueChanged.connect(self.update_totals)
        self.month.currentIndexChanged.connect(self.update_totals)
        self.year.valueChanged.connect(self.update_totals)
        self.start.dateChanged.connect(self.start_changed)
        self.end.dateChanged.connect(self.end_changed)
        self.update_totals()

    @staticmethod
    def year_input(year, name):
        field = QSpinBox()
        field.setRange(1900, 9999)
        field.setValue(year)
        field.setSuffix("년")
        field.setMinimumWidth(110)
        field.setAccessibleName(name)
        field.setKeyboardTracking(False)
        return field

    @staticmethod
    def date_input(value, name):
        field = QDateEdit(value)
        field.setDateRange(QDate(1900, 1, 1), QDate(9999, 12, 31))
        field.setDisplayFormat("yyyy-MM-dd")
        field.setCalendarPopup(True)
        field.setAccessibleName(name)
        field.setKeyboardTracking(False)
        return field

    def change_mode(self, index):
        self.period_controls.setCurrentIndex(index)
        self.update_totals()

    def start_changed(self, value):
        if value > self.end.date():
            self.end.setDate(value)
        self.update_totals()

    def end_changed(self, value):
        if value < self.start.date():
            self.start.setDate(value)
        self.update_totals()

    def set_assignments(self, assignments):
        self.assignments = assignments
        self.update_totals()

    def update_totals(self):
        mode = self.mode.currentData()
        if mode == "month":
            year, month = self.month_year.value(), self.month.currentData()
            start = date(year, month, 1).isoformat()
            end = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
        elif mode == "year":
            start, end = f"{self.year.value():04d}-01-01", f"{self.year.value():04d}-12-31"
        elif mode == "range":
            start, end = self.start.date().toString("yyyy-MM-dd"), self.end.date().toString("yyyy-MM-dd")
        else:
            start = end = None
        totals = assignment_totals(self.assignments, start, end)
        period = f"{start} ~ {end}" if start is not None else "전체 기간"
        self.period_label.setText(f"{period} · 보수 수령·최종 완료 {totals['count']:,}건")
        for row, (key, caption) in enumerate((("fee", "의뢰비"), ("net_fee", "실수령액"))):
            entered = totals[key + "_entered"]
            fallback = totals["net_fee_fallback"] if key == "net_fee" else 0
            amount = fee_text(totals[key]) if entered + fallback or not totals["count"] else "미입력"
            values = (caption, amount, f"{fallback:,}건" if key == "net_fee" else "—", f"{totals['count'] - entered - fallback:,}건")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if column == 1:
                    font = QFont(self.table.font())
                    font.setPointSizeF(12)
                    font.setWeight(QFont.Weight.DemiBold)
                    item.setFont(font)
                self.table.setItem(row, column, item)
