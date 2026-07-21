# lmp_visualizer/plotting_controller.py

import pyqtgraph as pg
from PyQt6.QtWidgets import QGraphicsScene
from PyQt6.QtGui import QColor, QTransform
from typing import Dict, Any
import numpy as np

class PlottingController:
    """Manages the pyqtgraph PlotWidget and exporting."""
    
    def __init__(self, plot_widget: pg.PlotWidget):
        self.widget = plot_widget
        self.plot_item = self.widget.getPlotItem()
        self.plot_item.showGrid(x=True, y=True)
        self.plot_item.addLegend()
        self.plot_item.getAxis('left').setTextPen('k')
        self.plot_item.getAxis('bottom').setTextPen('k')
        
        self.plots: Dict[str, Dict[str, Any]] = {}  # {plot_name: {item, error_item, ...}}
        self.y_axes: Dict[str, pg.ViewBox] = {}     # {y_col_name: ViewBox}
        
    def add_or_update_plot(self, name: str, data: dict, color: QColor, style):
        """Adds a new plot or updates an existing one."""
        x, y = data['x'], data['y']
        std = data.get('std')
        y_col = name.split('_')[-1]

        # Remove existing plot if it exists
        if name in self.plots:
            self.remove_plot(name)

        # Handle multiple Y-axes
        if y_col not in self.y_axes:
            if not self.y_axes: # First axis is the default one
                self.y_axes[y_col] = self.plot_item.getViewBox()
            else: # Create a new ViewBox for the new axis
                vb = pg.ViewBox()
                ax = pg.AxisItem('right')
                self.plot_item.layout.addItem(ax, 2, len(self.y_axes) + 2)
                self.plot_item.scene().addItem(vb)
                ax.linkToView(vb)
                vb.setXLink(self.plot_item.getViewBox())
                self.y_axes[y_col] = vb
        
        view_box = self.y_axes[y_col]

        # Create plot items
        pen = pg.mkPen(color=color, style=style)
        plot_item = pg.PlotDataItem(x, y, pen=pen, name=name)
        
        error_item = None
        if std is not None and np.any(std):
            brush = pg.mkBrush(color=color.red(), green=color.green(), blue=color.blue(), alpha=70)
            error_item = pg.FillBetweenItem(
                pg.PlotDataItem(x, y - std),
                pg.PlotDataItem(x, y + std),
                brush=brush
            )
            view_box.addItem(error_item)

        view_box.addItem(plot_item)
        self.plots[name] = {'item': plot_item, 'error_item': error_item, 'view_box': view_box}

        self.update_views()
        
    def remove_plot(self, name: str):
        """Removes a plot from the graph."""
        if name in self.plots:
            plot_info = self.plots.pop(name)
            plot_info['view_box'].removeItem(plot_info['item'])
            if plot_info['error_item']:
                plot_info['view_box'].removeItem(plot_info['error_item'])
    
    def update_views(self):
        """Update the range of all linked views."""
        main_vb = self.plot_item.getViewBox()
        main_vb.setGeometry(self.plot_item.vb.sceneBoundingRect())
        
        for vb in self.y_axes.values():
            if vb is not main_vb:
                vb.setGeometry(main_vb.sceneBoundingRect())
                vb.linkedViewChanged(main_vb, vb.XAxis)

    def set_visibility(self, name: str, visible: bool):
        if name in self.plots:
            self.plots[name]['item'].setVisible(visible)
            if self.plots[name]['error_item']:
                self.plots[name]['error_item'].setVisible(visible)

    def set_axis_labels(self, x_label: str, y_labels: Dict[str, str]):
        self.plot_item.setLabel('bottom', text=x_label)
        
        # Set primary y-axis label
        if y_labels:
            first_label = list(y_labels.values())[0]
            self.plot_item.setLabel('left', text=first_label)
        
        # Find the axis item associated with each viewbox
        for i, item in enumerate(self.plot_item.layout.items()):
            if isinstance(item, pg.AxisItem) and item.orientation == 'right':
                # This is a bit of a hack, assumes order. A better way would be to store the axis item.
                # For now this is a simple implementation.
                pass


    def export_plot(self, filename: str):
        """Exports the current plot view using Matplotlib for high quality output."""
        try:
            import matplotlib.pyplot as plt
        except ImportError:
            print("Matplotlib is required for exporting.")
            return

        fig, ax = plt.subplots()
        
        # Collect all items and their styles
        plot_data = []
        for name, p in self.plots.items():
            if p['item'].isVisible():
                data = p['item'].getData()
                pen = p['item'].opts['pen']
                color = pen.color().getRgbF()
                
                error_data = None
                if p['error_item']:
                    curve1_data = p['error_item'].curves[0].getData()
                    curve2_data = p['error_item'].curves[1].getData()
                    error_data = (curve1_data[0], curve1_data[1], curve2_data[1])

                plot_data.append({'x': data[0], 'y': data[1], 'color': color, 'label': name, 'error': error_data})

        # Plot on matplotlib axis
        for p in plot_data:
            ax.plot(p['x'], p['y'], color=p['color'], label=p['label'])
            if p['error']:
                ax.fill_between(p['error'][0], p['error'][1], p['error'][2], color=p['color'], alpha=0.25)
        
        ax.set_xlabel(self.plot_item.getAxis('bottom').labelText)
        ax.set_ylabel(self.plot_item.getAxis('left').labelText)
        ax.grid(True)
        ax.legend()
        fig.tight_layout()
        
        try:
            fig.savefig(filename, bbox_inches='tight')
            print(f"Plot exported to {filename}")
        except Exception as e:
            print(f"Failed to save plot: {e}")
        plt.close(fig)