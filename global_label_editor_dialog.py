# global_label_editor_dialog.py

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QFormLayout, QGroupBox,
    QScrollArea, QWidget
)
from PyQt6.QtCore import Qt
from typing import Dict, List

class GlobalLabelEditorDialog(QDialog):
    """Dialog for editing global property labels."""

    def __init__(self, properties: List[str],
                 current_map: Dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Global Property Labels")
        self.setModal(True)
        self.setMinimumSize(525, 400)

        self.line_edits: Dict[str, QLineEdit] = {}

        main_layout = QVBoxLayout(self)

        # --- Instructions ---
        info_label = QLabel(
            "Set custom display labels for property names. "
            "These labels will be used globally across all plots."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("font-style: italic; color: #555;")
        main_layout.addWidget(info_label)

        # --- Scroll Area for Properties ---
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { border: 1px solid #ccc; }")
        main_layout.addWidget(scroll_area)

        scroll_content = QWidget()
        form_layout = QFormLayout(scroll_content)
        form_layout.setContentsMargins(15, 15, 15, 15)
        form_layout.setSpacing(10)
        form_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        # Sort properties for consistent order
        properties.sort()

        for prop in properties:
            label = QLabel(f"{prop}:")
            line_edit = QLineEdit()
            line_edit.setPlaceholderText(f"Default: {prop}")
            line_edit.setText(current_map.get(prop, ""))
            self.line_edits[prop] = line_edit
            form_layout.addRow(label, line_edit)

        scroll_area.setWidget(scroll_content)

        # --- Buttons ---
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        self.ok_btn = QPushButton("Apply")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.accept)
        self.ok_btn.setStyleSheet("background-color: #4CAF50; color: white; padding: 5px;")
        button_layout.addWidget(self.ok_btn)

        main_layout.addLayout(button_layout)

    def get_updated_map(self) -> Dict[str, str]:
        """Returns the new label map."""
        updated_map = {}
        for prop, line_edit in self.line_edits.items():
            text = line_edit.text().strip()
            if text:  # Only add to map if a custom label is provided
                updated_map[prop] = text
        return updated_map
