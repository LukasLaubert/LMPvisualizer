# lmp_visualizer/trajectory_parser.py

import mmap
import os
import re
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from lmpvisualizer.shared.logger_setup import get_logger

logger = get_logger(__name__)

class TrajectoryParser:
    """
    High-performance parser for LAMMPS trajectory files (.lammpstrj).
    Implements Lazy Loading and Persistent Indexing.
    """

    # Bump when the .idx schema or the DSD results_library hash meaning changes;
    # older sidecars are discarded via the version check in _load_index.
    # v2: centralized DSD hash identity (dsd_data_manager helpers) — domain +
    # reference fields unified across persistent hash and controller cache key.
    IDX_VERSION = 2

    def __init__(self, filepath: Path):
        self.filepath = Path(filepath)
        self.index_map = {}  # {timestep: (offset, num_atoms, box_bounds_offset)}
        self.timesteps = []
        self.columns = []
        self.global_box_bounds = None 
        self.is_indexed = False
        self.header_found = False
        self.results_library = {} # { hash: {added_at: str, label: str, data: dict} }
        self.metadata_cache = {} # { 'column_bounds': {col: (min, max)} }
        
        # Cache for estimated global min/max
        self._cached_min_max = {} 

        # Optimization: Try to load metadata from index immediately to avoid opening the large file
        idx_path = self.filepath.with_suffix(self.filepath.suffix + ".idx")
        if self._load_index(idx_path):
            if self.header_found:
                return

        # Fallback: Only open the file if no index or index missing metadata
        if self.filepath.exists():
            self._parse_metadata_only()

    def _parse_metadata_only(self):
        """Reads only the first chunk of the file to determine columns."""
        try:
            with open(self.filepath, "rb") as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    self._parse_header_from_mmap(mm)
        except Exception as e:
            logger.warning("Error reading metadata for %s: %s", self.filepath, e)

    def _ensure_index(self):
        """Lazy loader: Builds the full index if not already done."""
        if self.is_indexed and self.index_map and self.columns:
            return
        self._build_full_index()

    def _build_full_index(self):
        self.index_map.clear()
        self.timesteps = []
        idx_path = self.filepath.with_suffix(self.filepath.suffix + ".idx")
        
        if self._load_index(idx_path) and self.index_map:
            self.is_indexed = True
            return

        timestep_pattern = b"ITEM: TIMESTEP"
        atom_header_pattern = b"ITEM: ATOMS"
        
        try:
            with open(self.filepath, "rb") as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    if not self.header_found:
                        self._parse_header_from_mmap(mm)
                    
                    mm.seek(0)
                    last_pos = 0
                    while True:
                        pos = mm.find(timestep_pattern, last_pos)
                        if pos == -1: break
                        eol = mm.find(b'\n', pos)
                        val_start = eol + 1
                        val_end = mm.find(b'\n', val_start)
                        try:
                            step_val = int(mm[val_start:val_end])
                        except ValueError:
                            last_pos = val_end
                            continue
                        natoms_pos = mm.find(b"ITEM: NUMBER OF ATOMS", val_end)
                        natoms_eol = mm.find(b'\n', natoms_pos)
                        n_start, n_end = natoms_eol + 1, mm.find(b'\n', natoms_eol + 1)
                        try:
                            num_atoms = int(mm[n_start:n_end])
                        except ValueError:
                            num_atoms = 0
                        box_pos = mm.find(b"ITEM: BOX BOUNDS", n_end)
                        atoms_pos = mm.find(atom_header_pattern, n_end)
                        data_start = mm.find(b'\n', atoms_pos) + 1
                        self.index_map[step_val] = (data_start, num_atoms, box_pos)
                        self.timesteps.append(step_val)
                        last_pos = data_start

            self.timesteps.sort()
            self.is_indexed = True
            self._save_index(idx_path)
        except Exception as e:
            logger.warning("Error indexing trajectory file %s: %s", self.filepath, e)

    def _load_index(self, idx_path: Path) -> bool:
        if not idx_path.exists(): return False
        try:
            import json
            if os.path.getmtime(self.filepath) > os.path.getmtime(idx_path): return False
            with open(idx_path, 'r') as f:
                data = json.load(f)
            if data.get('version') != self.IDX_VERSION: return False
            self.columns = data.get('columns', [])
            self.global_box_bounds = data.get('global_box_bounds')
            self.header_found = bool(self.columns)
            idx_data = data.get('index', {})
            # Speed optimization: dict comprehension
            self.index_map = {int(k): tuple(v) for k, v in idx_data.items()}
            self.timesteps = sorted(list(self.index_map.keys()))
            self.results_library = data.get('results_library', {})
            self.metadata_cache = data.get('metadata_cache', {})
            return True
        except Exception as e:
            logger.warning("Failed to load index %s: %s", idx_path, e)
            return False

    def _save_index(self, idx_path: Path):
        try:
            import json
            with open(idx_path, 'w') as f:
                f.write('{\n')
                f.write(f'  "version": {self.IDX_VERSION},\n')
                f.write(f'  "file_size": {os.path.getsize(self.filepath)},\n')
                f.write(f'  "columns": {json.dumps(self.columns)},\n')
                f.write(f'  "global_box_bounds": {json.dumps(self.global_box_bounds)},\n')
                f.write(f'  "metadata_cache": {json.dumps(self.metadata_cache)},\n')
                f.write(f'  "results_library": {json.dumps(self.results_library)},\n')
                f.write(f'  "index": {json.dumps(self.index_map)}\n')
                f.write('}')
        except Exception as e:
            logger.warning("Failed to save index %s: %s", idx_path, e)

    def store_library_entry(self, result_hash: str, result_data: dict, label: str = None):
        from datetime import datetime
        self.results_library[result_hash] = {'added_at': datetime.now().isoformat(), 'label': label, 'data': result_data}
        idx_path = self.filepath.with_suffix(self.filepath.suffix + ".idx")
        self._save_index(idx_path)

    def store_metadata(self, key: str, value: Any):
        self.metadata_cache[key] = value
        idx_path = self.filepath.with_suffix(self.filepath.suffix + ".idx")
        self._save_index(idx_path)

    def _parse_header_from_mmap(self, mm: mmap.mmap):
        mm.seek(0)
        atoms_pos = mm.find(b"ITEM: ATOMS")
        if atoms_pos != -1:
            eol = mm.find(b'\n', atoms_pos)
            header_line = mm[atoms_pos:eol].decode('utf-8')
            parts = header_line.split()
            if len(parts) > 2:
                self.columns = parts[2:]
                self.header_found = True
        box_pos = mm.find(b"ITEM: BOX BOUNDS")
        if box_pos != -1:
            self.global_box_bounds = self._read_box_bounds_from_mmap(mm, box_pos)

    def _read_box_bounds_from_mmap(self, mm: mmap.mmap, header_offset: int) -> List[List[float]]:
        bounds = []
        mm.seek(header_offset)
        mm.readline()
        for _ in range(3):
            line = mm.readline().decode('utf-8').strip()
            parts = line.split()
            if len(parts) >= 2:
                try: bounds.append([float(p) for p in parts])
                except ValueError: bounds.append([0.0, 0.0])
        return bounds

    def get_frame(self, timestep: int) -> Tuple[Optional[pd.DataFrame], List[List[float]]]:
        self._ensure_index()
        if timestep not in self.index_map: return None, []
        if not self.columns: self._parse_metadata_only()
        data_offset, num_atoms, box_offset = self.index_map[timestep]
        try:
            with open(self.filepath, "rb") as f:
                f.seek(box_offset)
                f.readline()
                box_bounds = []
                for _ in range(3):
                    line = f.readline().decode('utf-8').strip()
                    parts = line.split()
                    if len(parts) >= 2: box_bounds.append([float(p) for p in parts])
                f.seek(data_offset)
                df = pd.read_csv(f, sep=r'\s+', names=self.columns, nrows=num_atoms, engine='c', header=None, index_col=False)
                return df, box_bounds
        except Exception as e:
            logger.warning("Error reading frame %s in %s: %s", timestep, self.filepath, e)
            return None, []

    def get_global_min_max(self, column: str) -> Tuple[float, float]:
        self._ensure_index()
        if column in self._cached_min_max: return self._cached_min_max[column]
        col_bounds = self.metadata_cache.get('column_bounds', {})
        if column in col_bounds:
            bounds = tuple(col_bounds[column])
            self._cached_min_max[column] = bounds
            return bounds
        if not self.timesteps or column not in self.columns: return 0.0, 1.0
        num_samples = min(len(self.timesteps), 20)
        indices = np.linspace(0, len(self.timesteps) - 1, num=num_samples, dtype=int)
        sample_steps = [self.timesteps[i] for i in indices]
        glob_min, glob_max = float('inf'), float('-inf')
        for t in sample_steps:
            df, _ = self.get_frame(t)
            if df is not None and column in df:
                c_min, c_max = df[column].min(), df[column].max()
                if c_min < glob_min: glob_min = c_min
                if c_max > glob_max: glob_max = c_max
        if glob_min == float('inf'): glob_min, glob_max = 0.0, 1.0
        bounds = (float(glob_min), float(glob_max))
        self._cached_min_max[column] = bounds
        if 'column_bounds' not in self.metadata_cache: self.metadata_cache['column_bounds'] = {}
        self.metadata_cache['column_bounds'][column] = bounds
        self.store_metadata('column_bounds', self.metadata_cache['column_bounds'])
        return bounds

    def get_column_names(self) -> List[str]:
        return self.columns

    def get_timesteps(self) -> List[int]:
        self._ensure_index()
        return self.timesteps
