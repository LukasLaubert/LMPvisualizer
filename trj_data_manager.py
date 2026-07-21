# lmp_visualizer/trj_data_manager.py

from pathlib import Path
from typing import Dict, List, Tuple, Optional, Set, Any
import pandas as pd
from trajectory_parser import TrajectoryParser

class TrjDataManager:
    def __init__(self):
        # Storage: {study_name: {system_name: TrajectoryParser or Path}}
        self.parsers: Dict[str, Dict[str, Any]] = {}
        self.available_columns: List[str] = []
        self.warnings: List[str] = []
        self.successful_keywords: Set[str] = set()

    def load_project_data(self, studies: Dict[str, List[str]], root_path: Path, keywords: List[str], file_map: Dict[str, List[Path]] = None):
        """Discovers files but defers parser creation until needed (Lazy Loading)."""
        self.parsers.clear()
        self.available_columns = []
        self.warnings = []
        self.successful_keywords = set()

        if file_map is None: file_map = {}
        all_cols_found = set()

        for study_name, system_list in studies.items():
            self.parsers[study_name] = {}
            for system_name in system_list:
                key = f"{study_name}|{system_name}"
                target_files = file_map.get(key, [])
                
                if not target_files:
                    system_path = root_path / study_name / system_name
                    if system_path.is_dir():
                        for keyword in keywords:
                            target_files.extend(list(system_path.glob(f"*{keyword}*")))
                
                if not target_files: continue
                target_files.sort()
                
                # Store only the PATH. TrajectoryParser will be created on-demand in get_parser()
                for fpath in target_files:
                    key_name = system_name if len(target_files) == 1 else fpath.stem
                    self.parsers[study_name][key_name] = fpath
                    
                    for kw in keywords:
                        if kw in fpath.name: self.successful_keywords.add(kw)
                    
                    # Optimization: Try to peek at the .idx file to get columns without opening the main file
                    idx_path = fpath.with_suffix(fpath.suffix + ".idx")
                    if idx_path.exists():
                        try:
                            import json
                            with open(idx_path, 'r') as f:
                                data = json.load(f)
                                for col in data.get('columns', []):
                                    all_cols_found.add(col)
                        except: pass

        if not self.parsers:
             self.warnings.append("No valid trajectory files found with provided keywords.")

        # Update available columns from what we found in indices
        sorted_cols = sorted(list(all_cols_found))
        priority = ['id', 'type', 'x', 'y', 'z', 'vx', 'vy', 'vz', 'fx', 'fy', 'fz']
        self.available_columns = [c for c in priority if c in sorted_cols] + [c for c in sorted_cols if c not in priority]

        return self.warnings, self.successful_keywords

    def get_parser(self, study: str, system: str) -> Optional[TrajectoryParser]:
        """Lazy-loads the parser for a specific system only when accessed."""
        if study not in self.parsers or system not in self.parsers[study]:
            return None
            
        entry = self.parsers[study][system]
        
        if isinstance(entry, Path):
            try:
                parser = TrajectoryParser(entry)
                self.parsers[study][system] = parser
                # Update global column list if this parser found new ones
                for col in parser.get_column_names():
                    if col not in self.available_columns:
                        self.available_columns.append(col)
                return parser
            except Exception as e:
                print(f"Error initializing parser for {entry}: {e}")
                return None
                
        return entry

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

    def get_timesteps(self, study: str, system: str) -> List[int]:
        parser = self.get_parser(study, system)
        return parser.get_timesteps() if parser else []

    def get_frame(self, study: str, system: str, timestep: int) -> Tuple[Optional[pd.DataFrame], List[List[float]]]:
        parser = self.get_parser(study, system)
        return parser.get_frame(timestep) if parser else (None, [])

    def get_global_min_max(self, study: str, system: str, column: str) -> Tuple[float, float]:
        parser = self.get_parser(study, system)
        return parser.get_global_min_max(column) if parser else (0.0, 1.0)
