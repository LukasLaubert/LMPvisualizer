# lmp_visualizer/trj_data_manager.py

from pathlib import Path
from typing import Dict, List, Tuple, Optional, Set, Any
import pandas as pd
from lmpvisualizer.shared.trajectory_parser import TrajectoryParser
from lmpvisualizer.shared.logger_setup import get_logger

logger = get_logger(__name__)

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

        # For TRJ the displayed System is the file stem when several files share
        # one folder, so the folder-based virtual from LogParser would be wrong.
        # Build parsers only from real studies and recompute virtual from the
        # actual stem list afterwards.
        try:
            from lmpvisualizer.log.log_parser import LogParser as _LP
            _is_virtual = _LP._is_virtual_study_key
            _wildcard = _LP._wildcard_pattern
        except Exception:
            _is_virtual = lambda x: "*" in x and "/" in x
            _wildcard = lambda x: x
        real_studies = {k: v for k, v in studies.items() if not _is_virtual(k)}

        for study_name, system_list in real_studies.items():
            self.parsers[study_name] = {}
            for system_name in system_list:
                key = f"{study_name}|{system_name}"
                target_files = file_map.get(key, [])
                
                if not target_files:
                    # root_path may be several project paths.
                    for base in (root_path if isinstance(root_path, (list, tuple)) else [root_path]):
                        system_path = Path(base) / study_name / system_name
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

        # --- Virtual wildcard studies from actual displayed systems ---
        # For single-system studies the displayed System is the folder name, but the
        # actual file stem may be the correct token. Try file stem and use it only
        # if its wildcard matches another stem (so the Study with one system joins the
        # group formed by multi-system Studies). Display stays as folder.
        try:
            from collections import defaultdict
            # Collect file stems for matching decision
            study_entries = {}  # study -> list of (disp_sys, file_stem, fpath)
            all_stem_wildcards = set()
            for study, sys_dict in list(self.parsers.items()):
                lst = []
                for disp_sys, entry in sys_dict.items():
                    fpath = entry if isinstance(entry, Path) else getattr(entry, 'file_path', None)
                    if fpath is None:
                        # Fallback: try to get from entry if it's a Parser with .file ?
                        try:
                            fpath = Path(str(entry))
                        except Exception:
                            continue
                    try:
                        file_stem = Path(fpath).stem
                    except Exception:
                        file_stem = disp_sys
                    lst.append((disp_sys, file_stem, fpath))
                    all_stem_wildcards.add(_wildcard(file_stem))
                study_entries[study] = lst

            # Build effective joints: for single-system studies try file stem if it matches
            effective_joints = []  # (study, eff_sys, fpath)
            for study, lst in study_entries.items():
                if len(lst) == 1:
                    disp_sys, file_stem, fpath = lst[0]
                    if disp_sys != file_stem:
                        # Does fileStem wildcard occur elsewhere (other study)?
                        fs_wild = _wildcard(file_stem)
                        found = False
                        for other_study, other_lst in study_entries.items():
                            if other_study == study:
                                continue
                            for _, other_stem, _ in other_lst:
                                if _wildcard(other_stem) == fs_wild:
                                    found = True
                                    break
                            if found:
                                break
                        if found:
                            effective_joints.append((study, file_stem, fpath))
                            continue
                    effective_joints.append((study, disp_sys, fpath))
                else:
                    for disp_sys, file_stem, fpath in lst:
                        effective_joints.append((study, disp_sys, fpath))

            joints = []
            for study, eff_sys, fpath in effective_joints:
                if " › " in study:
                    label, raw = study.split(" › ", 1)
                    pat = f"{label} › {_wildcard(raw)}/{_wildcard(eff_sys)}"
                else:
                    pat = f"{_wildcard(study)}/{_wildcard(eff_sys)}"
                if "*" not in pat:
                    continue
                joints.append((study, eff_sys, pat, fpath))
            groups = defaultdict(list)
            for s, sys, pat, fpath in joints:
                groups[pat].append((s, sys, fpath))
            for pat, members in groups.items():
                if len(members) < 2:
                    continue
                if pat in self.parsers:
                    continue
                sys_union = sorted({m[1] for m in members})
                if not sys_union:
                    continue
                self.parsers[pat] = {}
                for study, sys, fpath in members:
                    if sys in self.parsers[pat]:
                        continue
                    self.parsers[pat][sys] = fpath
        except Exception as e:
            logger.warning("[System] TRJ virtual grouping failed: %s", e)

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
                logger.warning("Error initializing parser for %s: %s", entry, e)
                return None
                
        return entry

    def get_study_names(self) -> List[str]:
        try:
            from lmpvisualizer.log.log_parser import LogParser as _LP
            return sorted(self.parsers.keys(), key=lambda k: (_LP._is_virtual_study_key(k), k))
        except Exception:
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
