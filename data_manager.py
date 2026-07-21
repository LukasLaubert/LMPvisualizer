# lmp_visualizer/data_manager.py

import pandas as pd
from typing import Dict, Optional, Tuple, List

class DataManager:
    """Manages all simulation data using pandas."""
    
    def __init__(self):
        self.data: Dict[str, Dict[str, pd.DataFrame]] = {} # {study: {system: df}}
        self.warnings = []
        self.available_columns = []

    def load_project_data(self, studies: Dict[str, List[str]], root_path):
        """Loads all log file data for the discovered studies and systems."""
        from lammps_parser import LammpsParser # Local import
        self.data.clear()
        self.warnings = []
        
        all_cols = []
        # Use a set for quick lookups of columns already added
        seen_cols = set()
        for study_name, system_list in studies.items():
            self.data[study_name] = {}
            for system_name in system_list:
                log_path = root_path / study_name / system_name
                # Find any log.lammps file in the directory
                log_files = list(log_path.glob("log.lammps"))
                if not log_files:
                    self.warnings.append(f"No .log file found for: {study_name}/{system_name}")
                    continue
                
                df = LammpsParser.extract_thermo_data(log_files[0])
                if df is not None and not df.empty:
                    self.data[study_name][system_name] = df
                    for col in df.columns:
                        if col not in seen_cols:
                            all_cols.append(col)
                            seen_cols.add(col)
                else:
                    self.warnings.append(f"Could not parse thermo data for: {study_name}/{system_name}")
        
        self.available_columns = all_cols

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
        user_choices = {'truncate_step': int, 'exclude': [str]}
        """
        if study not in self.data:
            return None

        if system != 'average':
            if system in self.data[study] and x_col in self.data[study][system] and y_col in self.data[study][system]:
                df = self.data[study][system]
                return {'x': df[x_col], 'y': df[y_col], 'std': None}
            return None
        
        # Handle averaging
        study_dfs = self.data[study]
        
        # Apply user choices if provided
        dfs_to_average = []
        if user_choices:
            truncate_len = user_choices.get('truncate_len')
            exclude_systems = user_choices.get('exclude', [])
            
            for sys_name, df in study_dfs.items():
                if sys_name in exclude_systems:
                    continue
                if truncate_len is not None:
                    dfs_to_average.append(df.iloc[:truncate_len])
                else:
                    dfs_to_average.append(df)
        else:
            dfs_to_average = list(study_dfs.values())

        if not dfs_to_average:
            return None

        # Concatenate and group by the x-axis values (assuming 'Step' or similar index)
        try:
            combined_df = pd.concat(dfs_to_average)
            grouped = combined_df.groupby(x_col)
            
            mean_df = grouped.mean()
            
            result = {'x': mean_df.index.values, 'y': mean_df[y_col].values, 'std': None}
            
            if compute_std:
                std_df = grouped.std()
                result['std'] = std_df[y_col].values

            return result
        except Exception:
            # This can fail if x_col is not suitable for grouping (e.g. not monotonic)
            return None