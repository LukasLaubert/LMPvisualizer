# lmp_visualizer/data_manager.py

import re
import numpy as np
import pandas as pd
from pathlib import Path
import ast
from typing import Dict, Optional, Tuple, List


def split_indexed_token(token: str, known_names) -> Tuple[str, Optional[str]]:
    """Split tokens like 'Lx(end-1)' into ('Lx', 'end-1') when Lx is known."""
    known_set = set(known_names or [])
    if token in known_set:
        return token, None

    for name in sorted(known_set, key=len, reverse=True):
        prefix = f"{name}("
        if token.startswith(prefix) and token.endswith(")"):
            index_expr = token[len(prefix):-1].strip()
            return name, index_expr

    return token, None


def evaluate_index_expression(expr: str, end_value: int) -> int:
    """Evaluate a restricted 1-based index expression with 'end' support."""
    if end_value < 1:
        raise ValueError("No values are available for indexing.")

    allowed_binops = {
        ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b,
        ast.FloorDiv: lambda a, b: a // b,
        ast.Mod: lambda a, b: a % b,
        ast.Pow: lambda a, b: a ** b,
    }
    allowed_unary = {
        ast.UAdd: lambda a: a,
        ast.USub: lambda a: -a,
    }

    def eval_node(node):
        if isinstance(node, ast.Expression):
            return eval_node(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name) and node.id == "end":
            return end_value
        if isinstance(node, ast.BinOp) and type(node.op) in allowed_binops:
            return allowed_binops[type(node.op)](eval_node(node.left), eval_node(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in allowed_unary:
            return allowed_unary[type(node.op)](eval_node(node.operand))
        raise ValueError("Index expressions may only use numbers, 'end', and arithmetic operators.")

    try:
        value = eval_node(ast.parse(expr, mode="eval"))
    except Exception as exc:
        raise ValueError(f"Invalid index expression '{expr}': {exc}")

    if not np.isfinite(value) or abs(value - round(value)) > 1e-9:
        raise ValueError(f"Index expression '{expr}' must evaluate to an integer.")

    index = int(round(value))
    if index < 1 or index > end_value:
        raise IndexError(f"Index {index} is out of range. Available range is 1..{end_value}.")
    return index


def formula_min(*values):
    if not values:
        raise ValueError("min() requires at least one value.")
    result = values[0]
    for value in values[1:]:
        result = np.minimum(result, value)
    return result


def formula_max(*values):
    if not values:
        raise ValueError("max() requires at least one value.")
    result = values[0]
    for value in values[1:]:
        result = np.maximum(result, value)
    return result


class LogDataManager:
    """Manages all simulation data using pandas. Supports Lazy Loading."""
    def __init__(self):
        # Storage: {study: {system: pd.DataFrame OR List[Path]}}
        self.data: Dict[str, Dict[str, any]] = {} 
        self.warnings = []
        self.available_columns = []
        self.custom_properties = {} # Name -> Formula

    def set_custom_properties(self, props: Dict[str, str]):
        self.custom_properties = props

    def _ensure_system_loaded(self, study, system):
        """Lazy loader: Parses log files into DataFrame only when accessed."""
        if study not in self.data or system not in self.data[study]:
            return None
            
        entry = self.data[study][system]
        
        # If already a DataFrame, return it
        if isinstance(entry, pd.DataFrame):
            return entry
            
        # If it's a list (of paths), parse it now
        if isinstance(entry, list):
            from log_parser import LogParser
            df = LogParser.parse_multiple_logs(entry)
            if df is not None and not df.empty:
                self.data[study][system] = df # Replace list with DF (Memoization)
                return df
            else:
                # Parsing failed? Keep as list or remove?
                # Keep as list prevents repeated failed parse attempts if handled carefully,
                # but returning None indicates failure.
                return None
        return None

    def _evaluate_custom_property(self, df: pd.DataFrame, property_name: str) -> Optional[pd.Series]:
        if property_name not in self.custom_properties:
            return None
        
        formula = self.custom_properties[property_name]
        
        # Identify columns used in the formula: {ColumnName} or {ColumnName(index)}
        pattern = r"\{([^}]+)\}"
        tokens = re.findall(pattern, formula)
        known_names = set(df.columns) | set(self.custom_properties)
        if df.index.name:
            known_names.add(df.index.name)
        
        local_env = {}
        
        # Recursively resolve tokens
        for token in set(tokens):
            base_token, index_expr = split_indexed_token(token, known_names)
            # If the token refers to the property itself (e.g. 'Step' -> '{Step}/10'),
            # force fetching the raw column to avoid infinite recursion.
            ignore_custom = (base_token == property_name)
            series = self._get_series(df, base_token, ignore_custom=ignore_custom)
            
            if series is None:
                return None
            if index_expr is not None:
                try:
                    if not hasattr(series, "iloc"):
                        return None
                    index = evaluate_index_expression(index_expr, len(series))
                    series = series.iloc[index - 1]
                except Exception as e:
                    print(f"Error indexing token '{{{token}}}': {e}")
                    return None
            local_env[token] = series

        # Replace tokens in formula with valid variable names
        clean_formula = formula
        token_map = {}
        for i, token in enumerate(local_env.keys()):
            var_name = f"__var_{i}__"
            # Escape token for regex since it might contain special chars
            clean_formula = re.sub(r"\{" + re.escape(token) + r"\}", var_name, clean_formula)
            token_map[var_name] = local_env[token]

        # Handle 'cot' -> '1/tan' (simple text replacement)
        clean_formula = clean_formula.replace("cot(", "1/np.tan(")
        
        # Handle '^' -> '**' for user convenience
        clean_formula = clean_formula.replace("^", "**")
        
        # Safe environment for eval
        safe_globals = {
            "__builtins__": None,
            "np": np,
            "sqrt": np.sqrt,
            "sin": np.sin,
            "cos": np.cos,
            "tan": np.tan,
            "min": formula_min,
            "max": formula_max,
            "e": np.e,
            "pi": np.pi,
        }
        
        try:
            # Use eval with numpy support (via safe_globals and pandas series in token_map)
            result = eval(clean_formula, safe_globals, token_map)
            if np.isscalar(result) or getattr(result, "ndim", None) == 0:
                return pd.Series([float(result)] * len(df), index=df.index)
            return result
        except Exception as e:
            print(f"Error evaluating formula '{formula}': {e}")
            return None

    def _get_series(self, df: pd.DataFrame, col_name: str, ignore_custom: bool = False) -> Optional[pd.Series]:
        known_names = set(df.columns) | set(self.custom_properties)
        if df.index.name:
            known_names.add(df.index.name)
        base_name, index_expr = split_indexed_token(col_name, known_names)
        if index_expr is not None:
            series = self._get_series(df, base_name, ignore_custom=ignore_custom)
            if series is None or not hasattr(series, "iloc"):
                return None
            try:
                index = evaluate_index_expression(index_expr, len(series))
                return series.iloc[index - 1]
            except Exception as e:
                print(f"Error indexing token '{{{col_name}}}': {e}")
                return None

        if not ignore_custom and col_name in self.custom_properties:
             # Try to evaluate custom property first
             return self._evaluate_custom_property(df, col_name)

        if col_name in df.columns:
            return df[col_name]
        
        # Check if it is the index
        if df.index.name == col_name:
             return df.index.to_series()
        
        # If we are here, it's not in columns.
        # If ignore_custom is True, we already skipped custom check.
        # If ignore_custom is False, we already checked custom and it wasn't there.
        return None

    def _has_column(self, df: pd.DataFrame, col_name: str) -> bool:
        if col_name in df.columns:
            return True
        if col_name in self.custom_properties:
            # Check if it can be evaluated (shallow check)
            # Deep check might be expensive, but necessary for validity.
            # For now, assume if defined it exists, but _get_series returns None if failed.
            return True
        return False

    def load_project_data(self, studies: Dict[str, List[str]], root_path, log_keywords: List[str] = None, file_map: Dict[str, Path] = None):
        """
        Discovers log files and stores them for lazy loading.
        Peeks at headers to populate available_columns immediately.
        """
        from log_parser import LogParser # Local import
        self.data.clear()
        self.warnings = []
        self.available_columns = []
        
        all_cols_found = set()
        successful_keywords = set()
        if file_map is None:
            file_map = {}

        for study_name, system_list in studies.items():
            self.data[study_name] = {}
            for system_name in system_list:
                # Use the map provided by discover_studies_systems
                # Key is "study|system"
                key = f"{study_name}|{system_name}"
                log_files = file_map.get(key, [])

                if not log_files:
                    # Fallback for manual addition or older session files.
                    # root_path may be several project paths.
                    for base in (root_path if isinstance(root_path, (list, tuple)) else [root_path]):
                        log_path = Path(base) / study_name / system_name
                        if not log_keywords:
                            log_files.extend(log_path.glob("log.lammps"))
                        else:
                            for keyword in log_keywords:
                                log_files.extend(log_path.glob(f"*{keyword}*"))
                    log_files = sorted(list(set(log_files)))

                if not log_files:
                    continue
                
                # Update successful keywords
                for fpath in log_files:
                    for kw in (log_keywords or []):
                        if kw in fpath.name:
                            successful_keywords.add(kw)

                # STORE PATHS ONLY (Lazy Loading)
                self.data[study_name][system_name] = log_files
                
                # Peek at the FIRST file to get columns
                if log_files:
                    cols = LogParser.peek_columns(log_files[0])
                    for c in cols:
                        all_cols_found.add(c)

        # Update available columns global list
        # Prioritize standard thermo keywords if present
        sorted_cols = sorted(list(all_cols_found))
        priority = ['Step', 'Temp', 'Press', 'PotEng', 'KinEng', 'TotEng', 'Volume', 'Density']
        self.available_columns = [c for c in priority if c in sorted_cols] + [c for c in sorted_cols if c not in priority]
        
        return self.warnings, successful_keywords

    def get_study_names(self) -> List[str]:
        return sorted(list(self.data.keys()))

    def get_system_names(self, study: str) -> List[str]:
        if study in self.data:
            return sorted(list(self.data[study].keys()))
        return []

    def get_all_system_names(self) -> List[str]:
        """Returns a sorted list of all unique system names across all studies."""
        all_systems = set()
        for study_name in self.data:
            for system_name in self.data[study_name]:
                all_systems.add(system_name)
        return sorted(list(all_systems))

    def get_all_column_names(self) -> List[str]:
        """Returns a list of all available data columns from the project."""
        return self.available_columns

    def _get_column_lengths(self) -> Dict[str, int]:
        """Return minimum observed lengths for raw columns across loaded project systems."""
        lengths = {}
        for study in list(self.data.keys()):
            for system in list(self.data[study].keys()):
                df = self._ensure_system_loaded(study, system)
                if df is None:
                    continue
                for col in df.columns:
                    col_len = len(df[col])
                    lengths[col] = col_len if col not in lengths else min(lengths[col], col_len)
                if df.index.name:
                    idx_len = len(df.index)
                    lengths[df.index.name] = idx_len if df.index.name not in lengths else min(lengths[df.index.name], idx_len)
        return lengths

    def _infer_custom_property_length(self, prop_name: str, custom_properties: Dict[str, str], raw_lengths: Dict[str, int], seen=None):
        if prop_name in raw_lengths:
            return raw_lengths[prop_name]
        if prop_name not in custom_properties:
            return None
        if seen is None:
            seen = set()
        if prop_name in seen:
            return None
        seen.add(prop_name)

        known_names = set(raw_lengths.keys()) | set(custom_properties.keys())
        lengths = []
        for token in re.findall(r"\{([^}]+)\}", custom_properties[prop_name]):
            base, index_expr = split_indexed_token(token, known_names)
            if index_expr is not None:
                continue
            length = self._infer_custom_property_length(base, custom_properties, raw_lengths, seen.copy())
            if length is not None:
                lengths.append(length)
        return min(lengths) if lengths else 1

    def validate_formula_indices(self, formula: str, custom_properties: Dict[str, str] = None) -> Tuple[bool, str]:
        """Validate indexed tokens against available loaded data lengths."""
        custom_properties = custom_properties if custom_properties is not None else self.custom_properties
        initial_known_names = set(self.available_columns) | set(custom_properties.keys())
        tokens_to_check = []

        for token in re.findall(r"\{([^}]+)\}", formula):
            base, index_expr = split_indexed_token(token, initial_known_names)
            if index_expr is not None:
                tokens_to_check.append((token, base, index_expr))

        if not tokens_to_check:
            return True, ""

        raw_lengths = self._get_column_lengths()
        known_names = set(raw_lengths.keys()) | initial_known_names

        for token, _, _ in tokens_to_check:
            base, index_expr = split_indexed_token(token, known_names)
            length = self._infer_custom_property_length(base, custom_properties, raw_lengths)
            if length is None:
                continue
            try:
                evaluate_index_expression(index_expr, length)
            except Exception as exc:
                return False, f"Index for variable '{base}' in '{{{token}}}' is out of range or invalid:\n{exc}"

        return True, ""

    def check_data_consistency(self, study: str) -> Dict[str, int]:
        """
        Checks if dataframes for a study have the same length.
        Returns a dictionary of {system_name: length}.
        """
        if study not in self.data:
            return {}
        
        lengths = {}
        for sys in self.data[study].keys():
            df = self._ensure_system_loaded(study, sys)
            if df is not None:
                lengths[sys] = len(df)
        
        if len(set(lengths.values())) > 1:
            return lengths # Inconsistent
        return {} # Consistent

    def get_plot_data(
        self, study: str, system: str, x_col: str, y_col: str, 
        compute_std: bool, user_choices: Optional[dict] = None
    ) -> Optional[Dict]:
        """
        Retrieves data for plotting. Handles averaging and standard deviation.
        user_choices = {'truncate_len': int, 'exclude': [str]}
        """
        if study not in self.data:
            return None

        if system != 'average':
            if system in self.data[study]:
                # Ensure loaded
                df = self._ensure_system_loaded(study, system)
                if df is None: return None
                
                # Use _get_series to support custom properties
                x_data = self._get_series(df, x_col)
                y_data = self._get_series(df, y_col)
                
                if x_data is not None and y_data is not None:
                    return {'x': x_data, 'y': y_data, 'std': None}
            return None
        
        # --- Averaging Logic ---
        # Ensure all systems in study are loaded before averaging
        for sys_name in self.data[study].keys():
            self._ensure_system_loaded(study, sys_name)
            
        study_dfs = self.data[study]
        
        dfs_to_process = []
        if user_choices:
            truncate_len = user_choices.get('truncate_len')
            exclude_systems = user_choices.get('exclude', [])
            
            for sys_name, entry in study_dfs.items():
                if sys_name in exclude_systems:
                    continue
                # entry should be DF now
                if isinstance(entry, pd.DataFrame):
                    if truncate_len is not None:
                        dfs_to_process.append(entry.iloc[:truncate_len])
                    else:
                        dfs_to_process.append(entry)
        else:
            dfs_to_process = [df for df in study_dfs.values() if isinstance(df, pd.DataFrame)]

        if not dfs_to_process:
            return None

        # Determine alignment column ('Step' or 'index')
        align_col = 'Step'
        if not all(align_col in df.columns for df in dfs_to_process):
            align_col = 'index'

        processed_dfs = []
        for df in dfs_to_process:
            temp_df = df.copy()
            if align_col == 'index':
                temp_df = temp_df.reset_index()
            
            # Use last occurrence of a step
            temp_df = temp_df.drop_duplicates(subset=[align_col], keep='last')
            temp_df = temp_df.set_index(align_col)
            processed_dfs.append(temp_df)

        if not processed_dfs:
            return None

        # Extract the series for x and y columns using _get_series
        # Filter out None results (where custom property evaluation failed)
        x_series_list = []
        y_series_list = []
        
        for df in processed_dfs:
            x_s = self._get_series(df, x_col)
            y_s = self._get_series(df, y_col)
            
            if x_s is not None: x_series_list.append(x_s)
            if y_s is not None: y_series_list.append(y_s)

        # If we couldn't get series for all DataFrames, we might still proceed with partial data?
        # Or strictly require all? Original logic required column presence.
        # Let's require at least some data.
        if not y_series_list:
            return None

        # Create dataframes from the series lists for easy averaging
        # Use an inner join to only average over common steps/indices
        y_df = pd.concat(y_series_list, axis=1, join='inner')
        
        y_mean = y_df.mean(axis=1)
        y_std = y_df.std(axis=1) if compute_std else None

        # Average x-values to get a representative x-axis
        if x_series_list:
            x_df = pd.concat(x_series_list, axis=1, join='inner')
            x_mean = x_df.mean(axis=1)
        else: # Should not happen if we have y_series
            x_mean = y_mean.index.to_series()

        # Ensure all results are aligned to the same index
        final_index = y_mean.index
        x_mean = x_mean.reindex(final_index)
        if y_std is not None:
            y_std = y_std.reindex(final_index)

        return {'x': x_mean, 'y': y_mean, 'std': y_std}