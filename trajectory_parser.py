# lmp_visualizer/trajectory_parser.py

import mmap
import os
import re
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any

class TrajectoryParser:
    """
    High-performance parser for LAMMPS trajectory files (.lammpstrj).
    Implements Lazy Loading:
    1. Metadata (columns) is parsed immediately.
    2. Full timestep indexing is deferred until the system is actually accessed.
    """

    def __init__(self, filepath: Path):
        self.filepath = Path(filepath)
        self.index_map = {}  # {timestep: (offset, num_atoms, box_bounds_offset)}
        self.timesteps = []
        self.columns = []
        self.global_box_bounds = None 
        self.is_indexed = False
        self.header_found = False
        
        # Cache for estimated global min/max
        self._cached_min_max = {} 

        if self.filepath.exists():
            # Only parse metadata (columns) on init for speed
            self._parse_metadata_only()

    def _parse_metadata_only(self):
        """Reads only the first chunk of the file to determine columns."""
        try:
            with open(self.filepath, "rb") as f:
                # We assume header is within the first 10KB
                # Use mmap to avoid reading the whole file
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    self._parse_header_from_mmap(mm)
        except Exception as e:
            print(f"Error reading metadata for {self.filepath}: {e}")

    def _ensure_index(self):
        """Lazy loader: Builds the full index if not already done."""
        if self.is_indexed:
            return
        self._build_full_index()

    def _build_full_index(self):
        """
        Scans the file to build an index of timesteps and byte offsets.
        This is performed only when the system is selected.
        """
        self.index_map.clear()
        self.timesteps = []
        
        timestep_pattern = b"ITEM: TIMESTEP"
        atom_header_pattern = b"ITEM: ATOMS"
        
        try:
            with open(self.filepath, "rb") as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    # Parse metadata again just in case, or to ensure we have box bounds
                    if not self.header_found:
                        self._parse_header_from_mmap(mm)
                    
                    mm.seek(0)
                    last_pos = 0
                    
                    while True:
                        pos = mm.find(timestep_pattern, last_pos)
                        if pos == -1:
                            break
                        
                        # Read Timestep Value
                        eol = mm.find(b'\n', pos)
                        val_start = eol + 1
                        val_end = mm.find(b'\n', val_start)
                        
                        try:
                            step_val = int(mm[val_start:val_end])
                        except ValueError:
                            last_pos = val_end
                            continue

                        # Read Num Atoms
                        natoms_pos = mm.find(b"ITEM: NUMBER OF ATOMS", val_end)
                        natoms_eol = mm.find(b'\n', natoms_pos)
                        n_start = natoms_eol + 1
                        n_end = mm.find(b'\n', n_start)
                        
                        try:
                            num_atoms = int(mm[n_start:n_end])
                        except ValueError:
                            num_atoms = 0

                        # Find Box Bounds Offset
                        box_pos = mm.find(b"ITEM: BOX BOUNDS", n_end)
                        
                        # Find Data Start
                        atoms_pos = mm.find(atom_header_pattern, n_end)
                        atoms_eol = mm.find(b'\n', atoms_pos)
                        data_start = atoms_eol + 1
                        
                        self.index_map[step_val] = (data_start, num_atoms, box_pos)
                        self.timesteps.append(step_val)
                        
                        last_pos = data_start

            self.timesteps.sort()
            self.is_indexed = True
            
        except Exception as e:
            print(f"Error indexing trajectory file {self.filepath}: {e}")

    def _parse_header_from_mmap(self, mm: mmap.mmap):
        """Helper to extract columns and initial box bounds."""
        mm.seek(0)
        
        # Find Columns
        atoms_pos = mm.find(b"ITEM: ATOMS")
        if atoms_pos != -1:
            eol = mm.find(b'\n', atoms_pos)
            header_line = mm[atoms_pos:eol].decode('utf-8')
            parts = header_line.split()
            if len(parts) > 2:
                self.columns = parts[2:]
                self.header_found = True
        
        # Find Box Bounds
        box_pos = mm.find(b"ITEM: BOX BOUNDS")
        if box_pos != -1:
            self.global_box_bounds = self._read_box_bounds_from_mmap(mm, box_pos)

    def _read_box_bounds_from_mmap(self, mm: mmap.mmap, header_offset: int) -> List[List[float]]:
        bounds = []
        mm.seek(header_offset)
        mm.readline() # Skip header
        for _ in range(3):
            line = mm.readline().decode('utf-8').strip()
            parts = line.split()
            if len(parts) >= 2:
                try:
                    # Capture all parts (2 for ortho, 3 for triclinic)
                    bounds.append([float(p) for p in parts])
                except ValueError:
                    bounds.append([0.0, 0.0])
        return bounds

    def get_frame(self, timestep: int) -> Tuple[Optional[pd.DataFrame], List[List[float]]]:
        self._ensure_index() # Lazy load if needed
        
        if timestep not in self.index_map:
            return None, []

        data_offset, num_atoms, box_offset = self.index_map[timestep]
        
        try:
            with open(self.filepath, "rb") as f:
                # Read Box
                f.seek(box_offset)
                f.readline()
                box_bounds = []
                for _ in range(3):
                    line = f.readline().decode('utf-8').strip()
                    parts = line.split()
                    if len(parts) >= 2:
                        # Capture all parts (2 for ortho, 3 for triclinic)
                        box_bounds.append([float(p) for p in parts])

                # Read Atoms
                f.seek(data_offset)
                df = pd.read_csv(
                    f, 
                    sep=r'\s+', 
                    names=self.columns, 
                    nrows=num_atoms,
                    engine='c',      
                    header=None,     
                    index_col=False  
                )
                return df, box_bounds

        except Exception as e:
            print(f"Error reading frame {timestep}: {e}")
            return None, []

    def get_global_min_max(self, column: str) -> Tuple[float, float]:
        """
        Returns an approximate global min/max for a column.
        Samples up to 20 frames evenly distributed across the trajectory.
        """
        self._ensure_index() # Lazy load if needed
        
        if column not in self._cached_min_max:
            if not self.timesteps or column not in self.columns:
                return 0.0, 1.0
            
            # Sample up to 20 frames for better accuracy on "Global" bounds
            num_samples = min(len(self.timesteps), 20)
            indices = np.linspace(0, len(self.timesteps) - 1, num=num_samples, dtype=int)
            sample_steps = [self.timesteps[i] for i in indices]
            
            glob_min = float('inf')
            glob_max = float('-inf')
            
            for t in sample_steps:
                df, _ = self.get_frame(t)
                if df is not None and column in df:
                    c_min = df[column].min()
                    c_max = df[column].max()
                    if c_min < glob_min: glob_min = c_min
                    if c_max > glob_max: glob_max = c_max
            
            # Fallback if no data found
            if glob_min == float('inf'):
                glob_min, glob_max = 0.0, 1.0

            self._cached_min_max[column] = (glob_min, glob_max)
            
        return self._cached_min_max[column]

    def get_column_names(self) -> List[str]:
        # Available immediately after init
        return self.columns

    def get_timesteps(self) -> List[int]:
        self._ensure_index() # Lazy load if needed
        return self.timesteps