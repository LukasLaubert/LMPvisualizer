# lmp_visualizer/lammps_parser.py

import re
import os
import pandas as pd
from pathlib import Path
from io import StringIO
from typing import Dict, List, Tuple, Optional

class LammpsParser:
    """Parses LAMMPS project structures and log files."""

    @staticmethod
    def find_project_root(path: str) -> Path:
        """
        Searches up to two parent directories from the given path to find a valid project root.
        A valid root contains at least one subdirectory not named 'input_files'.
        """
        start_path = Path(path).resolve()
        for i in range(3):
            current_path = start_path if i == 0 else start_path.parents[i-1]
            if not current_path.exists():
                continue

            subdirs = [d for d in current_path.iterdir() if d.is_dir()]
            if any("input_files" not in d.name for d in subdirs):
                # A basic check to see if it looks like a project root
                has_input_files = any("input_files" in d.name for d in subdirs)
                if has_input_files:
                    return current_path
        
        raise FileNotFoundError(f"Could not find a valid LAMMPS project root in or above '{path}'.")

    @staticmethod
    def discover_studies_systems(root_path: Path) -> Tuple[Dict[str, List[str]], List[str]]:
        """
        Discovers studies and systems from the root path.
        Returns a dictionary of {study: [systems]} and a list of warnings.
        """
        studies = {}
        warnings = []
        
        study_dirs = [d for d in root_path.iterdir() if d.is_dir() and "input_files" not in d.name]
        
        if not study_dirs:
            return {}, ["No study directories found in the root path."]

        # First pass to find all systems and establish a base set
        base_systems = None
        for study_dir in study_dirs:
            systems = sorted([s.name for s in study_dir.iterdir() if s.is_dir()])
            if not systems:
                warnings.append(f"Study '{study_dir.name}' contains no system subdirectories.")
                continue
            studies[study_dir.name] = systems
            if base_systems is None:
                base_systems = set(systems)

        if base_systems is None:
             return {}, ["No valid systems found across any studies."]

        # Second pass to check for consistency
        consistent_studies = {}
        all_systems_found = set()
        for study_name, system_list in studies.items():
            current_systems = set(system_list)
            if current_systems != base_systems:
                warnings.append(
                    f"Study '{study_name}' has an inconsistent system set. "
                    f"Missing: {base_systems - current_systems}. "
                    f"Extra: {current_systems - base_systems}. Skipping study."
                )
            else:
                consistent_studies[study_name] = system_list
                all_systems_found.update(system_list)

        return consistent_studies, warnings
    
    @staticmethod
    def extract_thermo_data(logfile_path: Path) -> Optional[pd.DataFrame]:
        """
        Efficiently extracts thermo data from a LAMMPS log file into a pandas DataFrame.
        Handles log files with multiple data sections.
        """
        try:
            with open(logfile_path, 'r') as f:
                lines = f.readlines()
        except Exception:
            return None

        all_data_lines = []
        header_line = None
        in_data_block = False

        header_regex = re.compile(r'^\s*Step\s+')
        end_block_regex = re.compile(r'^\s*Loop time of')

        for line in lines:
            if header_regex.match(line):
                if header_line is None:
                    header_line = line.strip()
                in_data_block = True
                # Skip the header line itself from being added to data
                continue

            if in_data_block:
                if end_block_regex.match(line):
                    in_data_block = False
                else:
                    # Add a check to ensure the line looks like data
                    if re.match(r'^\s*[-0-9]', line):
                        all_data_lines.append(line)

        if not header_line or not all_data_lines:
            return None
        
        # Use StringIO to let pandas read the string data as if it were a file
        column_names = header_line.split()
        data_io = StringIO(''.join(all_data_lines))
        
        try:
            # FIX: Changed delim_whitespace to sep='\s+'
            df = pd.read_csv(data_io, sep=r'\s+', names=column_names, engine='python')
            
            # Drop duplicate steps, keeping the last occurrence
            df.drop_duplicates(subset='Step', keep='last', inplace=True)

            # Ensure all numeric columns are actually numeric, coercing errors
            for col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df.dropna(inplace=True) # Drop rows where coercion failed
            return df
        except Exception:
            return None

    @staticmethod
    def get_timestep(root_path: Path) -> Optional[float]:
        """Scans for a base_input.in or single .in file to find the timestep."""
        in_files = list(root_path.glob("*.in"))
        target_file = None
        if len(in_files) == 1:
            target_file = in_files[0]
        else:
            for f in in_files:
                if 'base_input' in f.name:
                    target_file = f
                    break
        
        if not target_file:
            return None
        
        timestep_regex = re.compile(r'^\s*timestep\s+([0-9.]+)')
        with open(target_file, 'r') as f:
            for line in f:
                match = timestep_regex.match(line)
                if match:
                    return float(match.group(1))
        return None

    @staticmethod
    def get_units(root_path: Path) -> Optional[str]:
        """Looks for a .data file in input_files to get units."""
        input_dir = None
        for d in root_path.iterdir():
            if "input_files" in d.name and d.is_dir():
                input_dir = d
                break
        
        if not input_dir:
            return None
            
        data_files = list(input_dir.glob("*.data"))
        if not data_files:
            return None
            
        # "LAMMPS data file ... timestep = ... units = real"
        units_regex = re.compile(r'units\s*=\s*(\w+)')
        for df in data_files:
            try:
                with open(df, 'r') as f:
                    first_line = f.readline()
                    match = units_regex.search(first_line)
                    if match:
                        return match.group(1)
            except Exception:
                continue
        return None