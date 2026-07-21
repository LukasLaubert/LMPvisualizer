# lmp_visualizer/trj_data_manager.py

from pathlib import Path
from typing import Dict, List, Tuple, Optional, Set
import pandas as pd
from trajectory_parser import TrajectoryParser

class TrjDataManager:
    def __init__(self):
        # Storage: {study_name: {system_name: TrajectoryParser}}
        self.parsers: Dict[str, Dict[str, TrajectoryParser]] = {}
        self.available_columns: List[str] = []
        self.warnings: List[str] = []
        self.successful_keywords: Set[str] = set()

    def load_project_data(self, studies: Dict[str, List[str]], root_path: Path, keywords: List[str], file_map: Dict[str, Path] = None):
        self.parsers.clear()
        self.available_columns = []
        self.warnings = []
        self.successful_keywords = set()

        if file_map is None:
            file_map = {}

        all_cols_found = set()

        for study_name, system_list in studies.items():
            self.parsers[study_name] = {}
            
            for system_name in system_list:
                target_files = []

                # Check for "study|system" key first (Flat/Parent mode)
                flat_key = f"{study_name}|{system_name}"

                # Case 1: Flat/Single file
                if flat_key in file_map:
                    entry = file_map[flat_key]
                    target_files = entry if isinstance(entry, list) else [entry]
                elif system_name in file_map:
                    entry = file_map[system_name]
                    target_files = entry if isinstance(entry, list) else [entry]
                
                # Case 2: Standard Project
                else:
                    system_path = root_path / study_name / system_name
                    if system_path.is_dir():
                        for keyword in keywords:
                            # Search for files containing keyword
                            # If keyword is an extension like .lammpstrj, this works
                            found = list(system_path.glob(f"*{keyword}*"))
                            target_files.extend(found)
                
                if not target_files:
                    continue

                # Sort to prefer shorter names or specific extensions
                target_files.sort()
                
                loaded_any = False
                
                # Iterate through ALL found files and register them
                for fpath in target_files:
                    try:
                        # Only metadata is parsed on init, so this is fast
                        parser = TrajectoryParser(fpath)
                        
                        # Check if we successfully got columns
                        if parser.get_column_names():
                            # Naming Logic:
                            # 1. If only one file is found in the system folder, 
                            #    always use the system folder name as the key.
                            # 2. If multiple files are found, use the filename.
                            # 3. Handle standard Study_System prefix as before.
                            
                            if len(target_files) == 1 and flat_key not in file_map:
                                key_name = system_name
                            else:
                                standard_prefix = f"{study_name}_{system_name}"
                                if fpath.stem == standard_prefix:
                                    key_name = system_name
                                else:
                                    key_name = fpath.stem

                            self.parsers[study_name][key_name] = parser
                            
                            for kw in keywords:
                                if kw in fpath.name:
                                    self.successful_keywords.add(kw)
                            
                            for col in parser.get_column_names():
                                all_cols_found.add(col)
                            
                            loaded_any = True
                            
                    except Exception as e:
                        print(f"Failed to init parser for {fpath}: {e}")

                if not loaded_any:
                    # If we found files but couldn't parse any, warn the user
                    self.warnings.append(f"Found files for {system_name} but could not parse headers.")

        sorted_cols = sorted(list(all_cols_found))
        priority = ['id', 'type', 'x', 'y', 'z', 'vx', 'vy', 'vz', 'fx', 'fy', 'fz']
        self.available_columns = [c for c in priority if c in sorted_cols] + [c for c in sorted_cols if c not in priority]

        if not self.parsers:
             self.warnings.append("No valid trajectory files found with provided keywords.")

        return self.warnings, self.successful_keywords

    def get_study_names(self) -> List[str]:
        return sorted(list(self.parsers.keys()))

    def get_system_names(self, study: str) -> List[str]:
        if study in self.parsers:
            return sorted(list(self.parsers[study].keys()))
        return []

    def get_all_system_names(self) -> List[str]:
        all_systems = set()
        for study in self.parsers:
            for system in self.parsers[study]:
                all_systems.add(system)
        return sorted(list(all_systems))

    def get_all_column_names(self) -> List[str]:
        return self.available_columns

    def get_parser(self, study: str, system: str) -> Optional[TrajectoryParser]:
        return self.parsers.get(study, {}).get(system)

    def get_timesteps(self, study: str, system: str) -> List[int]:
        parser = self.get_parser(study, system)
        if parser:
            return parser.get_timesteps()
        return []

    def get_frame(self, study: str, system: str, timestep: int) -> Tuple[Optional[pd.DataFrame], List[Tuple[float, float]]]:
        parser = self.get_parser(study, system)
        if parser:
            return parser.get_frame(timestep)
        return None, []

    def get_global_min_max(self, study: str, system: str, column: str) -> Tuple[float, float]:
        parser = self.get_parser(study, system)
        if parser:
            return parser.get_global_min_max(column)
        return 0.0, 1.0