from PyQt6.QtWidgets import QTableWidget, QAbstractItemView
from PyQt6.QtCore import pyqtSignal

class DraggablePlotTable(QTableWidget):
    """
    A QTableWidget that properly handles moving rows with cell widgets via drag-and-drop.
    """
    rowsMoved = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragDropOverwriteMode(False)

    def dropEvent(self, event):
        if not event.isAccepted() and event.source() == self:
            drop_row = self.rowAt(int(event.position().y()))
            source_row = self.currentRow()

            if drop_row != -1 and drop_row != source_row:
                # Take all items and widgets from the source row
                plot_item = self.takeItem(source_row, 0)
                color_widget = self.cellWidget(source_row, 1)
                style_widget = self.cellWidget(source_row, 2)
                show_widget = self.cellWidget(source_row, 3)
                del_widget = self.cellWidget(source_row, 4)

                # Remove the source row, which shifts rows below it up
                self.removeRow(source_row)

                # Adjust destination row if source was above it
                if source_row < drop_row:
                    drop_row -= 1

                # Insert a new row and put the items and widgets back
                self.insertRow(drop_row)
                self.setItem(drop_row, 0, plot_item)
                self.setCellWidget(drop_row, 1, color_widget)
                self.setCellWidget(drop_row, 2, style_widget)
                self.setCellWidget(drop_row, 3, show_widget)
                self.setCellWidget(drop_row, 4, del_widget)

                self.selectRow(drop_row)
                event.accept()
                self.rowsMoved.emit()

        super().dropEvent(event)
