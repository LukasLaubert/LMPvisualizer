# lmp_visualizer/lammps_parser.py

import re
import os
import pandas as pd
from pathlib import Path
from io import StringIO
from typing import Dict, List, Tuple, Optional

class LogParser:
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
            if not current_path.is_dir():
                continue

            subdirs = [d for d in current_path.iterdir() if d.is_dir()]
            if any("input_files" not in d.name for d in subdirs):
                has_input_files = any("input_files" in d.name for d in subdirs)
                if has_input_files:
                    return current_path
        
        raise FileNotFoundError(f"Could not find a valid LAMMPS project root in or above '{path}'.")

    @staticmethod
    def discover_studies_systems(path: Path, log_keywords: List[str]) -> Tuple[Dict[str, List[str]], List[str], Dict[str, List[Path]]]:
        """
        Discovers studies and systems by recursively finding log files.
        Study = Grandparent folder, System = Parent folder of the log file.
        Groups multiple logs in the same folder into a single system.
        """
        warnings = []
        file_map = {} # { "study|system": [List of Paths] }
        studies = {}

        if not path.is_dir():
            if path.is_file():
                studies = {'.': [path.stem]}
                file_map = {f".|{path.stem}": [path]}
                return studies, warnings, file_map
            return {}, ["Path does not exist."], {}

        # 1. Find all potential log files recursively
        all_found = []
        search_keywords = log_keywords if log_keywords else ["log.lammps"]
        for kw in search_keywords:
            # Use rglob for deep discovery
            pattern = f"*{kw}*" if "." not in kw else f"*{kw}"
            all_found.extend(list(path.rglob(pattern)))
        
        # 2. Filter and Group by directory
        unique_files = sorted(list(set(f for f in all_found if f.is_file())))
        
        for fpath in unique_files:
            # Skip hidden or export folders
            if any(p.startswith(('.', '_')) for p in fpath.relative_to(path).parts[:-1]):
                continue
                
            try:
                rel_parts = fpath.relative_to(path).parent.parts
                
                # Determine Study/System names based on hierarchy
                if len(rel_parts) >= 2:
                    study = rel_parts[-2]
                    system = rel_parts[-1]
                elif len(rel_parts) == 1:
                    study = path.name
                    system = rel_parts[0]
                else:
                    # File is in the root
                    study = "."
                    system = fpath.stem

                if study not in studies:
                    studies[study] = []
                if system not in studies[study]:
                    studies[study].append(system)

                key = f"{study}|{system}"
                if key not in file_map:
                    file_map[key] = []
                file_map[key].append(fpath)
                
            except Exception:
                continue

        if not studies:
            return {}, ["No log files found matching keywords in the directory hierarchy."], {}

        return studies, warnings, file_map

    # --- Multi-path support --------------------------------------------------
    #
    # Several project paths can be loaded side by side. Discovery runs per path and
    # the results are merged into the single {study: [systems]} shape the panels have
    # always consumed. Study names collide easily (study = grandparent folder), so a
    # colliding name is qualified with just enough of its path to tell them apart.
    # A study name that only one path owns is left exactly as it was, which keeps
    # single-path projects - and their saved sessions - byte-identical.

    STUDY_SEP = " › "  # single-right-pointing-angle quotation mark

    @staticmethod
    def canonical_path(value) -> str:
        """One spelling per project path.

        Chips, study origins and the (source path, study) pair stored on every table
        row are all compared as plain strings, so they have to agree character for
        character. They did not: a path typed with forward slashes, pasted with a
        trailing separator or restored from an older session file kept whatever
        spelling it arrived with, while discovery reported it back as str(Path(...)).
        Every row then looked like it belonged to a path that was no longer loaded
        and got pruned. Canonicalising at the door keeps the two in step.
        """
        text = str(value).strip().strip('"')
        if not text:
            return ""
        try:
            return str(Path(text))
        except Exception:
            return text

    @staticmethod
    def path_key(value) -> str:
        """Comparison key for a project path (Windows paths differ only in case)."""
        return os.path.normcase(LogParser.canonical_path(value))

    @staticmethod
    def same_path(a, b) -> bool:
        return LogParser.path_key(a) == LogParser.path_key(b)

    @staticmethod
    def normalize_paths(value) -> List[str]:
        """Accepts a str/Path or an iterable of them; returns de-duplicated strings."""
        if value is None:
            return []
        if isinstance(value, (str, Path)):
            value = [value]
        out, seen = [], set()
        for item in value:
            text = LogParser.canonical_path(item)
            key = os.path.normcase(text)
            if text and key not in seen:
                seen.add(key)
                out.append(text)
        return out

    @staticmethod
    def _shortest_distinct_labels(paths: List[Path]) -> Dict[str, str]:
        """Labels each path with the fewest trailing folders that keep it unique."""
        labels = {}
        parts = {str(p): [x for x in Path(p).parts if x not in ('/', '\\')] for p in paths}
        depth = 1
        remaining = list(parts.keys())

        while remaining and depth <= 8:
            candidate = {p: "/".join(parts[p][-depth:]) if parts[p] else p for p in remaining}
            counts = {}
            for label in candidate.values():
                counts[label] = counts.get(label, 0) + 1

            still = []
            for p, label in candidate.items():
                if counts[label] == 1 or depth >= len(parts[p]):
                    labels[p] = label
                else:
                    still.append(p)
            remaining = still
            depth += 1

        for p in remaining:  # pathological ties - fall back to the full path
            labels[p] = p
        return labels

    @staticmethod
    def discover_multi(paths, log_keywords: List[str]):
        """Discovery merged across several project paths.

        Returns (studies, warnings, file_map, origins) where origins maps every study
        key to {'path': <project path>, 'study': <raw folder name>} so a row can be
        traced back to the chip it came from even after the key gets qualified.
        """
        # Canonical spelling throughout: origins['path'] is compared against the chip
        # set and against the path stored on every table row.
        paths = [Path(p) for p in LogParser.normalize_paths(paths)]

        studies, warnings, file_map, origins = {}, [], {}, {}
        if not paths:
            return studies, ["No project path selected."], file_map, origins

        per_path = []
        for p in paths:
            s, w, fm = LogParser.discover_studies_systems(p, log_keywords)
            # Only surface per-path complaints when that path really is empty; a path
            # that found nothing must not mask the ones that did.
            if s:
                per_path.append((p, s, fm))
            else:
                warnings.append(f"{p}: no matching files found.")
            warnings.extend(w if s else [])

        if not per_path:
            return {}, warnings or ["No files found matching keywords."], {}, {}

        # Which raw study names are claimed by more than one path?
        owners = {}
        for p, s, _ in per_path:
            for study in s:
                owners.setdefault(study, set()).add(str(p))

        colliding = {study for study, ps in owners.items() if len(ps) > 1}
        labels = LogParser._shortest_distinct_labels([p for p, _, _ in per_path])

        for p, s, fm in per_path:
            for study, systems in s.items():
                key = (f"{labels[str(p)]}{LogParser.STUDY_SEP}{study}"
                       if study in colliding else study)
                origins[key] = {'path': str(p), 'study': study}
                studies.setdefault(key, [])
                for system in systems:
                    if system not in studies[key]:
                        studies[key].append(system)
                    src = fm.get(f"{study}|{system}", [])
                    if src:
                        file_map.setdefault(f"{key}|{system}", []).extend(src)

        return studies, warnings, file_map, origins

    @staticmethod
    def qualify_study(origins: Dict[str, dict], path: str, study: str):
        """Reverse lookup: (chip path, raw study) -> the current study key, or None."""
        for key, origin in (origins or {}).items():
            if origin.get('study') == study and LogParser.same_path(origin.get('path'), path):
                return key
        return None

    @staticmethod
    def peek_columns(logfile_path: Path) -> List[str]:
        """
        Reads the first chunk of the file to quickly extract column names.
        Avoids parsing the entire dataset. 
        """
        try:
            # Read first 100KB (increased from 10KB) to handle long preambles
            with open(logfile_path, 'r') as f:
                chunk = f.read(102400) 
                
            header_regex = re.compile(r'^\s*Step\s+', re.IGNORECASE)
            columns = set()
            
            for line in chunk.splitlines():
                if header_regex.match(line):
                    cols = line.strip().split()
                    columns.update(cols)
                    
            col_list = sorted(list(columns))
            # Normalize 'Step' casing for list placement
            step_variants = [c for c in col_list if c.lower() == 'step']
            if step_variants:
                for v in step_variants: col_list.remove(v)
                col_list.insert(0, step_variants[0])
                
            return col_list
        except Exception:
            return []

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
    def parse_multiple_logs(log_files: List[Path]) -> Optional[pd.DataFrame]:
        """
        Parses multiple LAMMPS log files, concatenates them, and removes duplicates.
        """
        all_dfs = []
        for log_file in log_files:
            df = LogParser.extract_thermo_data(log_file)
            if df is not None and not df.empty:
                all_dfs.append(df)

        if not all_dfs:
            return None

        combined_df = pd.concat(all_dfs, ignore_index=True)
        
        # Final deduplication across all concatenated files
        if 'Step' in combined_df.columns:
            combined_df.drop_duplicates(subset='Step', keep='last', inplace=True)
            combined_df.sort_values(by='Step', inplace=True)
            combined_df.reset_index(drop=True, inplace=True)

        return combined_df

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