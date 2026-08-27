from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QCheckBox, QPushButton, QDialogButtonBox, QWidget
)
from PyQt6.QtCore import Qt
from typing import Dict, Tuple, Set


class HeaderSelectionDialog(QDialog):
    """Lets user choose which thermo header types to include.

    One row per distinct header tuple, with all keywords wrapped across
    multiple lines. Checked rows are included in parsing.
    """
    def __init__(self, header_groups: Dict[Tuple[str, ...], Dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Thermo Headers")
        self.setMinimumWidth(1080)
        self.setMinimumHeight(360)
        self.resize(1080, 360)
        self.header_groups = header_groups
        # Order = appearance in files (insertion order of header_groups), not sorted by rows.
        # This shows minimize (7-col) first, then production (41-col) as they occur in the log.
        self.ordered = list(header_groups.items())
        # Keep header tuple for each row
        self.header_for_row = [h for h, _ in self.ordered]
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        total_files = sum(g.get('file_count', 0) for g in self.header_groups.values())
        total_rows = sum(g.get('rows', 0) for g in self.header_groups.values())
        # Count distinct signatures
        sig_count = len(self.header_groups)
        title = QLabel(f"Found {sig_count} header type(s) across {total_files} file(s) - {total_rows} data rows")
        title.setWordWrap(True)
        layout.addWidget(title)

        hint = QLabel("Uncheck a header to ignore its data blocks. All keywords per header are shown wrapped.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#666; font-size:12px;")
        layout.addWidget(hint)

        self.table = QTableWidget(len(self.ordered), 4)
        self.table.setHorizontalHeaderLabels(["Include", "Header (all keywords)", "Files", "Rows"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 70)
        self.table.setColumnWidth(2, 60)
        self.table.setColumnWidth(3, 70)
        self.table.verticalHeader().hide()
        self.table.setWordWrap(True)

        for row, (hdr, info) in enumerate(self.ordered):
            # Checkbox
            cb = QCheckBox()
            # Default: check headers that cover most data; if only one header, all checked
            # Heuristic: check if rows > 1 or largest header
            # Keep large production header checked, small minimize unchecked when both present
            # We default to checking headers with > 2 rows or the max-rows header
            max_rows = max(g.get('rows', 0) for g in self.header_groups.values()) if self.header_groups else 0
            should_check = info.get('rows', 0) == max_rows or info.get('rows', 0) > 2 or len(self.header_groups) == 1
            cb.setChecked(should_check)
            # Center checkbox
            cell_widget = QWidget()
            hl = QHBoxLayout(cell_widget)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hl.addWidget(cb)
            self.table.setCellWidget(row, 0, cell_widget)
            # Store checkbox for retrieval
            # Use property on cell widget
            cell_widget.setProperty("hdr", hdr)

            # Header keywords - wrapped
            keywords = " ".join(hdr)
            item = QTableWidgetItem(keywords)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setToolTip(keywords)
            # Allow wrapping; set text alignment top
            self.table.setItem(row, 1, item)

            files_item = QTableWidgetItem(str(info.get('file_count', 0)))
            files_item.setFlags(files_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            files_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            # Tooltip: file names one per line
            try:
                files = sorted(info.get('files', []))
                files_item.setToolTip("\n".join(files) if files else "")
            except Exception:
                files_item.setToolTip("")
            self.table.setItem(row, 2, files_item)

            rows_item = QTableWidgetItem(str(info.get('rows', 0)))
            rows_item.setFlags(rows_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            rows_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, rows_item)

        # Size rows to fit wrapped header text
        self.table.resizeRowsToContents()
        # Ensure at least reasonable height per row
        for row in range(self.table.rowCount()):
            h = self.table.rowHeight(row)
            if h < 36:
                self.table.setRowHeight(row, 36)
            elif h > 100:
                self.table.setRowHeight(row, 100)
        layout.addWidget(self.table)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        # Add Select All / None helpers
        extra_layout = QHBoxLayout()
        btn_all = QPushButton("Select All")
        btn_none = QPushButton("Select None")
        btn_all.clicked.connect(self._select_all)
        btn_none.clicked.connect(self._select_none)
        extra_layout.addWidget(btn_all)
        extra_layout.addWidget(btn_none)
        extra_layout.addStretch()
        extra_layout.addWidget(btn_box)
        layout.addLayout(extra_layout)

        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)

    def _select_all(self):
        for r in range(self.table.rowCount()):
            w = self.table.cellWidget(r, 0)
            cb = w.findChild(QCheckBox) if w else None
            if cb:
                cb.setChecked(True)

    def _select_none(self):
        for r in range(self.table.rowCount()):
            w = self.table.cellWidget(r, 0)
            cb = w.findChild(QCheckBox) if w else None
            if cb:
                cb.setChecked(False)

    def get_selected_headers(self) -> Set[Tuple[str, ...]]:
        selected: Set[Tuple[str, ...]] = set()
        for r in range(self.table.rowCount()):
            w = self.table.cellWidget(r, 0)
            cb = w.findChild(QCheckBox) if w else None
            if cb and cb.isChecked():
                selected.add(self.header_for_row[r])
        return selected

    def get_selected_or_all(self) -> Set[Tuple[str, ...]]:
        sel = self.get_selected_headers()
        # If user unchecks all, treat as keep all to avoid empty result
        if not sel:
            return set(self.header_groups.keys())
        return sel
