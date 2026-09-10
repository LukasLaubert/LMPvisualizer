import pyqtgraph as pg
from PyQt6.QtGui import QColor
from typing import Dict, Any
import numpy as np
import copy
import re
import plot_model
from logger_setup import get_logger

logger = get_logger(__name__)

# Custom AxisItem that separates axis line color from grid/tick color
class ColoredAxis(pg.AxisItem):
    """AxisItem with separate colors for axis line and grid/ticks."""
    
    def __init__(self, orientation, pen=None, textPen=None, axisPen=None, linkView=None, parent=None, maxTickLength=-5, showValues=True, text='', units='', unitPrefix='', **args):
        super().__init__(orientation, pen=pen, textPen=textPen, linkView=linkView, parent=parent, maxTickLength=maxTickLength, showValues=showValues, text=text, units=units, unitPrefix=unitPrefix, **args)
        self.axisPen = axisPen
        if self.axisPen is None:
            self.axisPen = self.pen()
    
    def drawPicture(self, p, axisSpec, tickSpecs, textSpecs):
        """Override drawPicture to use separate pen for axis line."""
        p.setRenderHint(p.RenderHint.Antialiasing, False)
        p.setRenderHint(p.RenderHint.TextAntialiasing, True)
        
        ## draw long line along axis line, using axisPen
        pen, p1, p2 = axisSpec
        p.setPen(self.axisPen)
        p.drawLine(p1, p2)
        
        ## draw ticks using normal pen (grid color)
        for pen, p1, p2 in tickSpecs:
            p.setPen(pen)
            p.drawLine(p1, p2)
        
        ## Draw all text
        if self.style['tickFont'] is not None:
            p.setFont(self.style['tickFont'])
        p.setPen(self.textPen())
        bounding = self.boundingRect().toAlignedRect()
        p.setClipRect(bounding)
        for rect, flags, text in textSpecs:
            p.drawText(rect, int(flags), text)
    
    def setAxisPen(self, pen):
        """Set the pen used for drawing the axis line."""
        self.axisPen = pen
        self.picture = None
        self.update()


class LogController:
    """Manages the pyqtgraph PlotWidget and its items."""
    
    def __init__(self, plot_widget: pg.PlotWidget):
        self.widget = plot_widget
        self.plot_item = self.widget.getPlotItem()
        self.plot_item.showGrid(x=True, y=True)
        self.plot_item.addLegend()
        self.plot_item.getAxis('left').setTextPen('k')
        self.plot_item.getAxis('bottom').setTextPen('k')
        
        # Single dictionary to hold all plot-related items by name
        self.plots: Dict[str, Dict[str, Any]] = {}
        # Dictionary to manage y-axes and their associated viewboxes
        self.y_axes: Dict[str, Dict[str, Any]] = {}
        # Store axis colors and labels for export
        self.y_axis_colors: Dict[str, QColor] = {}
        self.y_axis_labels: Dict[str, str] = {}
        self.x_axis_label: str = ""
        
        self.axes_locked = False
        self.scale_locked = False
        self._is_syncing_axes = False # Flag to prevent recursive signal handling
        self._in_update_views = False
        try:
            self.plot_item.getViewBox().sigResized.connect(self.update_views)
        except Exception:
            pass
        # The auto-scale 'A' button in the bottom-left only knows the main
        # ViewBox. With several y axes it would re-enable auto-range for X
        # (and Y of the main axis) but leave every auxiliary ViewBox untouched.
        # Intercept the button so every Y axis is re-autoscaled, with the
        # current alignment/scale locks honoured (reset_view already does).
        try:
            self.plot_item.autoBtn.clicked.disconnect(self.plot_item.autoBtnClicked)
        except Exception:
            pass
        try:
            self.plot_item.autoBtn.clicked.connect(self._on_auto_btn_clicked)
        except Exception:
            pass

    def toggle_axes_lock(self, locked: bool, reset_view: bool = True):
        """Connects or disconnects the synchronization signal for all active Y-axes."""
        self.axes_locked = locked
        
        # Iterate over all existing viewboxes to connect or disconnect the signal
        for axis_info in self.y_axes.values():
            vb = axis_info['viewbox']
            # First, always try to disconnect to prevent duplicate connections
            try:
                vb.sigRangeChanged.disconnect(self._on_axis_range_changed)
            except (TypeError, RuntimeError):
                pass # Ignore if it wasn't connected
            
            # If locking (either 0-lock or scale-lock), reconnect the signal
            if self.axes_locked or self.scale_locked:
                vb.sigRangeChanged.connect(self._on_axis_range_changed)
        
        # Trigger reset view to apply the new locking logic immediately
        if reset_view and self.y_axes:
            self.reset_view()

    def toggle_scale_lock(self, locked: bool):
        """Toggles scaling lock state and updates connections."""
        self.scale_locked = locked
        
        # Re-evaluate connections
        for axis_info in self.y_axes.values():
            vb = axis_info['viewbox']
            try:
                vb.sigRangeChanged.disconnect(self._on_axis_range_changed)
            except (TypeError, RuntimeError):
                pass

            if self.axes_locked or self.scale_locked:
                vb.sigRangeChanged.connect(self._on_axis_range_changed)
        
        # Trigger immediate sync if enabling scale lock
        if locked and self.y_axes:
            self.reset_view()

    def align_zero_preserve_scale(self, source_vb: pg.ViewBox = None):
        """Align y-axis zero positions without changing current y-range heights."""
        if len(self.y_axes) < 2:
            return

        source_vb = source_vb or self.plot_item.getViewBox()
        src_min, src_max = source_vb.viewRange()[1]
        src_height = src_max - src_min
        if src_height == 0:
            return

        src_zero_rel = -src_min / src_height
        was_syncing = self._is_syncing_axes
        self._is_syncing_axes = True
        try:
            for axis_info in self.y_axes.values():
                vb = axis_info['viewbox']
                if vb is source_vb:
                    continue

                curr_min, curr_max = vb.viewRange()[1]
                curr_height = curr_max - curr_min
                if curr_height == 0:
                    continue

                new_min = -src_zero_rel * curr_height
                vb.setYRange(new_min, new_min + curr_height, padding=0)
        finally:
            self._is_syncing_axes = was_syncing

    def apply_current_locks(self):
        """Forces synchronization/reset of views. Useful after reloading plots."""
        # This ensures the view resets (to auto-fit) even when No Locks are active.
        if self.y_axes:
            self.reset_view()

    def _on_axis_range_changed(self, changed_vb: pg.ViewBox):
        """Signal handler that triggers when any connected axis is moved."""
        if self._is_syncing_axes or not (self.axes_locked or self.scale_locked):
            return

        self._is_syncing_axes = True
        try:
            self._synchronize_axes(source_vb=changed_vb)
        finally:
            self._is_syncing_axes = False

    def _synchronize_axes(self, source_vb: pg.ViewBox):
        """
        Synchronizes Y-axes based on current lock settings.
        - If Scale Locked: Syncs height (zoom level).
        - If Axes Locked: Syncs relative zero position.
        - If Both: Syncs both (effectively identical ranges).
        """
        if len(self.y_axes) < 2:
            return

        # Get source parameters
        src_range = source_vb.viewRange()[1]
        src_min, src_max = src_range
        src_height = src_max - src_min
        if src_height == 0: return

        # Calculate the relative position of the zero line on the source axis
        src_zero_rel = -src_min / src_height

        # Apply to all other axes
        for axis_info in self.y_axes.values():
            vb = axis_info['viewbox']
            if vb is source_vb:
                continue 

            curr_range = vb.viewRange()[1]
            curr_min, curr_max = curr_range
            curr_height = curr_max - curr_min
            if curr_height == 0: continue
            
            # Step 1: Determine new height (zoom)
            new_height = curr_height
            if self.scale_locked:
                new_height = src_height
            
            # Step 2: Determine new position (min)
            if self.axes_locked:
                # Align 0 position relative to height
                new_min = -src_zero_rel * new_height
            else:
                if self.scale_locked:
                    # If only scale is locked, preserve the current center of the target view
                    curr_center = (curr_min + curr_max) / 2
                    new_min = curr_center - (new_height / 2)
                else:
                    # If no lock active (shouldn't happen in this loop), keep current
                    new_min = curr_min

            new_max = new_min + new_height
            vb.setYRange(new_min, new_max, padding=0)

    def add_or_update_plot(self, name: str, data: dict, color: QColor, style, thickness: float = 1.0, layer_priority: int = 0):
        """Adds a new plot or updates an existing one by name."""
        self._add_or_update_plot_impl(name, data, color, style, layer_priority=layer_priority, thickness=thickness)

    def add_or_update_plot_with_custom_colors(self, name: str, data: dict, color: QColor, style, layer_priority: int = 0, thickness: float = 1.0):
        """Adds a new plot or updates an existing one by name with custom layer priority."""
        self._add_or_update_plot_impl(name, data, color, style, layer_priority, thickness=thickness)

    def _add_or_update_plot_impl(self, name: str, data: dict, color: QColor, style, layer_priority: int = 0, thickness: float = 1.0):
        """Internal implementation for adding/updating plots with layer priority."""
        if name in self.plots:
            self.remove_plot(name)

        x, y = data['x'], data['y']
        std = data.get('std')
        y_col_name = data.get('y_col')
        error_alpha_multiplier = data.get('error_alpha_multiplier', 0.5)
        error_layer_priority = data.get('error_layer_priority')
        error_color = data.get('error_color', color)

        if not y_col_name:
            return

        # --- Y-Axis and ViewBox Management ---
        if y_col_name not in self.y_axes:
            if not self.y_axes: # First axis is the default left one
                vb = self.plot_item.getViewBox()
                self.y_axes[y_col_name] = {
                    'axis': self.plot_item.getAxis('left'),
                    'viewbox': vb
                }
            else:  # Create a new ViewBox and Axis on the right for subsequent plots
                vb = pg.ViewBox()
                ax = ColoredAxis('right')

                # Assign a decreasing Z-value so that the first added right axis (inner) stays 'above' subsequent axes (outer) in the scene stack regarding event capture.
                ax.setZValue(1000 - len(self.y_axes))

                self.plot_item.layout.addItem(ax, 2, len(self.y_axes) + 2)
                self.plot_item.scene().addItem(vb)
                ax.linkToView(vb)
                vb.setXLink(self.plot_item.getViewBox())
                self.y_axes[y_col_name] = {'axis': ax, 'viewbox': vb}
            
            # If any lock is active, connect the signal to the newly created viewbox
            if self.axes_locked or self.scale_locked:
                self.y_axes[y_col_name]['viewbox'].sigRangeChanged.connect(self._on_axis_range_changed)

        view_box = self.y_axes[y_col_name]['viewbox']

        # --- Create Plot Items ---
        pen = pg.mkPen(color=color, style=style, width=thickness)
        plot_data_item = pg.PlotDataItem(x, y, pen=pen, name=name)
        
        error_item = None
        if std is not None and np.any(std):
            r, g, b, a = error_color.getRgb()
            brush_alpha = max(0, min(255, int(a * error_alpha_multiplier)))
            brush = pg.mkBrush(color=pg.mkColor(r, g, b, brush_alpha))
            
            # FIX: Initialize FillBetweenItem with its required curve arguments
            error_item = pg.FillBetweenItem(
                curve1=pg.PlotDataItem(x, y - std),
                curve2=pg.PlotDataItem(x, y + std),
                brush=brush
            )
            view_box.addItem(error_item)
        
        view_box.addItem(plot_data_item)
        
        z_value = layer_priority * 10
        if error_item:
            if error_layer_priority is not None:
                error_item.setZValue(error_layer_priority * 10)
            else:
                error_item.setZValue(z_value)
        plot_data_item.setZValue(z_value + 1)
        
        # If this plot has high priority (e.g. selected), ensure its ViewBox is also on top
        # to prevent it from being obscured by other ViewBoxes (axes).
        if layer_priority > 0:
            view_box.setZValue(100)
        
        self.plots[name] = {
            'item': plot_data_item, 
            'error_item': error_item, 
            'view_box': view_box,
            'layer_priority': layer_priority,
            'y_col': y_col_name,  # Store y_col for export
            'y_label': data.get('y_label', y_col_name),
            'x_col': data.get('x_col', ''),
            'x_label': data.get('x_label', data.get('x_col', '')),
            'row': data.get('row', 1_000_000),  # table row for legend order; fits use large default
            'plot_id': data.get('plot_id'),  # stable table row id for preset matching
            'std_type': data.get('std_type'),  # raw-inter / running:* / smooth-inter, for text export
            'mean_info': data.get('mean_info'),  # smoothing provenance for text export source row
            'fit_info': data.get('fit_info'),  # fit function/range/params for text export source row
        }
        self.update_views()

    def remove_plot(self, name: str):
        """Removes a plot and its associated items from the graph."""
        if name in self.plots:
            plot_info = self.plots.pop(name)
            if plot_info['item']:
                plot_info['view_box'].removeItem(plot_info['item'])
            if plot_info['error_item']:
                plot_info['view_box'].removeItem(plot_info['error_item'])
    
    def clear_all_plots(self):
        """Removes all plots, axes, and viewboxes from the graph. Preserves lock states."""
        # Disconnect signals from current viewboxes before deleting them
        for axis_info in self.y_axes.values():
            try:
                axis_info['viewbox'].sigRangeChanged.disconnect(self._on_axis_range_changed)
            except (TypeError, RuntimeError):
                pass

        for name in list(self.plots.keys()):
            self.remove_plot(name)
            
        # Clear the dynamically added axes and viewboxes.
        # Teardown must mirror creation exactly (layout.addItem + scene().addItem +
        # linkToView). Taking the axis out of the layout alone leaves it parented in
        # the scene - still painted, and still linked to a ViewBox we are about to
        # drop - which crashes GraphicsView.paintEvent once that ViewBox is collected.
        for y_col, axis_info in list(self.y_axes.items()):
            axis, viewbox = axis_info['axis'], axis_info['viewbox']
            # Don't remove the default left axis
            if axis is not self.plot_item.getAxis('left'):
                self.plot_item.layout.removeItem(axis)
                try:
                    axis.unlinkFromView()
                except Exception:
                    pass
                if axis.scene():
                    axis.scene().removeItem(axis)
                if viewbox.scene():
                    self.plot_item.scene().removeItem(viewbox)

        self.plots.clear()
        self.y_axes.clear()
        self.y_axis_colors.clear()  # Clear stored colors
        self.y_axis_labels.clear()  # Clear stored labels
        self.x_axis_label = ""       # Clear x-axis label
        
        # Reset main ViewBox Z-value to default
        self.plot_item.getViewBox().setZValue(0)
        
        # Note: axes_locked and scale_locked flags are preserved

    def update_views(self, *args):
        """Updates the geometry of all viewboxes to match the main one."""
        if getattr(self, '_in_update_views', False):
            return
        self._in_update_views = True
        try:
            main_vb = self.plot_item.getViewBox()
            if not main_vb or not main_vb.scene():
                return
                
            # Ensure the main viewbox geometry is correct first
            main_vb_rect = self.plot_item.vb.sceneBoundingRect()
            main_vb.setGeometry(main_vb_rect)
            
            # Update all other viewboxes to match this geometry
            for axis_info in self.y_axes.values():
                vb = axis_info['viewbox']
                if vb is not main_vb:
                    vb.setGeometry(main_vb_rect)
                    vb.linkedViewChanged(main_vb, vb.XAxis)

            # Geometry settled (or is about to): heal any axis label sitting at
            # a previous text's center.
            self._recenter_axis_labels()
        finally:
            self._in_update_views = False

    def _recenter_axis_labels(self):
        """Recompute every axis label position from current geometry.

        AxisItem only repositions its label in resizeEvent, so a label change
        that leaves the item size untouched leaves the label sitting at the
        previous text's center - and a long label then grows off-center. The
        persistent default axes (left, bottom) are exposed to this on every
        label change, while recreated right axes only get away with it by luck
        of a fresh layout pass. Re-running pyqtgraph's own computation heals
        every such state at once.
        """
        axes = []
        try:
            for key in ('left', 'right', 'top', 'bottom'):
                axes.append(self.plot_item.getAxis(key))
        except Exception:
            pass
        try:
            for info in self.y_axes.values():
                axes.append(info.get('axis'))
        except Exception:
            pass
        seen = set()
        for axis in axes:
            if axis is None or id(axis) in seen:
                continue
            seen.add(id(axis))
            try:
                axis.resizeEvent(None)
            except Exception:
                pass

    def set_axis_labels(self, x_label: str, y_labels: Dict[str, str]):
        self.plot_item.setLabel('bottom', text=x_label)
        self.x_axis_label = x_label  # Store for export
        
        # Reset labels for all managed axes
        for axis_info in self.y_axes.values():
            axis_info['axis'].setLabel(text="")

        # Set new labels only for currently plotted data
        for y_col, label in y_labels.items():
            if y_col in self.y_axes:
                self.y_axes[y_col]['axis'].setLabel(text=label)
                self.y_axis_labels[y_col] = label  # Store for export

        # Labels were just swapped on persistent items: reposition from the
        # live geometry instead of waiting for a size change that may never
        # come (see _recenter_axis_labels).
        self._recenter_axis_labels()

    def set_axis_color(self, y_col: str, color: QColor):
        """Sets the color of a specific y-axis, including its label and axis line.
        Grid lines remain black/gray regardless of axis color."""
        if y_col in self.y_axes:
            axis = self.y_axes[y_col]['axis']
            # Set text color
            axis.setTextPen(color)
            # Use setAxisPen for ColoredAxis to only affect axis line, not grid
            if isinstance(axis, ColoredAxis):
                axis.setAxisPen(pg.mkPen(color=color, width=1))
            else:
                axis.setPen(color)
            self.y_axis_colors[y_col] = color  # Store for export

    @staticmethod
    def _viewbox_axis_inverted(viewbox, axis: str) -> bool:
        key = 'xInverted' if axis == 'x' else 'yInverted'
        return bool(getattr(viewbox, 'state', {}).get(key, False))

    @staticmethod
    def _oriented_range(values, inverted: bool):
        return plot_model.oriented_limits(values, inverted)

    def _x_inverted(self) -> bool:
        return self._viewbox_axis_inverted(self.plot_item.getViewBox(), 'x')

    def _y_inverted(self, y_col: str) -> bool:
        if y_col in self.y_axes:
            return self._viewbox_axis_inverted(self.y_axes[y_col]['viewbox'], 'y')
        return False

    @staticmethod
    def _format_export_value(value):
        return plot_model.format_export_value(value)

    @staticmethod
    def _subset_positions(base_x, sub_x):
        return plot_model.subset_positions(base_x, sub_x)

    @staticmethod
    def _align_values(length, positions, values):
        return plot_model.align_values(length, positions, values)

    def export_plot(self, filename: str, figsize=None, fits_at_data_x=False):
        # Dispatch to text export if applicable
        if filename.lower().endswith(('.csv', '.tsv')):
            self._export_text_data(filename, fits_at_data_x=fits_at_data_x)
            return

        try:
            import matplotlib.pyplot as plt
        except ImportError:
            logger.warning("Matplotlib is required for exporting.")
            print("Matplotlib is required for exporting.")
            return

        plots_by_yaxis = {}
        # Track max priority per axis to sort axes later
        axis_max_priority = {}

        for name, plot_info in self.plots.items():
            item = plot_info.get('item')
            if not item or not item.isVisible(): continue
            y_col = plot_info.get('y_col')
            if not y_col: continue
            if y_col not in plots_by_yaxis: plots_by_yaxis[y_col] = []
            plots_by_yaxis[y_col].append((name, plot_info))

            prio = plot_info.get('layer_priority', 0)
            if y_col not in axis_max_priority:
                axis_max_priority[y_col] = prio
            else:
                axis_max_priority[y_col] = max(axis_max_priority[y_col], prio)

        # Sort plots within each axis by priority
        for y_col in plots_by_yaxis:
            plots_by_yaxis[y_col].sort(key=lambda x: x[1].get('layer_priority', 0))

        if not plots_by_yaxis: return

        y_axis_order = [y_col for y_col in self.y_axes.keys() if y_col in plots_by_yaxis]
        # Sort axes so that higher priority ones are drawn later (on top)
        y_axis_order.sort(key=lambda y: axis_max_priority.get(y, 0))

        if not y_axis_order: return

        fig, ax_primary = plt.subplots(figsize=figsize if figsize else (10, 6))

        matplotlib_axes = plot_model.setup_multi_y_axes(ax_primary, y_axis_order)

        # Apply Limits, preserving PyQtGraph axis inversion.
        vb_main = self.plot_item.getViewBox()
        ax_primary.set_xlim(plot_model.oriented_limits(vb_main.viewRange()[0], self._x_inverted()))

        for y_col in y_axis_order:
            ax = matplotlib_axes[y_col]
            if y_col in self.y_axes:
                vb = self.y_axes[y_col]['viewbox']
                ax.set_ylim(plot_model.oriented_limits(vb.viewRange()[1], self._y_inverted(y_col)))

        # Plot Curves - legend = table row order, selected at bottom (most top plot last)
        legend_entries = []  # (is_selected, row, sub_prio, handle, label)

        for y_col in y_axis_order:
            ax = matplotlib_axes[y_col]
            axis_color = self.y_axis_colors.get(y_col, QColor("black"))
            mpl_axis_color = plot_model.qcolor_to_rgb(axis_color, default=(0.0, 0.0, 0.0))

            try:
                ax.spines['left' if y_col == y_axis_order[0] else 'right'].set_edgecolor(mpl_axis_color)
            except Exception:
                pass
            plot_model.apply_axis_style(
                ax,
                ylabel=self.y_axis_labels.get(y_col, y_col),
                axis_color=mpl_axis_color,
                grid=False,
            )

            for name, plot_info in plots_by_yaxis[y_col]:
                item = plot_info['item']
                data = item.getData()
                if not all(d is not None for d in data) or len(data[0]) == 0: continue

                pen = item.opts['pen']
                color = plot_model.qcolor_to_rgba(pen.color())
                width = pen.width()
                linestyle = plot_model.qt_pen_style_to_mpl(pen.style())

                error_item = plot_info.get('error_item')
                std = None
                if error_item is not None:
                    try:
                        c1 = error_item.curves[0].getData()
                        c2 = error_item.curves[1].getData()
                        if all(d is not None for d in c1) and all(d is not None for d in c2):
                            std = np.abs(np.asarray(c2[1]) - np.asarray(c1[1])) / 2.0
                    except Exception:
                        std = None

                # Consume the single shared series model, then render it.
                # Previously the export drew only the fill when std was
                # present (no line); it now draws fill + line via the
                # shared helper so live / popout / export converge.
                series = plot_model.make_series(
                    f"{y_col}_{name}", name,
                    np.asarray(data[0]), np.asarray(data[1]), std,
                    color=pen.color(), linestyle_matlab=linestyle,
                    linestyle_qt=pen.style(), width=width,
                    layer_priority=plot_info.get('layer_priority', 0),
                    row=plot_info.get('row', plot_model.DEFAULT_ROW),
                )
                _, _, entries = plot_model.render_series(
                    ax, series, color=color, linestyle=linestyle,
                    linewidth=width, marker='None',
                    show_std=True, show_std_legend=True, show_legend=True,
                )
                legend_entries.extend(entries)
        legend_entries = plot_model.sort_legend_entries(legend_entries)
        all_handles = [h for _, _, _, h, _ in legend_entries]
        all_labels = [l for _, _, _, _, l in legend_entries]

        plot_model.apply_axis_style(ax_primary, xlabel=self.x_axis_label,
                                    grid=True, grid_alpha=0.3)
        try:
            ax_primary.spines['bottom'].set_edgecolor('black')
            ax_primary.tick_params(axis='x', colors='black', direction='in')
            ax_primary.xaxis.label.set_color('black')
        except Exception:
            pass

        if all_handles:
            target_ax = matplotlib_axes[y_axis_order[-1]]
            leg = target_ax.legend(all_handles, all_labels, loc='best')
            leg.set_zorder(10000)

        fig.tight_layout()
        try:
            fig.savefig(filename, bbox_inches='tight', dpi=300)
            logger.info("Plot exported to %s", filename)
        except Exception as e:
            logger.warning("Failed to save plot: %s", e)
        finally:
            plt.close(fig)

    def _export_text_data(self, filename: str, fits_at_data_x=False):
        """Internal handler for exporting data to CSV or TSV."""
        import csv

        delimiter = '\t' if filename.lower().endswith('.tsv') else ','

        # Suffixes of band-only plots: their fill belongs to the parent
        # line, never to a dataset of their own (their line is NoPen).
        BAND_SUFFIXES = ("_running_mean_std_inter", "_running_mean_std")

        def _band_base(name: str):
            for suffix in BAND_SUFFIXES:
                if name.endswith(suffix):
                    return name[:-len(suffix)]
            return None

        def _band_values(error_item):
            """(std_values, fill QColor) read back from a drawn band."""
            try:
                c1 = error_item.curves[0].getData()
                c2 = error_item.curves[1].getData()
                if c1[1] is None or c2[1] is None:
                    return None, None
                std = np.abs(np.asarray(c2[1]) - np.asarray(c1[1])) / 2.0
                try:
                    fill = error_item.brush().color()
                except Exception:
                    fill = None
                return std, fill
            except Exception:
                return None, None

        # Virtual average systems carry the combined "average & std" token in
        # the legend name, but an export column holds exactly one statistic:
        # a y-column is the average line, a std-column is the spread. Split
        # the token accordingly so each header names what it contains.
        # Fit labels ("Fit: ...") are left untouched.
        _AVG_SYSTEMS = ("average", "average & std")

        def _export_label(name: str, stat: str):
            if name.startswith("Fit:"):
                return name
            parts = name.split(" | ", 3)
            if len(parts) == 4 and parts[1] in _AVG_SYSTEMS:
                parts[1] = stat
                return " | ".join(parts)
            return name

        plots_by_yaxis = {}
        axis_max_priority = {}
        band_plots = []
        datasets_by_name = {}
        for name, plot_info in self.plots.items():
            item = plot_info.get('item')
            if not item or not item.isVisible():
                continue
            y_col = plot_info.get('y_col')
            if not y_col:
                continue

            if _band_base(name) is not None:
                band_plots.append((name, plot_info))
                continue

            plots_by_yaxis.setdefault(y_col, []).append((name, plot_info))
            axis_max_priority[y_col] = max(axis_max_priority.get(y_col, 0), plot_info.get('layer_priority', 0))

        if not plots_by_yaxis:
            return

        y_axis_order = [y_col for y_col in self.y_axes.keys() if y_col in plots_by_yaxis]
        y_axis_order.sort(key=lambda y: axis_max_priority.get(y, 0))

        sorted_plot_list = []
        for y_col in y_axis_order:
            sorted_plot_list.extend(sorted(plots_by_yaxis[y_col], key=lambda x: x[1].get('layer_priority', 0)))

        x_groups = []

        for name, plot_info in sorted_plot_list:
            item = plot_info['item']
            x_data, y_data = item.getData()
            if x_data is None or y_data is None:
                continue

            x_values = np.asarray(x_data)
            y_values = np.asarray(y_data)

            # Own band (Orig pale / average std): values read back from
            # the drawn fill, type tagged at draw time.
            stds = []
            if plot_info.get('error_item') is not None:
                std_values, fill = _band_values(plot_info['error_item'])
                if std_values is not None:
                    stds.append({
                        'values': np.asarray(std_values),
                        'type': plot_info.get('std_type') or 'raw-inter',
                        'color': plot_model.qcolor_to_hex(fill) if fill is not None else '',
                        'x': x_values,
                    })

            try:
                line_hex = plot_model.qcolor_to_hex(item.opts['pen'].color())
            except Exception:
                line_hex = ''

            dataset = {
                'name': name,
                'info': plot_info,
                'x': x_values,
                'y': y_values,
                'stds': stds,
                'color': line_hex,
            }

            target_group = None
            target_positions = None
            for group in x_groups:
                positions = self._subset_positions(group['x'], x_values)
                if positions is not None:
                    target_group = group
                    target_positions = positions
                    break

            if target_group is None:
                x_label = plot_info.get('x_label', plot_info.get('x_col', self.x_axis_label))
                target_group = {'x': x_values, 'x_label': x_label, 'datasets': []}
                target_positions = list(range(len(x_values)))
                x_groups.append(target_group)

            entry = {
                'name': dataset['name'],
                'info': dataset['info'],
                'y': self._align_values(len(target_group['x']), target_positions, dataset['y']),
                'color': dataset['color'],
                'stds': dataset['stds'],
                'positions': target_positions,
            }
            target_group['datasets'].append(entry)
            datasets_by_name[name] = entry

        # Attach band-only plots to their parent line (mean line first,
        # Orig base as fallback). Orphan bands are skipped: without the
        # parent line they have no meaning as a dataset.
        for name, plot_info in band_plots:
            base = _band_base(name)
            parent = datasets_by_name.get(base + "_running_mean")
            if parent is None:
                parent = datasets_by_name.get(base)
            if parent is None:
                continue
            if plot_info.get('error_item') is None:
                continue
            std_values, fill = _band_values(plot_info['error_item'])
            if std_values is None:
                continue
            try:
                band_x, _ = plot_info['item'].getData()
            except Exception:
                band_x = None
            parent['stds'].append({
                'values': np.asarray(std_values),
                'type': plot_info.get('std_type') or 'running',
                'color': plot_model.qcolor_to_hex(fill) if fill is not None else '',
                'x': np.asarray(band_x) if band_x is not None else None,
            })

        header_row_kind = []
        header_row_label = []
        header_row_identity = []
        header_row_source = []
        header_row_color = []
        data_columns = []
        max_rows = 0
        x_inverted = self._x_inverted()

        # Kinds are per-source: y_N, y_N_std, y_N_mean, y_N_mean_std, y_N_fit
        # (multiples: _2, _3 ...). x columns number in emission order.

        def _base_name(ds):
            # Row-3 identity without the technical _running_mean suffix:
            # the processing lives in the source row instead.
            name = ds['name']
            if ds['info'].get('mean_info') is not None and name.endswith('_running_mean'):
                name = name[:-len('_running_mean')]
            return name

        def _is_avg_name(name):
            if name.startswith('Fit:'):
                return False
            parts = name.split(' | ', 3)
            return len(parts) == 4 and parts[1] in _AVG_SYSTEMS

        def _y_source(ds):
            mi = ds['info'].get('mean_info')
            if mi is None:
                return 'orig'
            if _is_avg_name(ds['name']):
                order = 'smooth before averaging' if mi.get('smooth_before') else 'average then smooth'
                return f"running mean, {order}: {mi.get('setting')}, w={mi.get('window')}"
            return f"running mean: {mi.get('setting')}, w={mi.get('window')}"

        def _std_source(ds, std):
            src = f"std: {std['type']}"
            mi = ds['info'].get('mean_info')
            if mi is not None and std['type'] != 'raw-inter' and mi.get('window'):
                src += f", w={mi['window']}"
            return src

        def _fit_identity_type(name):
            # Panel names fits 'Fit: <source> (<type>) [ID:<n>]': the identity
            # row keeps only the source; the type goes to the source row.
            base = re.sub(r" \[ID:[^\]]*\]$", "", name)
            m = re.match(r"^Fit: (.*) \(([^()]*)\)$", base)
            if m is None:
                return base, ""
            src = m.group(1)
            if src.endswith("_running_mean"):
                src = src[: -len("_running_mean")]
            return f"Fit: {_export_label(src, 'average')}", m.group(2)

        def _fmt_num(value):
            try:
                f = float(value)
            except Exception:
                return str(value)
            try:
                if np.isfinite(f) and f.is_integer() and abs(f) < 1e15:
                    return str(int(f))
            except Exception:
                pass
            return repr(f)

        def _substitute_params(func, params):
            # Full formula: fitted values in place of names. Longest names
            # first, identifier boundaries only; 'x' is the variable, never
            # a parameter. A successful fit cannot use a name both as a
            # function and a parameter, so shadowing names are safe to fill.
            try:
                items = sorted(params.items(), key=lambda kv: -len(kv[0]))
            except Exception:
                return func
            out = func
            for key, val in items:
                if not key or key == 'x':
                    continue
                try:
                    out = re.sub(r"(?<![\w.])" + re.escape(key) + r"(?![\w])",
                                 _fmt_num(val), out)
                except Exception:
                    pass
            return out

        def _fit_source(ds, fallback_type, at_data_x=False):
            fi = ds['info'].get('fit_info') or {}
            ftype = fi.get('type') or fallback_type
            parts = [f"fit: {ftype}" if ftype else "fit"]
            if fi.get('min') is not None and fi.get('max') is not None:
                parts.append(f"range=[{_fmt_num(fi['min'])}, {_fmt_num(fi['max'])}]")
            func = fi.get('function', '')
            if func:
                params = fi.get('params') or {}
                if params:
                    func = _substitute_params(func, params)
                parts.append(f"func: {func}")
            if at_data_x:
                parts.append("at data x")
            return ", ".join(parts)

        def _eval_fit_at_x(func, params, xs):
            # Same sandbox the fit itself ran in; per-point so one bad
            # point blanks only itself.
            try:
                code = compile(func.replace('^', '**'), '<fit-export>', 'eval')
            except Exception:
                return None
            safe = {"__builtins__": None, "np": np,
                    "sqrt": np.sqrt, "sin": np.sin, "cos": np.cos, "tan": np.tan,
                    "exp": np.exp, "log": np.log, "log10": np.log10, "abs": np.abs,
                    "e": np.e, "pi": np.pi, "power": np.power}
            out = np.full(len(xs), np.nan)
            loc = dict(params)
            for i, xv in enumerate(xs):
                try:
                    loc['x'] = xv
                    out[i] = eval(code, safe, loc)
                except Exception:
                    pass
            return out

        def _fit_values_at_anchor(fds, group):
            # Fit evaluated at the source grid; points outside the fit
            # range (and non-finite results) become blank cells.
            fi = fds['info'].get('fit_info') or {}
            func = fi.get('function', '')
            if not func:
                return None
            try:
                xs = np.asarray(group['x'], dtype=float)
            except Exception:
                return None
            mask = np.ones(len(xs), dtype=bool)
            if fi.get('min') is not None and fi.get('max') is not None:
                try:
                    mask = (xs >= float(fi['min'])) & (xs <= float(fi['max']))
                except Exception:
                    pass
            vals = _eval_fit_at_x(func, fi.get('params') or {}, xs)
            if vals is None:
                return None
            return [float(v) if m and np.isfinite(v) else None
                    for v, m in zip(vals, mask)]

        def _match_key(name):
            # Fit legend sources and dataset names reduced to the same form:
            # no _running_mean suffix, average token normalized.
            base = name
            if base.endswith("_running_mean"):
                base = base[: -len("_running_mean")]
            return _export_label(base, 'average')

        _FIT_SRC_RE = re.compile(r"^Fit: (.*) \([^()]*\)(?: \[ID:[^\]]*\])?$")

        def _fit_source_key(name):
            m = _FIT_SRC_RE.match(name)
            return _match_key(m.group(1)) if m else None

        # --- Column plan: one numbered source after another ------------------
        # A source is the orig dataset (or a lone mean); its mean line, its
        # bands and its fits follow it, the fit last. Kinds: y_N, y_N_std,
        # y_N_mean, y_N_mean_std, y_N_fit (multiples: _2, _3 ...).
        anchors = []          # [{key, base, group_idx, orig, means[]}]
        anchor_by_key = {}
        for gi, group in enumerate(x_groups):
            for ds in group['datasets']:
                if ds['name'].startswith('Fit:'):
                    continue
                key = (_match_key(ds['name']), gi)
                anchor = anchor_by_key.get(key)
                if anchor is None:
                    anchor = {'key': key, 'base': _match_key(ds['name']),
                              'group_idx': gi, 'orig': None, 'means': []}
                    anchor_by_key[key] = anchor
                    anchors.append(anchor)
                if ds['info'].get('mean_info') is not None:
                    anchor['means'].append(ds)
                elif anchor['orig'] is None:
                    anchor['orig'] = ds
                else:  # duplicate orig on one grid: own source number
                    dup = {'key': (key[0], gi, len(anchors)), 'base': key[0],
                           'group_idx': gi, 'orig': ds, 'means': []}
                    anchors.append(dup)

        def _fit_seq_id(name):
            m = re.search(r"\[ID:(\d+)\]$", name)
            return int(m.group(1)) if m else 10 ** 9

        fits_by_anchor = {id(a): [] for a in anchors}
        orphan_fits = []  # [(group_idx, ds)] source row gone (e.g. deleted)
        fit_order = 0
        for gi, group in enumerate(x_groups):
            for ds in group['datasets']:
                if not ds['name'].startswith('Fit:'):
                    continue
                src_key = _fit_source_key(ds['name'])
                anchor = anchor_by_key.get((src_key, gi))
                if anchor is None:
                    for cand in anchors:
                        if cand['base'] == src_key:
                            anchor = cand
                            break
                if anchor is None:
                    orphan_fits.append((gi, ds))
                else:
                    fits_by_anchor[id(anchor)].append((gi, ds, fit_order))
                    fit_order += 1
        for flist in fits_by_anchor.values():
            # _fit / _fit_2 ... follow creation order, not grid order.
            flist.sort(key=lambda t: (_fit_seq_id(t[1]['name']), t[2]))
            for i, (gi, ds, _) in enumerate(flist):
                flist[i] = (gi, ds)

        for n, anchor in enumerate(anchors, start=1):
            anchor['num'] = n
        for k, (_, ds) in enumerate(orphan_fits, start=len(anchors) + 1):
            ds['_orphan_num'] = k

        def _emit_std(stds, owner, num, mean_tag, y_axis_label, order, group, xidx):
            counts = {}
            x_state['sets'][xidx].add(num)
            for std in stds:
                std_x = std.get('x')
                pos = self._subset_positions(group['x'], std_x) if std_x is not None else None
                if pos is None:
                    continue
                aligned = self._align_values(len(group['x']), pos, std['values'])
                tag = f"y_{num}{mean_tag}_std"
                counts[tag] = counts.get(tag, 0) + 1
                if counts[tag] > 1:
                    tag = f"{tag}_{counts[tag]}"
                header_row_kind.append(tag)
                header_row_label.append(y_axis_label)
                header_row_identity.append(_export_label(_base_name(owner), 'std'))
                header_row_source.append(_std_source(owner, std))
                header_row_color.append(std.get('color', ''))
                data_columns.append([aligned[i] for i in order])

        def _order_for(length):
            order = list(range(length))
            if x_inverted:
                order.reverse()
            return order

        def _emit_y(ds, kind, order, xidx, y_values=None, at_data_x=False):
            y_col_name = ds['info'].get('y_col', '')
            y_axis_label = self.y_axis_labels.get(y_col_name, y_col_name)
            header_row_kind.append(kind)
            m = re.match(r"y_(\d+)", kind)
            if m is not None:
                x_state['sets'][xidx].add(int(m.group(1)))
            header_row_label.append(y_axis_label)
            if ds['name'].startswith('Fit:'):
                ident, ftype = _fit_identity_type(ds['name'])
                header_row_identity.append(ident)
                header_row_source.append(_fit_source(ds, ftype, at_data_x))
            else:
                header_row_identity.append(_export_label(_base_name(ds), 'average'))
                header_row_source.append(_y_source(ds))
            header_row_color.append(ds.get('color', ''))
            values = y_values if y_values is not None else ds['y']
            data_columns.append([values[i] if i < len(values) else None for i in order])
            return y_axis_label

        def _emit_x(group, is_copy=False):
            order = _order_for(len(group['x']))
            max_rows[0] = max(max_rows[0], len(order))
            header_row_kind.append('x')
            header_row_label.append(group.get('x_label', self.x_axis_label))
            header_row_identity.append('')
            header_row_source.append('')
            header_row_color.append('')
            data_columns.append([group['x'][i] for i in order])
            x_state['sets'].append(set())
            x_state['copies'].append(is_copy)
            return len(x_state['sets']) - 1

        max_rows = [max_rows]
        x_state = {'sets': [], 'copies': []}
        emitted_x = set()
        copied_x = {}
        group_x_idx = {}
        for anchor in anchors:
            group = x_groups[anchor['group_idx']]
            order = _order_for(len(group['x']))
            max_rows[0] = max(max_rows[0], len(order))
            if anchor['group_idx'] not in emitted_x:
                emitted_x.add(anchor['group_idx'])
                group_x_idx[anchor['group_idx']] = _emit_x(group)
            gx = group_x_idx[anchor['group_idx']]
            num = anchor['num']
            line = anchor['orig'] if anchor['orig'] is not None else anchor['means'][0]
            mean_tag = '' if anchor['orig'] is not None else '_mean'
            y_axis_label = _emit_y(line, f"y_{num}{mean_tag}", order, gx)
            _emit_std(line['stds'], line, num, mean_tag, y_axis_label, order, group, gx)
            if anchor['orig'] is not None:
                for mean_ds in anchor['means']:
                    y_axis_label = _emit_y(mean_ds, f"y_{num}_mean", order, gx)
                    _emit_std(mean_ds['stds'], mean_ds, num, '_mean', y_axis_label, order, group, gx)
            # Fits last: same-grid fits need no x, moved fits share one x copy
            # per grid, emitted at first use and named for all its users.
            fit_seq = 0
            for gi, fds in fits_by_anchor[id(anchor)]:
                fit_seq += 1
                tag = f"y_{num}_fit" + (f"_{fit_seq}" if fit_seq > 1 else "")
                if fits_at_data_x:
                    # Evaluated at the source grid: no x copy, values align
                    # with the anchor columns. Falls back to own grid below.
                    grid_vals = _fit_values_at_anchor(fds, group)
                    if grid_vals is not None:
                        _emit_y(fds, tag, order, gx, y_values=grid_vals, at_data_x=True)
                        continue
                if gi == anchor['group_idx']:
                    xidx = group_x_idx[gi]
                else:
                    if gi not in copied_x:
                        copied_x[gi] = _emit_x(x_groups[gi], is_copy=True)
                    xidx = copied_x[gi]
                _emit_y(fds, tag, _order_for(len(x_groups[gi]['x'])), xidx)

        # Orphan fits (source row gone, e.g. deleted): after all sources,
        # grouped by origin grid with their x.
        orphans_by_group = {}
        for gi, ds in orphan_fits:
            orphans_by_group.setdefault(gi, []).append(ds)
        for gi in sorted(orphans_by_group):
            group = x_groups[gi]
            if gi not in emitted_x:
                emitted_x.add(gi)
                group_x_idx[gi] = _emit_x(group)
            order = _order_for(len(group['x']))
            for ds in orphans_by_group[gi]:
                _emit_y(ds, f"y_{ds['_orphan_num']}_fit", order, group_x_idx[gi])
        max_rows = max_rows[0]

        # Every x is named for the sources it serves (x_1,3,4); fit-grid
        # copies take a _fit suffix so they cannot collide with a data x
        # serving the same sources.
        xi = 0
        for idx, k in enumerate(header_row_kind):
            if k == 'x':
                served = sorted(x_state['sets'][xi])
                name = f"x_{','.join(str(n) for n in served)}" if served else 'x'
                if x_state['copies'][xi] and name != 'x':
                    name += '_fit'
                header_row_kind[idx] = name
                xi += 1

        try:
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, delimiter=delimiter)
                # Order: kind, identity, source, axis label, color.
                writer.writerow(header_row_kind)
                writer.writerow(header_row_identity)
                writer.writerow(header_row_source)
                writer.writerow(header_row_label)
                writer.writerow(header_row_color)

                for i in range(max_rows):
                    row_data = []
                    for col in data_columns:
                        row_data.append(self._format_export_value(col[i]) if i < len(col) else '')
                    writer.writerow(row_data)
            logger.info("Data exported to %s", filename)
        except Exception as e:
            logger.warning("Failed to save data: %s", e)

    def get_current_plot_state(self) -> Dict[str, Any]:
        """Extracts current state for PopOutWindow."""
        state = {
            'title': "", 
            'x_label': self.x_axis_label,
            'x_limits': self.plot_item.getViewBox().viewRange()[0],
            'x_inverted': self._x_inverted(),
            'y_axes': {}
        }

        for name, plot_info in self.plots.items():
            item = plot_info.get('item')
            if not item or not item.isVisible(): continue

            y_col = plot_info.get('y_col')
            if not y_col: continue

            if y_col not in state['y_axes']:
                vb = self.y_axes[y_col]['viewbox']
                state['y_axes'][y_col] = {
                    'label': self.y_axis_labels.get(y_col, y_col),
                    'color': self.y_axis_colors.get(y_col, QColor("black")),
                    'y_limits': vb.viewRange()[1],
                    'y_inverted': self._y_inverted(y_col),
                    'series': []
                }

            x, y = item.getData()
            if x is None or y is None: continue
            
            std_data = None
            error_item = plot_info.get('error_item')
            if error_item:
                c1 = error_item.curves[0].getData()
                c2 = error_item.curves[1].getData()
                if c1[1] is not None and c2[1] is not None:
                    std_data = np.abs(c2[1] - c1[1]) / 2.0

            pen = item.opts['pen']

            # Same dict shape as before, values sourced from the single builder.
            series_entry = plot_model.make_series(
                f"{y_col}_{name}", name,
                np.array(x), np.array(y),
                np.array(std_data) if std_data is not None else None,
                color=pen.color(),
                linestyle_matlab=plot_model.qt_pen_style_to_mpl(pen.style()),
                linestyle_qt=pen.style(),
                width=pen.width(),
                layer_priority=plot_info.get('layer_priority', 0),
                row=plot_info.get('row', plot_model.DEFAULT_ROW),
                uid=plot_info.get('plot_id'),
            )
            state['y_axes'][y_col]['series'].append(series_entry)
        
        for y_col in state['y_axes']:
            state['y_axes'][y_col]['series'].sort(key=lambda s: s['layer_priority'])
            
        return state

    def get_global_y_range(self):
        """Calculates the global min and max Y values across all visible plots."""
        global_min = float('inf')
        global_max = float('-inf')
        found_data = False

        for plot_info in self.plots.values():
            item = plot_info.get('item')
            if not item or not item.isVisible():
                continue
            
            _, y = item.getData()
            if y is not None and len(y) > 0:
                try:
                    y_min = np.nanmin(y)
                    y_max = np.nanmax(y)
                    if np.isfinite(y_min) and np.isfinite(y_max):
                        found_data = True
                        global_min = min(global_min, float(y_min))
                        global_max = max(global_max, float(y_max))
                except Exception:
                    pass
                
                # Check error band if present
                error_item = plot_info.get('error_item')
                if error_item:
                    c1 = error_item.curves[0].getData()
                    c2 = error_item.curves[1].getData()
                    if c1[1] is not None and len(c1[1]) > 0:
                        try:
                            c1_min = np.nanmin(c1[1])
                            if np.isfinite(c1_min):
                                found_data = True
                                global_min = min(global_min, float(c1_min))
                        except Exception:
                            pass
                    if c2[1] is not None and len(c2[1]) > 0:
                        try:
                            c2_max = np.nanmax(c2[1])
                            if np.isfinite(c2_max):
                                found_data = True
                                global_max = max(global_max, float(c2_max))
                        except Exception:
                            pass

        if not found_data or not np.isfinite(global_min) or not np.isfinite(global_max):
            return None
        return global_min, global_max

    def reset_view(self):
        """
        Resets the view based on current lock states.
        Calculates ranges manually to avoid pyqtgraph auto-range issues.
        """
        # Always reset X to auto first
        self.plot_item.enableAutoRange(axis='x')

        # 1. Gather all active ViewBoxes and their specific data bounds
        axes_data = []
        
        # Main ViewBox
        main_vb = self.plot_item.getViewBox()
        dmin, dmax = self._get_data_bounds_for_viewbox(main_vb)
        axes_data.append({'vb': main_vb, 'min': dmin, 'max': dmax})
        
        # Aux ViewBoxes
        for axis_info in self.y_axes.values():
            vb = axis_info['viewbox']
            if vb is not main_vb:
                dmin, dmax = self._get_data_bounds_for_viewbox(vb)
                axes_data.append({'vb': vb, 'min': dmin, 'max': dmax})

        # Padding factor (e.g., 5%)
        pad = 0.05

        # --- SCENARIO 1: BOTH LOCKS (Global Union) ---
        if self.axes_locked and self.scale_locked:
            # Find global min/max
            gmin, gmax = float('inf'), float('-inf')
            has_data = False
            for d in axes_data:
                if d['min'] is not None:
                    gmin = min(gmin, d['min'])
                    gmax = max(gmax, d['max'])
                    has_data = True
            
            if not has_data:
                gmin, gmax = 0.0, 1.0
            
            # Apply global range to ALL viewboxes
            for d in axes_data:
                d['vb'].setYRange(gmin, gmax, padding=pad)

        # --- SCENARIO 2: AXES LOCK ONLY (Smart Zero Alignment) ---
        elif self.axes_locked:
            # Step A: Calculate Ideal Ratio (R) for each axis
            ideal_ratios = []
            
            for d in axes_data:
                dmin, dmax = d['min'], d['max']
                if dmin is None: continue

                span = dmax - dmin
                if span == 0: span = 1.0 if dmax == 0 else abs(dmax)

                if dmin >= 0:
                    r_i = 0.0 # All positive -> 0 at bottom
                elif dmax <= 0:
                    r_i = 1.0 # All negative -> 0 at top
                else:
                    # Crossing zero -> R is fraction of space for negative
                    r_i = abs(dmin) / (abs(dmin) + dmax)
                
                ideal_ratios.append(r_i)
            
            target_R = float(np.median(ideal_ratios)) if ideal_ratios else 0.5

            # Step B: Scale each axis individually to fit data into target_R
            for d in axes_data:
                vb, dmin, dmax = d['vb'], d['min'], d['max']
                if dmin is None:
                    vb.setYRange(-target_R, 1-target_R, padding=pad)
                    continue

                # Calculate Height (H) required
                req_h_min = 0.0
                req_h_max = 0.0
                
                if target_R > 1e-6 and dmin < 0:
                    req_h_min = abs(dmin) / target_R
                
                if (1 - target_R) > 1e-6 and dmax > 0:
                    req_h_max = dmax / (1 - target_R)
                
                H = max(req_h_min, req_h_max)
                if H == 0: H = 1.0
                
                # Apply bounds
                new_min = -H * target_R
                new_max = H * (1 - target_R)
                vb.setYRange(new_min, new_max, padding=pad)

        # --- SCENARIO 3: SCALE LOCK ONLY (Uniform Zoom) ---
        elif self.scale_locked:
            # Find the Maximum Data Span across all axes
            max_span = 0.0
            for d in axes_data:
                if d['min'] is not None:
                    span = d['max'] - d['min']
                    if span > max_span: max_span = span
            
            if max_span == 0: max_span = 1.0
            
            # Apply Max Span to each axis, centered on its own data
            # This ensures every axis has the same 'zoom level' (units per pixel)
            for d in axes_data:
                vb, dmin, dmax = d['vb'], d['min'], d['max']
                if dmin is None:
                    center = 0.5
                else:
                    center = (dmin + dmax) / 2.0
                
                half = max_span / 2.0
                vb.setYRange(center - half, center + half, padding=pad)

        # --- SCENARIO 4: NO LOCKS (Pure Independent) ---
        else:
            # Replicate the "Auto Scale" button behavior exactly.
            # This enables continuous auto-scaling and hides the 'A' symbol.
            for d in axes_data:
                d['vb'].enableAutoRange(axis='y')

        # keep aux geometry in sync, O(1) per axis, no extra layout pass
        try:
            self.update_views()
        except Exception:
            pass

    def get_view_ranges(self) -> Dict[str, Any]:
        """Returns a dictionary of view ranges for all active axes."""
        ranges = {}
        # Main ViewBox (associated with left axis)
        ranges['main'] = self.plot_item.getViewBox().viewRange()
        
        # Auxiliary Axes
        for y_col, axis_info in self.y_axes.items():
            if axis_info['viewbox'] is not self.plot_item.getViewBox():
                ranges[y_col] = axis_info['viewbox'].viewRange()
        return ranges

    def set_view_ranges(self, ranges: Dict[str, Any]):
        """Restores view ranges from a dictionary."""
        if not ranges: return

        was_syncing = self._is_syncing_axes
        self._is_syncing_axes = True
        try:
            # Restore Main
            if 'main' in ranges:
                xr, yr = ranges['main']
                self.plot_item.getViewBox().setRange(xRange=xr, yRange=yr, padding=0)

            # Restore Aux
            for y_col, axis_info in self.y_axes.items():
                if y_col in ranges:
                    xr, yr = ranges[y_col]
                    axis_info['viewbox'].setYRange(yr[0], yr[1], padding=0)
                    # X is linked to main, so no need to set X
        finally:
            self._is_syncing_axes = was_syncing

    def _get_data_bounds_for_viewbox(self, viewbox: pg.ViewBox):
        """Helper to get min/max Y data from all visible plots associated with a given ViewBox."""
        vmin = float('inf')
        vmax = float('-inf')
        has_data = False

        for plot_info in self.plots.values():
            if plot_info['view_box'] is viewbox:
                item = plot_info.get('item')
                if not item or not item.isVisible():
                    continue
                
                _, y = item.getData()
                if y is not None and len(y) > 0:
                    # Use nan-aware min/max to handle outer-joined NaNs from multi-header files
                    try:
                        y_min = np.nanmin(y)
                        y_max = np.nanmax(y)
                    except (ValueError, RuntimeWarning):
                        continue
                    if not np.isfinite(y_min) or not np.isfinite(y_max):
                        continue
                    has_data = True
                    vmin = min(vmin, float(y_min))
                    vmax = max(vmax, float(y_max))
                    
                    error_item = plot_info.get('error_item')
                    if error_item:
                        # FillBetweenItem stores curves. Check their data.
                        c1 = error_item.curves[0].getData()
                        c2 = error_item.curves[1].getData()
                        if c1[1] is not None and len(c1[1]) > 0:
                            try:
                                c1_min = np.nanmin(c1[1])
                                if np.isfinite(c1_min):
                                    has_data = True
                                    vmin = min(vmin, float(c1_min))
                            except Exception:
                                pass
                        if c2[1] is not None and len(c2[1]) > 0:
                            try:
                                c2_max = np.nanmax(c2[1])
                                if np.isfinite(c2_max):
                                    has_data = True
                                    vmax = max(vmax, float(c2_max))
                            except Exception:
                                pass
        
        if not has_data or not np.isfinite(vmin) or not np.isfinite(vmax):
            return None, None
        return vmin, vmax

    def _on_auto_btn_clicked(self, *args):
        """Replaces PlotItem.autoBtnClicked so every y axis is rescaled.

        PlotItem's default only touches the main ViewBox (X + first Y). With
        several y axes the extra ViewBoxes stay where they were, so only X
        appears to auto-scale. Delegating to reset_view re-uses the same
        lock-aware rescaling that the red/blue lock buttons already use
        (global union / zero alignment / shared span / independent), and
        afterwards hides the 'A' button like the original did.
        """
        try:
            self.reset_view()
        except Exception:
            # Fallback: at least restore X auto-range like the original
            try:
                self.plot_item.enableAutoRange()
            except Exception:
                pass
        try:
            # Original autoBtnClicked hides the button after a successful auto-range
            if hasattr(self.plot_item, 'autoBtn') and self.plot_item.autoBtn is not None:
                self.plot_item.autoBtn.hide()
        except Exception:
            pass

    def _get_mpl_linestyle(self, qt_style):
        """Maps Qt PenStyle enums/ints to Matplotlib linestyle strings."""
        return plot_model.qt_pen_style_to_mpl(qt_style)
