import numpy as np
import time
from PyQt6.QtCore import QObject, pyqtSignal, QRunnable, pyqtSlot

try:
    from scipy.optimize import minimize
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

class FitWorkerSignals(QObject):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

class FitWorker(QRunnable):
    def __init__(self, x_data, y_data, function_str, params_config, bounds, method, error_metric, time_limit):
        super().__init__()
        self.setAutoDelete(False)
        
        self.x_data = x_data
        self.y_data = y_data
        self.function_str = function_str
        self.params_config = params_config
        self.bounds = bounds
        self.method = method
        self.error_metric = error_metric
        self.time_limit = time_limit
        self.signals = FitWorkerSignals()
        self._is_cancelled = False

    @pyqtSlot()
    def run(self):
        if not SCIPY_AVAILABLE:
            self.signals.error.emit("Scipy not installed.")
            return

        try:
            # --- 1. Robust Data Preparation ---
            try:
                x_arr = np.array(self.x_data, dtype=float)
                y_arr = np.array(self.y_data, dtype=float)
                # Ensure bounds are floats
                x_min = float(self.bounds[0])
                x_max = float(self.bounds[1])
            except ValueError:
                self.signals.error.emit("Data contains non-numeric values.")
                return
            
            # Remove NaNs/Infs
            valid_mask = np.isfinite(x_arr) & np.isfinite(y_arr)
            x_clean = x_arr[valid_mask]
            y_clean = y_arr[valid_mask]

            # Apply Min/Max Bounds (The Filter)
            range_mask = (x_clean >= x_min) & (x_clean <= x_max)
            x_fit = x_clean[range_mask]
            y_fit = y_clean[range_mask]

            if len(x_fit) < 2:
                # Fallback: If bounds are too tight, emit error or warning
                self.signals.error.emit(f"No data in range [{x_min:.2f}, {x_max:.2f}]")
                return

            # --- 2. Model Function Construction ---
            processed_func_str = self.function_str.replace('^', '**')

            safe_globals = {
                "__builtins__": None, "np": np,
                "sqrt": np.sqrt, "sin": np.sin, "cos": np.cos, "tan": np.tan,
                "exp": np.exp, "log": np.log, "log10": np.log10, "abs": np.abs,
                "e": np.e, "pi": np.pi, "power": np.power
            }

            param_names = sorted(self.params_config.keys())
            free_params = []
            fixed_params = {}
            initial_guess = []
            
            for p in param_names:
                cfg = self.params_config[p]
                if cfg['locked']:
                    fixed_params[p] = float(cfg['guess'])
                else:
                    free_params.append(p)
                    initial_guess.append(float(cfg['guess']))

            def model_func(current_free_values, x_val):
                local_vars = {'x': x_val}
                
                free_iter = iter(current_free_values)
                for p in param_names:
                    if p in fixed_params:
                        local_vars[p] = fixed_params[p]
                    else:
                        local_vars[p] = next(free_iter)
                
                try:
                    res = eval(processed_func_str, safe_globals, local_vars)
                    if np.isscalar(res):
                        return np.full_like(x_val, res) if hasattr(x_val, '__len__') else res
                    return res
                except Exception:
                    return np.full_like(x_val, 1e30) if hasattr(x_val, '__len__') else 1e30

            # --- 3. Objective Function ---
            start_time = time.time()

            def objective(free_values):
                if self._is_cancelled: raise StopIteration
                if time.time() - start_time > self.time_limit: raise TimeoutError

                y_pred = model_func(free_values, x_fit)
                
                if np.any(np.abs(y_pred) >= 1e29): return 1e30

                residuals = y_fit - y_pred
                
                if self.error_metric == "Mean Absolute Error":
                    return np.mean(np.abs(residuals))
                elif self.error_metric == "Mean Bias":
                    return np.abs(np.mean(residuals))
                elif self.error_metric == "Mean Cubed Error":
                    return np.mean(np.abs(residuals)**3)
                else: # Mean Squared Error
                    return np.mean(residuals**2)

            # --- 4. Optimization ---
            final_params_map = {}
            final_error = float('inf')
            
            if free_params:
                try:
                    res = minimize(
                        objective, 
                        initial_guess, 
                        method=self.method, 
                        tol=1e-5,
                        options={'maxiter': 2000}
                    )
                    best_free = res.x
                    final_error = res.fun
                except (StopIteration, TimeoutError):
                    best_free = initial_guess
                except Exception as e:
                    self.signals.error.emit(f"Optimizer failed: {str(e)}")
                    return

                free_iter = iter(best_free)
                for p in param_names:
                    if p in fixed_params:
                        final_params_map[p] = fixed_params[p]
                    else:
                        final_params_map[p] = next(free_iter)
            else:
                final_error = objective([])
                final_params_map = fixed_params

            # --- 5. Extrapolation ---
            # Ensure we plot slightly wider than bounds if needed, or strictly bounds
            plot_min = x_min
            plot_max = x_max
            if plot_max <= plot_min: plot_max = plot_min + 1.0
            
            n_points = 1000
            x_plot = np.linspace(plot_min, plot_max, n_points)
            
            final_free_values = [final_params_map[p] for p in free_params]
            y_plot = model_func(final_free_values, x_plot)
            
            # Filter huge numbers for plotting
            valid_plot_mask = (np.abs(y_plot) < 1e20)
            y_plot = np.where(valid_plot_mask, y_plot, np.nan)
            
            status = 'Success'
            if final_error > 1e15: status = 'Fit Poor / Failed'

            result_payload = {
                'best_params': final_params_map,
                'final_error': final_error,
                'x_fit': x_plot,
                'y_fit': y_plot,
                'status': status
            }
            
            self.signals.finished.emit(result_payload)

        except Exception as e:
            self.signals.error.emit(str(e))

    def cancel(self):
        self._is_cancelled = True