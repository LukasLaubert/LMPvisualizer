# lmp_visualizer/lammps_parser.py

import re
import os
import pandas as pd
from pathlib import Path
from io import StringIO
from typing import Dict, List, Tuple, Optional
from lmpvisualizer.shared.logger_setup import get_logger

logger = get_logger(__name__)

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
            # Leading dot = suffix (".out"), trailing dot = prefix ("in."),
            # otherwise the keyword matches anywhere in the file name.
            if kw.startswith("."):
                pattern = f"*{kw}"
            elif kw.endswith("."):
                pattern = f"{kw}*"
            else:
                pattern = f"*{kw}*"
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

        # --- Virtual wildcard studies ----------------------------------------
        # Build one bold entry per digit-wildcard pattern (≥2 members) exactly
        # like the v0.6.3 system-switch matcher, but on the joint Study/System
        # string. Nothing else changes: real studies stay, virtual ones are added.
        try:
            virt_studies, virt_fm = LogParser._group_virtual_studies(studies, file_map)
            for pat, sys_list in virt_studies.items():
                if pat not in studies:
                    studies[pat] = sys_list
            for k, v in virt_fm.items():
                if k not in file_map:
                    file_map[k] = v
        except Exception as e:
            logger.warning("[System] Virtual study grouping failed: %s", e)

        return studies, warnings, file_map, origins

    # --- Wildcard virtual studies -----------------------------------------------
    # Joint Study/System strings are collapsed by digit groups (each maximal digit
    # run → one "*") exactly like the v0.6.3 system-switch fallback
    # (_system_pattern_tokenize). Every joint that shares a wildcard pattern is
    # grouped; each pattern with ≥2 members becomes a bold virtual study whose
    # name is the pattern itself (e.g. Silica_cmBut_*pb_*_*/output_cm_*).
    # Its system list is the union of the literal system names in the group and
    # its file_map entries aggregate the real files per literal.

    @staticmethod
    def _wildcard_pattern(text: str) -> str:
        """Replace each maximal digit run with a single '*'."""
        out = []
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if ch.isdigit():
                out.append("*")
                i += 1
                while i < n and text[i].isdigit():
                    i += 1
            else:
                out.append(ch)
                i += 1
        return "".join(out)

    @staticmethod
    def _is_virtual_study_key(name: str) -> bool:
        return "*" in name and "/" in name

    @staticmethod
    def display_study_for_virtual(study: str, system: str) -> str:
        """Plot label helper: for virtual Study/System the part after '/' is
        the system wildcard and is replaced by the chosen literal System.
        Keeps wildcard for synthetic 'average' systems."""
        if not study or not system:
            return study
        if not LogParser._is_virtual_study_key(study):
            return study
        if system in ("average", "average & std", "Strain average"):
            return study
        if system.startswith("Select "):
            return study
        if "/" not in study:
            return study
        # Qualified: 'label › Study_*/System_*' -> keep label literal, replace only tail
        return f"{study.rsplit('/', 1)[0]}/{system}"

    @staticmethod
    def _group_virtual_studies(studies: Dict[str, List[str]],
                               file_map: Dict[str, List[Path]]):
        """Return (virtual_studies, virtual_file_map) for the current discovery."""
        # Build joints from real studies only (skip already-virtual to avoid recursion)
        # For qualified studies (label › raw) keep the label literal - only the
        # raw folder names participate in the digit-wildcard matching.
        joints = []  # (study, system, joint, pattern)
        for study, systems in list(studies.items()):
            if LogParser._is_virtual_study_key(study):
                continue
            for system in systems:
                if "*" in system:
                    continue
                if LogParser.STUDY_SEP in study:
                    label, raw = study.split(LogParser.STUDY_SEP, 1)
                    pat = f"{label}{LogParser.STUDY_SEP}{LogParser._wildcard_pattern(raw)}/{LogParser._wildcard_pattern(system)}"
                else:
                    pat = f"{LogParser._wildcard_pattern(study)}/{LogParser._wildcard_pattern(system)}"
                if "*" not in pat:
                    continue
                joints.append((study, system, f"{study}/{system}", pat))

        if not joints:
            return {}, {}

        from collections import defaultdict
        groups: Dict[str, list] = defaultdict(list)
        for entry in joints:
            groups[entry[3]].append(entry)

        virtual_studies: Dict[str, List[str]] = {}
        virtual_file_map: Dict[str, List[Path]] = {}
        for pat, members in groups.items():
            if len(members) < 2:
                continue
            if pat in studies or pat in virtual_studies:
                continue
            # Distinct literal system names that belong to this pattern
            sys_union = sorted({m[1] for m in members})
            if not sys_union:
                continue
            virtual_studies[pat] = sys_union
            # Aggregate files per literal system
            agg: Dict[str, List[Path]] = defaultdict(list)
            for study, system, _joint, _pat in members:
                key = f"{study}|{system}"
                src = file_map.get(key, [])
                if src:
                    agg[system].extend(src)
            for sys_lit, files in agg.items():
                # de-duplicate, keep sorted for determinism
                uniq = sorted({str(p): p for p in files}.values(), key=lambda p: str(p))
                virtual_file_map[f"{pat}|{sys_lit}"] = uniq

        return virtual_studies, virtual_file_map

    @staticmethod
    def qualify_study(origins: Dict[str, dict], path: str, study: str):
        """Reverse lookup: (chip path, raw study) -> the current study key, or None."""
        for key, origin in (origins or {}).items():
            if origin.get('study') == study and LogParser.same_path(origin.get('path'), path):
                return key
        return None

    # --- Stable row identity ---------------------------------------------------
    #
    # A table row is keyed by what it shows (study key, system, display name) and
    # by where it sits (row index) - neither survives a reload: study keys gain
    # or lose their STUDY_SEP prefix as chips come and go, and row indices shift
    # on every insert/remove. The stable identity of a row is therefore the
    # (canonical source path, raw study folder, system/effective display) triple:
    # the source path names the chip the row came from, the raw study folder is
    # the name discovery reported before qualification, and the system/display
    # travels inside the row's own state. Rows are re-pointed at the current
    # study keys via origins on every load; only rows whose chip is gone are
    # dropped. Virtual wildcard studies ("*") and the synthetic "average" /
    # "average & std" systems have no single origin and are matched by key.

    @staticmethod
    def stable_row_identity(source_path: str, study_raw: str) -> dict:
        """Canonical (source path, raw study) pair stored on every table row."""
        return {'path': LogParser.canonical_path(source_path), 'study': study_raw or ''}

    @staticmethod
    def repoint_study_key(origins: Dict[str, dict], source_path: str, study_raw: str):
        """Current study key for a stored row identity, or None if it is gone."""
        if source_path is None or study_raw is None:
            return None
        return LogParser.qualify_study(
            origins, LogParser.canonical_path(source_path), study_raw)

    @staticmethod
    def path_is_loaded(source_path: str, paths) -> bool:
        """True when a row's source path still has its chip loaded.

        Rows without a stored path (legacy single-path sessions) count as
        loaded; they adopt the origin of their study key during re-pointing.
        """
        if source_path is None:
            return True
        return any(LogParser.same_path(source_path, p) for p in (paths or []))

    @staticmethod
    def peek_columns(logfile_path: Path) -> List[str]:
        """
        Reads the first chunk of the file to quickly extract column names.
        Avoids parsing the entire dataset.
        The chunk may end mid-line (header is 662 chars); a cut header would
        contribute a truncated token like 'v_c'/'v_cauchy' which then appeared
        as fake axis entries ('v_c', 'v_cau', ...). Extend to the next newline
        so the last header is complete before splitting.
        """
        try:
            with open(logfile_path, 'r', errors='ignore') as f:
                chunk = f.read(102400)
                # If the chunk ends inside a line, complete that line so a
                # header is never split (otherwise the tail token is truncated).
                if chunk and chunk[-1] != '\n':
                    rest = f.readline()
                    if rest:
                        chunk += rest

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
    def _collect_header_groups(log_files: List[Path]) -> Dict[Tuple[str, ...], Dict]:
        """Lightweight scan: distinct header -> {cols, files, rows, header_str}."""
        header_regex = re.compile(r'^\s*Step\s+')
        end_block_regex = re.compile(r'^\s*Loop time of')
        groups: Dict[Tuple[str, ...], Dict] = {}
        # De-duplicate input list
        seen = set()
        uniq_files = []
        for p in log_files or []:
            try:
                key = str(Path(p).resolve())
            except Exception:
                key = str(p)
            if key not in seen:
                seen.add(key)
                uniq_files.append(Path(p))
        for fpath in uniq_files:
            try:
                with open(fpath, 'r', errors='ignore') as f:
                    lines = f.readlines()
            except Exception:
                continue
            cur = None
            in_block = False
            for line in lines:
                if header_regex.match(line):
                    cur = tuple(line.strip().split())
                    if cur not in groups:
                        groups[cur] = {'cols': list(cur), 'header_str': line.strip(), 'files': set(), 'rows': 0}
                    groups[cur]['files'].add(str(fpath))
                    in_block = True
                    continue
                if in_block:
                    if end_block_regex.match(line):
                        in_block = False
                        cur = None
                    elif re.match(r'^\s*[-0-9]', line):
                        if cur is not None:
                            groups[cur]['rows'] += 1
        # Convert file sets to counts for UI
        for g in groups.values():
            g['file_count'] = len(g['files'])
            # Keep set for internal use as well
        return groups

    @staticmethod
    def get_header_groups_for_filemap(file_map: Dict[str, List[Path]]) -> Dict[Tuple[str, ...], Dict]:
        """Convenience: collect header groups across all paths in a file_map."""
        all_files: List[Path] = []
        for v in (file_map or {}).values():
            all_files.extend(v)
        return LogParser._collect_header_groups(all_files)

    @staticmethod
    def get_files_with_multiple_headers(file_map: Dict[str, List[Path]]):
        """Returns batches where a single file has >1 header.
        Groups files by their internal header set (frozenset of header tuples).
        Each batch = {'header_set': frozenset, 'files': [...], 'groups': {header: aggregated info}}.
        Only files with >1 distinct header are included; single-header files are ignored.
        """
        # First, per-file header map
        per_file = {}  # file_str -> set of headers
        per_file_groups = {}  # file_str -> groups dict for that file
        for paths in (file_map or {}).values():
            for p in paths or []:
                try:
                    fstr = str(Path(p).resolve())
                except Exception:
                    fstr = str(p)
                if fstr in per_file:
                    continue
                groups = LogParser._collect_header_groups([Path(p)])
                # groups keys are the headers found in that file
                per_file[fstr] = set(groups.keys())
                per_file_groups[fstr] = groups

        # Keep only files with >1 header
        multi_files = {f: hdrs for f, hdrs in per_file.items() if len(hdrs) > 1}
        if not multi_files:
            return []

        # Group by header set
        batches: Dict[frozenset, Dict] = {}
        for fstr, hdr_set in multi_files.items():
            key = frozenset(hdr_set)
            if key not in batches:
                batches[key] = {'header_set': key, 'files': [], 'groups': {}}
            batches[key]['files'].append(fstr)

        # Aggregate groups per batch (sum rows, union files per header)
        for key, batch in batches.items():
            agg: Dict[Tuple[str, ...], Dict] = {}
            for fstr in batch['files']:
                groups = per_file_groups[fstr]
                for hdr, info in groups.items():
                    if hdr not in agg:
                        agg[hdr] = {'cols': info['cols'], 'header_str': info['header_str'], 'files': set(), 'rows': 0}
                    agg[hdr]['files'].add(fstr)
                    agg[hdr]['rows'] += info['rows']
            for g in agg.values():
                g['file_count'] = len(g['files'])
            batch['groups'] = agg

        # Return list sorted for determinism (largest batch first)
        return sorted(batches.values(), key=lambda b: (-len(b['files']), -sum(g['rows'] for g in b['groups'].values())))



    @staticmethod
    def extract_thermo_data(logfile_path: Path, allowed_headers: Optional[set] = None) -> Optional[pd.DataFrame]:
        """
        Efficiently extracts thermo data from a LAMMPS log file into a pandas DataFrame.
        Handles log files with multiple data sections and multiple header types.
        Groups by distinct header; concat with outer join. If allowed_headers is set,
        only those header tuples are included (selected via header dialog).
        """
        try:
            with open(logfile_path, 'r') as f:
                lines = f.readlines()
        except Exception:
            return None

        groups: Dict[Tuple[str, ...], List[str]] = {}
        cur_header: Optional[Tuple[str, ...]] = None
        in_data_block = False

        header_regex = re.compile(r'^\s*Step\s+')
        end_block_regex = re.compile(r'^\s*Loop time of')

        for line in lines:
            if header_regex.match(line):
                cur_header = tuple(line.strip().split())
                if allowed_headers is not None and cur_header not in allowed_headers:
                    # Keep tracking but don't create a group for disallowed headers
                    # We still need to enter block to skip its data
                    in_data_block = True
                    continue
                if cur_header not in groups:
                    groups[cur_header] = []
                in_data_block = True
                continue

            if in_data_block:
                if end_block_regex.match(line):
                    in_data_block = False
                    cur_header = None
                else:
                    if re.match(r'^\s*[-0-9]', line):
                        if cur_header is None:
                            continue
                        if allowed_headers is not None and cur_header not in allowed_headers:
                            continue
                        # Only append if group exists (allowed)
                        if cur_header in groups:
                            groups[cur_header].append(line)

        if not groups:
            return None
        # Remove empty groups (header seen but no data)
        groups = {k: v for k, v in groups.items() if v}
        if not groups:
            return None

        # Fast path: single header -> single read_csv (identical speed to before)
        if len(groups) == 1:
            header, data_lines = next(iter(groups.items()))
            column_names = list(header)
            data_io = StringIO(''.join(data_lines))
            try:
                df = pd.read_csv(data_io, sep=r'\s+', names=column_names, engine='python')
                df.drop_duplicates(subset='Step', keep='last', inplace=True)
                for col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                df.dropna(inplace=True)
                return df
            except Exception:
                return None

        # General path: one DataFrame per distinct header, then outer concat.
        dfs = []
        for header, data_lines in groups.items():
            column_names = list(header)
            data_io = StringIO(''.join(data_lines))
            try:
                df = pd.read_csv(data_io, sep=r'\s+', names=column_names, engine='python')
                # Per-block cleanup before concat - drop only rows that failed
                # numeric conversion for its own columns (not global NaNs from outer join)
                for col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                df.dropna(inplace=True)
                if not df.empty:
                    dfs.append(df)
            except Exception:
                continue
        if not dfs:
            return None
        try:
            combined = pd.concat(dfs, ignore_index=True, sort=False)
            if 'Step' in combined.columns:
                combined.drop_duplicates(subset='Step', keep='last', inplace=True)
                combined.sort_values(by='Step', inplace=True)
                combined.reset_index(drop=True, inplace=True)
            # After outer concat, rows from one header have NaN for the other's columns;
            # only require Step to be valid - other NaNs are kept so X/Y from same header still plots.
            if 'Step' in combined.columns:
                combined = combined.dropna(subset=['Step'])
                # Ensure numeric for any columns that may have been upcast via concat
                for col in combined.columns:
                    if combined[col].dtype == object:
                        combined[col] = pd.to_numeric(combined[col], errors='coerce')
            return combined
        except Exception:
            return None

    @staticmethod
    def parse_multiple_logs(log_files: List[Path], allowed_headers: Optional[set] = None) -> Optional[pd.DataFrame]:
        """
        Parses multiple LAMMPS log files, concatenates them, and removes duplicates.
        If allowed_headers is set, only those header types are included.
        """
        all_dfs = []
        for log_file in log_files:
            df = LogParser.extract_thermo_data(log_file, allowed_headers=allowed_headers)
            if df is not None and not df.empty:
                all_dfs.append(df)

        if not all_dfs:
            return None

        combined_df = pd.concat(all_dfs, ignore_index=True, sort=False)
        
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