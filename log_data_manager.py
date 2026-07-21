# lmp_visualizer/data_manager.py

import pandas as pd
from pathlib import Path
from typing import Dict, Optional, Tuple, List

class LogDataManager:
    """Manages all simulation data using pandas."""
    def __init__(self):
        self.data: Dict[str, Dict[str, pd.DataFrame]] = {} # {study: {system: df}}
        self.warnings = []
        self.available_columns = []

    def load_project_data(self, studies: Dict[str, List[str]], root_path, log_keywords: List[str] = None, file_map: Dict[str, Path] = None):
        """Loads all log file data for the discovered studies and systems."""
        from log_parser import LogParser # Local import
        self.data.clear()
        self.warnings = []
        self.available_columns = []
        
        all_cols_ordered = []
        successful_keywords = set()
        if file_map is None:
            file_map = {}

        for study_name, system_list in studies.items():
            self.data[study_name] = {}
            for system_name in system_list:
                log_files = []
                if study_name == '.': # Flat directory or single file mode
                    if system_name in file_map:
                        log_files = [file_map[system_name]]
                else: # Standard project structure
                    log_path = root_path / study_name / system_name
                    if not log_keywords:
                        log_files = list(log_path.glob("log.lammps"))
                    else:
                        for keyword in log_keywords:
                            log_files.extend(log_path.glob(f"*{keyword}*"))
                        log_files = sorted(list(set(log_files)))

                if not log_files:
                    self.warnings.append(f"No log files found for: {study_name}/{system_name}")
                    continue
                
                # This check is now implicitly handled by the fact that we found the files.
                # We can simplify the successful_keywords logic.
                for keyword in log_keywords:
                    for file_path in log_files:
                        if keyword in file_path.name:
                            successful_keywords.add(keyword)

                df = LogParser.parse_multiple_logs(log_files)

                if df is not None and not df.empty:
                    self.data[study_name][system_name] = df
                    for col in df.columns:
                        if col not in all_cols_ordered:
                            all_cols_ordered.append(col)
                else:
                    self.warnings.append(f"Could not parse thermo data for: {study_name}/{system_name}")
        
        self.available_columns = all_cols_ordered
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

    def check_data_consistency(self, study: str) -> Dict[str, int]:
        """
        Checks if dataframes for a study have the same length.
        Returns a dictionary of {system_name: length}.
        """
        if study not in self.data:
            return {}
        
        lengths = {sys: len(df) for sys, df in self.data[study].items()}
        
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
            if system in self.data[study] and x_col in self.data[study][system] and y_col in self.data[study][system]:
                df = self.data[study][system]
                return {'x': df[x_col], 'y': df[y_col], 'std': None}
            return None
        
        # --- Averaging Logic ---
        study_dfs = self.data[study]
        
        dfs_to_process = []
        if user_choices:
            truncate_len = user_choices.get('truncate_len')
            exclude_systems = user_choices.get('exclude', [])
            
            for sys_name, df in study_dfs.items():
                if sys_name in exclude_systems:
                    continue
                if truncate_len is not None:
                    dfs_to_process.append(df.iloc[:truncate_len])
                else:
                    dfs_to_process.append(df)
        else:
            dfs_to_process = list(study_dfs.values())

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

        # Extract the series for x and y columns
        x_series_list = [df[x_col] for df in processed_dfs if x_col in df.columns]
        y_series_list = [df[y_col] for df in processed_dfs if y_col in df.columns]

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