# lmp_visualizer/dsd_data_manager.py

import numpy as np
import pandas as pd
import hashlib
import json
from io import StringIO
from typing import Dict, List, Tuple, Optional, Set
from pathlib import Path
from lmpvisualizer.shared.trajectory_parser import TrajectoryParser
from lmpvisualizer.shared.logger_setup import get_logger

logger = get_logger(__name__)


# --- Centralized DSD result identity (single source of truth) ---
#
# The persistent `.idx` results_library hash (get_domain_hash) and the
# controller's RAM `_strain_cache_key` (build_strain_cache_key) — plus the
# data-manager's own `strain_evolution_cache` mem id — must cover exactly the
# same result-affecting fields. Visual-only props
# (color, style/strain_style, size/strain_size, show/strain_show, active,
# is_optimal_line and other underscore-private helpers) are allow-listed OUT
# and never affect the identity.

_DSD_UNSET = object()


def _dsd_canon_z_col(value):
    if value is None:
        return None
    if isinstance(value, str) and (value == "No Z-Filter" or value == ""):
        return None
    return value


def canonicalize_dsd_domain(domain):
    """Canonical domain part of the DSD result identity (visuals excluded)."""
    d = domain or {}
    number_boxes = d.get('number_boxes', d.get('num_boxes', 10))
    box_arrangement = d.get('box_arrangement', d.get('arrangement', 'inside'))
    overlap = d.get('overlap_percentage', d.get('overlap', 0.0))
    try:
        splits = sorted(float(s) for s in (d.get('splits') or []))
    except Exception:
        splits = list(d.get('splits') or [])
    act = d.get('active_segments', [])
    try:
        active_segments = [bool(x) for x in act] if act else []
    except Exception:
        active_segments = list(act) if act else []
    try:
        atom_types = sorted(d.get('atom_types', []) or [])
    except Exception:
        try:
            atom_types = list(d.get('atom_types', []) or [])
        except Exception:
            atom_types = []
    be = d.get('box_edges')
    if isinstance(be, (list, tuple)) and len(be) == 2:
        def _c(v):
            return None if v is None else float(v)
        try:
            box_edges = [_c(be[0]), _c(be[1])]
        except Exception:
            box_edges = [be[0], be[1]]
    elif be is None:
        box_edges = None
    else:
        try:
            f = float(be)
            box_edges = None if f == float('inf') else f
        except Exception:
            box_edges = be
    return {
        'name': d.get('name'),
        'splits': splits,
        'active_segments': active_segments,
        'atom_types': atom_types,
        'box_edges': box_edges,
        'pbc': bool(d.get('pbc', False)),
        'weighted': bool(d.get('weighted', False)),
        'bary_mid_twoside_weight': bool(d.get('bary_mid_twoside_weight', False)),
        'box_arrangement': box_arrangement,
        'number_boxes': number_boxes,
        'overlap_percentage': overlap,
        'weighting_shape': d.get('weighting_shape', 1.0),
        'fit_outer_box': bool(d.get('fit_outer_box', False)),
    }


def canonicalize_dsd_reference(slice_axis, observe_axis, options, z_ranges=_DSD_UNSET, timesteps=None):
    """Canonical reference-context part of the DSD result identity."""
    opts = options or {}
    if z_ranges is _DSD_UNSET:
        z_ranges = opts.get('z_ranges')
    def _ax(a):
        return a.lower() if isinstance(a, str) else a
    z_col = _dsd_canon_z_col(opts.get('z_filter_col'))
    z_ref = opts.get('z_filter_ref')
    if z_col is None:
        z_ref = None
        z_ranges_c = None
    else:
        if z_ranges:
            try:
                z_ranges_c = sorted([[float(lo), float(hi)] for (lo, hi) in z_ranges])
            except Exception:
                try:
                    z_ranges_c = sorted(list(z_ranges))
                except Exception:
                    z_ranges_c = list(z_ranges)
        else:
            z_ranges_c = None
    try:
        initial_step = timesteps[0] if timesteps else None
    except Exception:
        initial_step = None
    try:
        initial_step = int(initial_step) if initial_step is not None else None
    except Exception:
        pass
    disp_std = opts.get('disp_std', True)
    strain_std = opts.get('strain_std', False)
    total_count = opts.get('total_count', False)
    weighted_count = opts.get('weighted_count', False)
    return {
        'slice_axis': _ax(slice_axis),
        'observe_axis': _ax(observe_axis),
        'z_filter_col': z_col,
        'z_filter_ref': z_ref,
        'z_ranges': z_ranges_c,
        'initial_step': initial_step,
        'disp_std': bool(disp_std) if disp_std is not None else None,
        'strain_std': bool(strain_std) if strain_std is not None else None,
        'total_count': bool(total_count) if total_count is not None else None,
        'weighted_count': bool(weighted_count) if weighted_count is not None else None,
    }


def build_dsd_result_identity(domain, timesteps, slice_axis, observe_axis, options=None, z_ranges=_DSD_UNSET):
    """Build the canonical identity dict shared by hash and cache key."""
    return {
        'domain': canonicalize_dsd_domain(domain),
        'reference': canonicalize_dsd_reference(slice_axis, observe_axis, options, z_ranges, timesteps),
    }


def hash_dsd_result_identity(identity):
    """Hash a canonical identity dict (deterministic, JSON-sorted)."""
    id_json = json.dumps(identity, sort_keys=True, default=str)
    return hashlib.sha256(id_json.encode()).hexdigest()


class DSDDataManager:
    def __init__(self):
        # Storage: {study_name: {system_name: TrajectoryParser or Path}}
        self.parsers: Dict[str, Dict[str, any]] = {}
        self.current_study = None
        self.current_system = None
        self.cache = {} 
        self.slicing_results = {} 
        self.strain_evolution_cache = {} # Key: (Study, System, DomainIdentity) -> {strains, stds}

    def get_domain_hash(self, v_domain, timesteps, slice_axis, observe_axis, options, z_ranges=_DSD_UNSET):
        """Persistent hash for a domain definition and its reference context.

        Centralized: builds the canonical identity via build_dsd_result_identity
        (same fields the controller cache key uses). Visual-only props
        (color, style/size/show and strain_* variants) are excluded.
        `z_ranges` may be passed explicitly (controller path) or left unset to
        fall back to `options['z_ranges']` (legacy/data-manager path).
        """
        identity = build_dsd_result_identity(v_domain, timesteps, slice_axis, observe_axis, options, z_ranges)
        return hash_dsd_result_identity(identity)

    def build_strain_cache_key(self, domains, timesteps, slice_axis, observe_axis, options, z_ranges=_DSD_UNSET,
                               study=None, system=None):
        """RAM cache key covering exactly the same fields as the persistent hash.

        Expands splits into virtual segments through get_required_hashes, so the
        key is composed of the very hashes stored in the `.idx` library, plus
        the location (study/system) and the full active range (the persistent
        hash pins only the initial step; ts_map merging serves range subsets).
        """
        hashes = tuple(sorted(self.get_required_hashes(domains, timesteps, slice_axis, observe_axis, options, z_ranges)))
        try:
            range_tuple = tuple(timesteps) if timesteps else ()
        except Exception:
            range_tuple = ()
        return (hashes, study, system, range_tuple)

    def load_project_data(self, studies: Dict[str, List[str]], root_path: Path, keywords: List[str], file_map: Dict[str, List[Path]] = None):
        """Discovers files but defers parser creation until needed (Lazy Loading)."""
        self.parsers.clear()
        self.cache.clear()
        self.strain_evolution_cache.clear()
        # Keyed by id() of the frame DataFrames, which self.cache just dropped - a
        # recycled id would otherwise serve another project's slice results.
        self.slicing_results.clear()
        warnings = []
        successful_keywords = set()

        if file_map is None: file_map = {}

        try:
            from lmpvisualizer.log.log_parser import LogParser as _LP
            _is_virtual = _LP._is_virtual_study_key
            _wildcard = _LP._wildcard_pattern
        except Exception:
            _is_virtual = lambda x: "*" in x and "/" in x
            _wildcard = lambda x: x
        # TRJ/DSD displayed System is the file stem when several files share one
        # folder, so folder-based virtual from LogParser would be wrong. Build only
        # from real studies and recompute virtual from the actual stem list.
        real_studies = {k: v for k, v in studies.items() if not _is_virtual(k)}

        for study_name, system_list in real_studies.items():
            self.parsers[study_name] = {}
            for system_name in system_list:
                key = f"{study_name}|{system_name}"
                target_files = file_map.get(key, [])
                
                if not target_files:
                    # Backward compat scan. root_path may be several project paths.
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
                        if kw in fpath.name: successful_keywords.add(kw)

        # --- Virtual wildcard studies from actual displayed systems ---
        # For single-system studies the displayed System is the folder name, but the
        # actual file stem may be the correct token. Try file stem and use it only
        # if its wildcard matches another stem.
        try:
            from collections import defaultdict
            study_entries = {}
            all_stem_wildcards = set()
            for study, sys_dict in list(self.parsers.items()):
                lst = []
                for disp_sys, entry in sys_dict.items():
                    fpath = entry if isinstance(entry, Path) else getattr(entry, 'file_path', None)
                    if fpath is None:
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

            effective_joints = []
            for study, lst in study_entries.items():
                if len(lst) == 1:
                    disp_sys, file_stem, fpath = lst[0]
                    if disp_sys != file_stem:
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
            logger.warning("[System] DSD virtual grouping failed: %s", e)

        if not self.parsers:
             warnings.append("No valid trajectory files found.")

        return warnings, successful_keywords

    def get_parser(self, study, system) -> Optional[TrajectoryParser]:
        """Lazy-loads the parser for a specific system only when accessed."""
        if study not in self.parsers or system not in self.parsers[study]:
            return None
            
        entry = self.parsers[study][system]
        
        # If entry is a Path, promote it to a TrajectoryParser
        if isinstance(entry, Path):
            try:
                parser = TrajectoryParser(entry)
                self.parsers[study][system] = parser
                return parser
            except Exception as e:
                logger.warning("Error initializing parser for %s: %s", entry, e)
                return None
                
        return entry

    def get_timesteps(self, study, system):
        parser = self.get_parser(study, system)
        if parser: return parser.get_timesteps()
        return []

    def load_frame(self, study, system, timestep):
        cache_key = (study, system, timestep)
        if cache_key in self.cache:
            return self.cache[cache_key]
            
        parser = self.get_parser(study, system)
        if parser:
            df, box_bounds = parser.get_frame(timestep)
            if df is not None:
                 self.cache[cache_key] = (df, box_bounds)
            return df, box_bounds
        return None, []

    def clear_cache(self):
        self.cache = {}
        self.slicing_results = {}
        self.strain_evolution_cache = {}

    def slice_disp_mean(self, df_initial, df_curr, settings, slice_axis, observe_axis, box_curr, 
                        z_col=None, z_ref='Current', z_ranges=None, df_final=None, box_init=None):
        """Wrapper that handles domain splitting."""
        splits = settings.get('splits', [])
        active_segments = settings.get('active_segments', [])
        results = []
        
        if not splits:
            res = self._slice_disp_single_domain(df_initial, df_curr, settings, slice_axis, observe_axis, box_curr, 
                                               z_col, z_ref, z_ranges, df_final, box_init)
            if res is not None: results.append(res)
        else:
            sorted_splits = sorted(splits)
            segments = []
            segments.append((None, sorted_splits[0]))
            for i in range(len(sorted_splits) - 1):
                segments.append((sorted_splits[i], sorted_splits[i+1]))
            segments.append((sorted_splits[-1], None))
            
            for i, (seg_min, seg_max) in enumerate(segments):
                if i < len(active_segments) and not active_segments[i]: continue
                s_copy = settings.copy()
                s_copy['box_edges'] = (seg_min, seg_max)
                res = self._slice_disp_single_domain(df_initial, df_curr, s_copy, slice_axis, observe_axis, box_curr,
                                                   z_col, z_ref, z_ranges, df_final, box_init)
                if res is not None: results.append(res)
        return results

    def _get_slice_cache_key(self, df_initial, df_curr, settings, slice_axis, observe_axis, 
                             z_col, z_ref, z_ranges, df_final):
        relevant_keys = [
            'atom_types', 'box_edges', 'number_boxes', 'num_boxes', 
            'box_arrangement', 'arrangement', 'overlap_percentage', 'overlap',
            'pbc', 'weighting_shape', 'weighted', 'bary_mid_twoside_weight', 'fit_outer_box'
        ]
        settings_tuple = []
        for k in sorted(relevant_keys):
            val = settings.get(k)
            if isinstance(val, list): val = tuple(sorted(val)) if k == 'atom_types' else tuple(val)
            settings_tuple.append((k, val))
        z_ranges_tuple = tuple(sorted(z_ranges)) if z_ranges else None
        
        return (id(df_initial), id(df_curr), tuple(settings_tuple), slice_axis, observe_axis,
                z_col, z_ref, z_ranges_tuple, id(df_final) if df_final is not None else None)

    def _slice_disp_single_domain(self, df_initial, df_curr, settings, slice_axis, observe_axis, box_curr,
                        z_col=None, z_ref='Current', z_ranges=None, df_final=None, box_init=None):
        cache_key = self._get_slice_cache_key(df_initial, df_curr, settings, slice_axis, observe_axis, 
                                              z_col, z_ref, z_ranges, df_final)
        if cache_key in self.slicing_results:
            cached = self.slicing_results[cache_key]
            return cached.copy() if cached is not None else None

        atom_types = settings.get('atom_types', [])
        if atom_types:
            df_init_geo = df_initial[df_initial['type'].isin(atom_types)].copy()
            df_curr_geo = df_curr[df_curr['id'].isin(df_init_geo['id'])].copy()
            df_init_geo = df_init_geo.set_index('id').sort_index()
            df_curr_geo = df_curr_geo.set_index('id').sort_index()
            common_ids = df_init_geo.index.intersection(df_curr_geo.index)
            df_init_geo, df_curr_geo = df_init_geo.loc[common_ids], df_curr_geo.loc[common_ids]
        else:
            df_init_geo, df_curr_geo = df_initial.set_index('id').sort_index(), df_curr.set_index('id').sort_index()

        if df_init_geo.empty:
            self.slicing_results[cache_key] = None
            return None

        df_init_pop, df_curr_pop = df_init_geo, df_curr_geo
        if z_col and z_col != "No Z-Filter" and z_ranges:
            z_vals = None
            if z_ref == 'Initial':
                if z_col in df_init_geo.columns: z_vals = df_init_geo[z_col].values
            elif z_ref == 'Final' or (isinstance(z_ref, str) and z_ref.startswith("Step ")):
                if df_final is not None:
                     df_final_sub = df_final[df_final['id'].isin(df_init_geo.index)]
                     df_final_sub = df_final_sub.set_index('id').reindex(df_init_geo.index)
                     if z_col in df_final_sub.columns: z_vals = df_final_sub[z_col].values
            else: # Current
                if z_col in df_curr_geo.columns: z_vals = df_curr_geo[z_col].values
            
            if z_vals is not None:
                mask = np.zeros(len(z_vals), dtype=bool)
                for (z_min, z_max) in z_ranges: mask |= (z_vals >= z_min) & (z_vals <= z_max)
                df_init_pop, df_curr_pop = df_init_geo[mask], df_curr_geo[mask]

        if df_init_pop.empty:
            self.slicing_results[cache_key] = None
            return None

        pos_slice_init_pop = df_init_pop[slice_axis.lower()].values
        pos_slice_curr_pop = df_curr_pop[slice_axis.lower()].values
        
        box_edges = settings.get('box_edges', float('inf'))
        num_boxes = settings.get('number_boxes', settings.get('num_boxes', 10))
        arrangement = settings.get('box_arrangement', settings.get('arrangement', 'inside'))
        overlap_pct = settings.get('overlap_percentage', settings.get('overlap', 0.0))
        pbc_active = settings.get('pbc', False)
        use_weighted = settings.get('weighted', False)
        weight_shape = settings.get('weighting_shape', 1.0)
        fit_outer = settings.get('fit_outer_box', False)
        bary_mode = settings.get('bary_mid_twoside_weight', False)

        def get_box_params(box):
            if not box: return {}
            if len(box) >= 4: xy, xz, yz = box[3][0], box[3][1], box[3][2]
            else: xy, xz, yz = (box[0][2] if len(box[0]) > 2 else 0.0), (box[1][2] if len(box[1]) > 2 else 0.0), (box[2][2] if len(box[2]) > 2 else 0.0)
            xb, yb, zb = box[0], box[1], box[2]
            lz, zlo = zb[1] - zb[0], zb[0]
            ly, ylo = (yb[1] - yb[0]) - abs(yz), yb[0] - min(0.0, yz)
            mx, gx = min(0.0, xy, xz, xy+xz), max(0.0, xy, xz, xy+xz)
            lx, xlo = (xb[1] - xb[0]) - (gx - mx), xb[0] - mx
            return {'lx':lx, 'ly':ly, 'lz':lz, 'xy':xy, 'xz':xz, 'yz':yz, 'xlo':xlo, 'ylo':ylo, 'zlo':zlo}

        pi, pc = get_box_params(box_init), get_box_params(box_curr)
        s_ax = slice_axis.lower()
        if s_ax == 'x': shift_vec_i, shift_vec_c = np.array([pi['lx'], 0.0, 0.0]), np.array([pc['lx'], 0.0, 0.0])
        elif s_ax == 'y': shift_vec_i, shift_vec_c = np.array([pi['xy'], pi['ly'], 0.0]), np.array([pc['xy'], pc['ly'], 0.0])
        else: shift_vec_i, shift_vec_c = np.array([pi['xz'], pi['yz'], pi['lz']]), np.array([pc['xz'], pc['yz'], pc['lz']])

        if isinstance(box_edges, (list, tuple)) and len(box_edges) == 2:
            b_min, b_max = box_edges
            if b_min is None: b_min = np.min(pos_slice_init_pop)
            if b_max is None: b_max = np.max(pos_slice_init_pop)
        else: b_min, b_max = np.min(pos_slice_init_pop), np.max(pos_slice_init_pop)

        L_total = b_max - b_min
        if arrangement == 'protrude':
            L_box_no_ov = L_total / num_boxes if num_boxes > 1 else L_total
            b_min -= L_box_no_ov / 2
            b_max += L_box_no_ov / 2
            L_total = b_max - b_min
        
        L_box_no_ov = L_total / num_boxes
        L_box = L_box_no_ov * (1 + overlap_pct / 100.0)
        
        results = []
        for k in range(num_boxes):
            center = b_min + (k + 0.5) * L_box_no_ov
            box_min, box_max = center - L_box/2, center + L_box/2
            mask_main = (pos_slice_init_pop >= box_min) & (pos_slice_init_pop <= box_max)
            if not np.any(mask_main): continue

            list_xi, list_yi, list_zi = [df_init_pop['x'].values[mask_main]], [df_init_pop['y'].values[mask_main]], [df_init_pop['z'].values[mask_main]]
            list_xc, list_yc, list_zc = [df_curr_pop['x'].values[mask_main]], [df_curr_pop['y'].values[mask_main]], [df_curr_pop['z'].values[mask_main]]
            list_slice_for_binning, list_slice_curr = [pos_slice_init_pop[mask_main]], [pos_slice_curr_pop[mask_main]]

            if pbc_active:
                # One target list per axis: every ghost contribution must land in the
                # list of the axis it belongs to, otherwise x grows three times faster
                # than y/z and the later per-atom arithmetic cannot broadcast.
                lists_i, lists_c = (list_xi, list_yi, list_zi), (list_xc, list_yc, list_zc)

                L_slice_init = pi.get(f'l{s_ax}', 1.0)
                ghost_pos_L = pos_slice_init_pop - L_slice_init
                mask_gL = (ghost_pos_L >= box_min) & (ghost_pos_L <= box_max)
                if np.any(mask_gL):
                    list_slice_for_binning.append(ghost_pos_L[mask_gL])
                    s_idx = {'x':0, 'y':1, 'z':2}[s_ax]
                    list_slice_curr.append(pos_slice_curr_pop[mask_gL] - shift_vec_c[s_idx])
                    for i, axis in enumerate(['x','y','z']):
                        lists_i[i].append(df_init_pop[axis].values[mask_gL] - shift_vec_i[i])
                        lists_c[i].append(df_curr_pop[axis].values[mask_gL] - shift_vec_c[i])

                ghost_pos_R = pos_slice_init_pop + L_slice_init
                mask_gR = (ghost_pos_R >= box_min) & (ghost_pos_R <= box_max)
                if np.any(mask_gR):
                    list_slice_for_binning.append(ghost_pos_R[mask_gR])
                    s_idx = {'x':0, 'y':1, 'z':2}[s_ax]
                    list_slice_curr.append(pos_slice_curr_pop[mask_gR] + shift_vec_c[s_idx])
                    for i, axis in enumerate(['x','y','z']):
                        lists_i[i].append(df_init_pop[axis].values[mask_gR] + shift_vec_i[i])
                        lists_c[i].append(df_curr_pop[axis].values[mask_gR] + shift_vec_c[i])

            all_xi, all_yi, all_zi = np.concatenate(list_xi), np.concatenate(list_yi), np.concatenate(list_zi)
            all_xc, all_yc, all_zc = np.concatenate(list_xc), np.concatenate(list_yc), np.concatenate(list_zc)
            p_pos_for_w, p_pos_curr_for_cross = np.concatenate(list_slice_for_binning), np.concatenate(list_slice_curr)
            
            obs_map_i, obs_map_c = {'x':all_xi, 'y':all_yi, 'z':all_zi}, {'x':all_xc, 'y':all_yc, 'z':all_zc}
            p_init, p_curr = obs_map_i[observe_axis.lower()], obs_map_c[observe_axis.lower()]
            p_disp = p_curr - p_init
            
            o_ax = observe_axis.lower()
            L_obs_curr = pc.get(f'l{o_ax}', 1.0)
            if pbc_active and L_obs_curr > 0:
                s_idx, o_idx = {'x':0, 'y':1, 'z':2}[s_ax], {'x':0, 'y':1, 'z':2}[o_ax]
                L_s_curr_val, tilt_curr = shift_vec_c[s_idx], shift_vec_c[o_idx]
                if L_s_curr_val > 0:
                    crossings = np.round((p_pos_curr_for_cross - p_pos_for_w) / L_s_curr_val)
                    if np.any(crossings != 0) and tilt_curr != 0: p_disp -= crossings * tilt_curr
                w_rel = (all_zi - pi['zlo']) / pi['lz']
                v_rel = (all_yi - (pi['ylo'] + w_rel * pi['yz'])) / pi['ly']
                u_rel = (all_xi - (pi['xlo'] + v_rel * pi['xy'] + w_rel * pi['xz'])) / pi['lx']
                d_bias = (u_rel * (pc['lx']-pi['lx']) + v_rel * (pc['xy']-pi['xy']) + w_rel * (pc['xz']-pi['xz'])) if o_ax == 'x' else (v_rel * (pc['ly']-pi['ly']) + w_rel * (pc['yz']-pi['yz'])) if o_ax == 'y' else (w_rel * (pc['lz']-pi['lz']))
                p_disp -= L_obs_curr * np.round((p_disp - d_bias) / L_obs_curr)

            eff_center = (np.min(pos_slice_init_pop[mask_main]) + np.max(pos_slice_init_pop[mask_main])) / 2 if (fit_outer and np.any(mask_main)) else center
            if not bary_mode:
                sigma = (box_max - box_min) / 5.0
                w = np.exp(-((p_pos_for_w - eff_center)**2)/(2*sigma**2)) ** weight_shape if sigma > 0 else np.ones_like(p_init)
            else:
                bary_center = np.mean(p_pos_for_w)
                mask_left, mask_right = p_pos_for_w <= bary_center, p_pos_for_w > bary_center
                sigma_l, sigma_r = (bary_center - box_min)/2.5, (box_max - bary_center)/2.5
                w = np.zeros_like(p_pos_for_w)
                w[mask_left] = np.exp(-((p_pos_for_w[mask_left]-bary_center)**2)/(2*sigma_l**2))**weight_shape if sigma_l > 0 else 1.0
                w[mask_right] = np.exp(-((p_pos_for_w[mask_right]-bary_center)**2)/(2*sigma_r**2))**weight_shape if sigma_r > 0 else 1.0
                eff_center = bary_center
            
            if not use_weighted: w = np.ones_like(p_init)
            if (sum_w_total := np.sum(w)) == 0: continue
            n_main = len(list_slice_for_binning[0])
            
            if use_weighted:
                mean_disp = np.average(p_disp, weights=w)
                variance = np.average((p_disp - mean_disp)**2, weights=w)
            else:
                mean_disp, variance = np.mean(p_disp), np.var(p_disp)
                
            results.append({'center': eff_center, 'mean_disp': mean_disp, 'std_dev': np.sqrt(variance), 'count': n_main, 'sum_weights': np.sum(w[:n_main])})
        
        final_df = pd.DataFrame(results)
        self.slicing_results[cache_key] = final_df
        return final_df

    def get_required_hashes(self, domains, timesteps, slice_axis, observe_axis, options, z_ranges=_DSD_UNSET) -> List[str]:
        """Returns the list of all persistent hashes required for a full DSD preload."""
        if not timesteps: return []
        
        hashes = []
        for d_idx, domain in enumerate(domains):
            if domain.get('is_optimal_line'): continue
            
            # Replicate Virtual Domain Expansion logic
            splits = domain.get('splits', [])
            active_segments = domain.get('active_segments', [])
            
            if not splits:
                hashes.append(self.get_domain_hash(domain, timesteps, slice_axis, observe_axis, options, z_ranges))
            else:
                sorted_splits = sorted(splits)
                segments = [(None, sorted_splits[0])] + [(sorted_splits[i], sorted_splits[i+1]) for i in range(len(sorted_splits)-1)] + [(sorted_splits[-1], None)]
                
                # Ensure active_segments list is long enough
                act = list(active_segments)
                if len(act) < len(segments):
                    act.extend([True] * (len(segments) - len(act)))
                
                for i, (seg_min, seg_max) in enumerate(segments):
                    if act[i]:
                        d_seg = domain.copy()
                        d_seg['box_edges'] = (seg_min, seg_max)
                        hashes.append(self.get_domain_hash(d_seg, timesteps, slice_axis, observe_axis, options, z_ranges))
        return hashes

    def calculate_strain_evolution_cached(self, domains, timesteps, study, system, slice_axis, observe_axis, options, parser_override=None):
        """Pre-computes strain using a 3-tier cache: RAM -> Persistent .idx Library -> Combined Calculation."""
        parser = parser_override if parser_override else self.get_parser(study, system)
        if not parser or not timesteps: return None

        virtual_domains = []
        for d_idx, domain in enumerate(domains):
            if domain.get('is_optimal_line'): continue
            d_base = domain.copy()
            d_base['_source_index'], d_base['_parent_identity'] = d_idx, (domain.get('name'), tuple(domain.get('splits', [])), tuple(domain.get('active_segments', [])))
            splits, active_segments = domain.get('splits', []), domain.get('active_segments', [])
            if not splits: virtual_domains.append( (d_base, domain.get('name', f"Domain {d_idx}")) )
            else:
                sorted_splits = sorted(splits)
                segments = [(None, sorted_splits[0])] + [(sorted_splits[i], sorted_splits[i+1]) for i in range(len(sorted_splits)-1)] + [(sorted_splits[-1], None)]
                if len(active_segments) < len(segments): active_segments.extend([True]*(len(segments)-len(active_segments)))
                active_rank = 0
                for i, (seg_min, seg_max) in enumerate(segments):
                    if active_segments[i]:
                        active_rank += 1
                        d_new = d_base.copy()
                        d_new['box_edges'], d_new['_is_split'], d_new['_split_rank'] = (seg_min, seg_max), True, active_rank
                        base_name = domain.get('name', f"Domain {d_idx+1}")
                        virtual_domains.append( (d_new, f"{base_name}{active_rank}" if sum(active_segments) > 1 else base_name) )

        df_init, box_init = self.load_frame(study, system, timesteps[0])
        if df_init is None: return None
        any_domain_pbc = any(d.get('pbc', False) for d in domains if not d.get('is_optimal_line'))

        final_domains_data, domain_tasks, initial_step = [], [], timesteps[0]
        for i, (v_domain, name) in enumerate(virtual_domains):
            # RAM id is pinned to the persistent hash so both layers cover
            # exactly the same result-affecting fields (centralized identity).
            p_hash = self.get_domain_hash(v_domain, timesteps, slice_axis, observe_axis, options)
            mem_id = (study, system, p_hash)
            d_res = {
                'name': name, 'strains': [np.nan]*len(timesteps), 'stds': [0.0]*len(timesteps),
                'min_x': [np.nan]*len(timesteps), 'max_x': [np.nan]*len(timesteps), 
                'y_at_min_x': [np.nan]*len(timesteps), 'y_at_max_x': [np.nan]*len(timesteps),
                'min_y': [np.nan]*len(timesteps), 'max_y': [np.nan]*len(timesteps),
                'color': v_domain.get('color', 'blue'), 'style': v_domain.get('style', '-'),
                'source_index': v_domain.get('_source_index'),
                'parent_identity': v_domain.get('_parent_identity')
            }
            
            # 1. Try RAM Cache
            cached_mem = self.strain_evolution_cache.get(mem_id)
            # 2. Try Persistent Library
            lib_entry = parser.results_library.get(p_hash) if not cached_mem else None
            
            # Reference data source (Lib prioritized over fresh d_res)
            ref_data = cached_mem if cached_mem else (lib_entry['data'] if lib_entry else None)
            
            needs_calc = False
            if ref_data:
                # Map existing results to current timesteps
                for ts_idx, ts in enumerate(timesteps):
                    if 'ts_map' in ref_data and str(ts) in ref_data['ts_map']:
                        r_idx = ref_data['ts_map'][str(ts)]
                        for k in ['strains', 'stds', 'min_x', 'max_x', 'y_at_min_x', 'y_at_max_x', 'min_y', 'max_y']:
                            d_res[k][ts_idx] = ref_data[k][r_idx]
                    else:
                        needs_calc = True
            else:
                needs_calc = True

            if needs_calc:
                domain_tasks.append((i, v_domain, mem_id, p_hash))
            
            final_domains_data.append(d_res)

        if domain_tasks:
            # We only calculate the specific timesteps that are missing Nans
            for ts_idx, ts in enumerate(timesteps):
                # Optimization: check if ANY task actually needs this frame
                tasks_needing_ts = [t for t in domain_tasks if np.isnan(final_domains_data[t[0]]['strains'][ts_idx])]
                if not tasks_needing_ts: continue

                df_curr, box_curr = self.load_frame(study, system, ts)
                if df_curr is None: continue
                
                for res_idx, v_domain, _, _ in tasks_needing_ts:
                    res = self._slice_disp_single_domain(df_init, df_curr, v_domain, slice_axis, observe_axis, box_curr, z_col=options.get('z_filter_col'), z_ref=options.get('z_filter_ref'), z_ranges=options.get('z_ranges'), box_init=box_init)
                    mean_strain, std_strain, bx_min, bx_max, by_min, by_max = np.nan, 0.0, np.nan, np.nan, np.nan, np.nan
                    frame_min_y, frame_max_y = np.nan, np.nan

                    if res is not None and not res.empty:
                        centers, disps = res['center'].values, res['mean_disp'].values
                        frame_min_y, frame_max_y = np.min(disps), np.max(disps)
                        if 'std_dev' in res.columns:
                            stds = res['std_dev'].values
                            frame_min_y = min(frame_min_y, np.min(disps - stds))
                            frame_max_y = max(frame_max_y, np.max(disps + stds))

                        if len(centers) >= 2:
                            idxs = np.argsort(centers)
                            x_sorted, y_sorted = centers[idxs], disps[idxs]
                            bx_min, bx_max, by_min, by_max = x_sorted[0], x_sorted[-1], y_sorted[0], y_sorted[-1]
                            dx, dy = np.diff(x_sorted), np.diff(y_sorted)
                            with np.errstate(divide='ignore', invalid='ignore'): local_strains = dy / dx
                            valid_slopes = local_strains[dx > 0]
                            if len(valid_slopes) > 0:
                                mean_strain = np.mean(valid_slopes)
                                std_strain = np.std(valid_slopes, ddof=1) if len(valid_slopes) > 1 else 0.0
                            else: mean_strain, std_strain = 0.0, 0.0
                        else: mean_strain, std_strain = 0.0, 0.0
                    
                    final_domains_data[res_idx]['strains'][ts_idx] = mean_strain
                    final_domains_data[res_idx]['stds'][ts_idx] = std_strain
                    final_domains_data[res_idx]['min_x'][ts_idx] = bx_min
                    final_domains_data[res_idx]['max_x'][ts_idx] = bx_max
                    final_domains_data[res_idx]['y_at_min_x'][ts_idx] = by_min
                    final_domains_data[res_idx]['y_at_max_x'][ts_idx] = by_max
                    final_domains_data[res_idx]['min_y'][ts_idx] = frame_min_y
                    final_domains_data[res_idx]['max_y'][ts_idx] = frame_max_y

            # Update Caches
            for res_idx, _, mem_id, p_hash in domain_tasks:
                d_res = final_domains_data[res_idx]
                # Build ts_map for storage
                ts_map = {str(ts): i for i, ts in enumerate(timesteps)}
                res_data = {k: d_res[k] for k in ['strains', 'stds', 'min_x', 'max_x', 'y_at_min_x', 'y_at_max_x', 'min_y', 'max_y']}
                res_data['ts_map'] = ts_map
                
                # Merge with existing library data if available
                lib_entry = parser.results_library.get(p_hash)
                if lib_entry:
                    old_data = lib_entry['data']
                    old_map = old_data.get('ts_map', {})
                    # Add new points to old arrays
                    for ts_str, old_idx in old_map.items():
                        if ts_str not in ts_map:
                            for k in ['strains', 'stds', 'min_x', 'max_x', 'y_at_min_x', 'y_at_max_x', 'min_y', 'max_y']:
                                res_data[k].append(old_data[k][old_idx])
                            res_data['ts_map'][ts_str] = len(res_data['strains']) - 1
                
                self.strain_evolution_cache[mem_id] = res_data
                parser.store_library_entry(p_hash, res_data, label=d_res['name'])

        target_strains = []
        s_idx, o_idx = {'x':0,'y':1,'z':2}.get(slice_axis.lower()), {'x':0,'y':1,'z':2}.get(observe_axis.lower())
        for ts_idx, ts in enumerate(timesteps):
            if any_domain_pbc:
                _, box_curr = self.load_frame(study, system, ts)
                target_strains.append(self._calculate_target_strain(box_init, box_curr, s_idx, o_idx))
            else:
                g_min_x, g_max_x, val_at_min, val_at_max, found_data = np.inf, -np.inf, np.nan, np.nan, False
                for d_data in final_domains_data:
                    if ts_idx >= len(d_data['min_x']): continue
                    x_min, x_max, y_min, y_max = d_data['min_x'][ts_idx], d_data['max_x'][ts_idx], d_data['y_at_min_x'][ts_idx], d_data['y_at_max_x'][ts_idx]
                    if np.isnan(x_min) or np.isnan(x_max): continue
                    if x_min < g_min_x: g_min_x, val_at_min, found_data = x_min, y_min, True
                    if x_max > g_max_x: g_max_x, val_at_max, found_data = x_max, y_max, True
                target_strains.append((val_at_max - val_at_min) / (g_max_x - g_min_x) if (found_data and g_max_x > g_min_x) else 0.0)

        return {'timesteps': timesteps, 'target_strains': target_strains, 'domains': final_domains_data}

    def _calculate_target_strain(self, box_init, box_curr, s_idx, o_idx):
        if not box_init or not box_curr or s_idx is None or o_idx is None: return 0.0
        def get_box_params(box_data):
            xb, yb, zb = box_data[0], box_data[1], box_data[2]
            xy, xz, yz = (xb[2] if len(xb) > 2 else 0.0), (yb[2] if len(yb) > 2 else 0.0), (zb[2] if len(zb) > 2 else 0.0)
            zlo, lz = zb[0], zb[1] - zb[0]
            ylo, ly = yb[0] - min(0.0, yz), (yb[1] - yb[0]) - abs(yz)
            mins_x, maxs_x = min(0.0, xy, xz, xy+xz), max(0.0, xy, xz, xy+xz)
            xlo, lx = xb[0] - mins_x, (xb[1] - xb[0]) - (maxs_x - mins_x)
            return {'xlo': xlo, 'ylo': ylo, 'zlo': zlo, 'lx': max(1e-9, lx), 'ly': max(1e-9, ly), 'lz': max(1e-9, lz), 'xy': xy, 'xz': xz, 'yz': yz}
        p_i, p_c = get_box_params(box_init), get_box_params(box_curr)
        def calc_u(obj_idx, x_i, y_i, z_i):
            w0 = (z_i - p_i['zlo']) / p_i['lz']
            v0 = (y_i - (p_i['ylo'] + w0 * p_i['yz'])) / p_i['ly']
            u0 = (x_i - (p_i['xlo'] + v0 * p_i['xy'] + w0 * p_i['xz'])) / p_i['lx']
            return [p_c['xlo'] + u0 * p_c['lx'] + v0 * p_c['xy'] + w0 * p_c['xz'] - x_i, p_c['ylo'] + v0 * p_c['ly'] + w0 * p_c['yz'] - y_i, p_c['zlo'] + w0 * p_c['lz'] - z_i][obj_idx]
        c_i = [p_i['xlo'] + 0.5*p_i['lx'], p_i['ylo'] + 0.5*p_i['ly'], p_i['zlo'] + 0.5*p_i['lz']]
        x1, x2 = box_init[s_idx][0], box_init[s_idx][1]
        pos1, pos2 = list(c_i), list(c_i)
        pos1[s_idx], pos2[s_idx] = x1, x2
        return (calc_u(o_idx, *pos2) - calc_u(o_idx, *pos1)) / (x2 - x1) if x2 != x1 else 0.0

    def calculate_derivatives(self, df_results):
        """
        Calculates derivatives (strain) from the sliced displacement results.
        df_results: DataFrame with 'center', 'mean_disp'
        Returns: DataFrame with 'center', 'strain'
        """
        if df_results is None or len(df_results) < 2:
            return None
            
        centers = df_results['center'].values
        disps = df_results['mean_disp'].values
        
        d_disp = np.diff(disps)
        d_pos = np.diff(centers)
        
        # Avoid division by zero
        with np.errstate(divide='ignore', invalid='ignore'):
            strains = d_disp / d_pos
        
        strains[d_pos == 0] = np.nan
        
        strain_centers = (centers[:-1] + centers[1:]) / 2
        
        return pd.DataFrame({
            'center': strain_centers,
            'strain': strains
        })

    def calculate_mae(self, actual_strain, target_strain):
        """
        Computes Mean Absolute Error.
        """
        if actual_strain is None or len(actual_strain) == 0: return None
        
        diff = actual_strain - target_strain
        mae = np.mean(np.abs(diff))
        return mae

    def calculate_strain_average_evolution(self, domains, timesteps, study, slice_axis, observe_axis, options):
        """
        Calculates the average strain and standard deviation across all systems in a study,
        respecting the provided timestep range.
        """
        if study not in self.parsers:
            return None
            
        systems = list(self.parsers[study].keys())
        if len(systems) < 2:
            return None
            
        all_results = []
        
        # 1. Collect results for each system relative to the requested range
        for system in systems:
            full_tsteps = self.get_timesteps(study, system)
            if not full_tsteps:
                continue
            
            # Find the closest subset in this simulation that matches the requested range
            if timesteps:
                full_arr = np.array(full_tsteps)
                idx_min = (np.abs(full_arr - timesteps[0])).argmin()
                idx_max = (np.abs(full_arr - timesteps[-1])).argmin()
                if idx_min > idx_max: idx_min, idx_max = idx_max, idx_min
                target_range = full_tsteps[idx_min : idx_max + 1]
            else:
                target_range = full_tsteps
                
            res = self.calculate_strain_evolution_cached(domains, target_range, study, system, slice_axis, observe_axis, options)
            if res:
                all_results.append(res)
                
        if not all_results:
            return None
            
        # 2. Find common timesteps across all result sets
        common_timesteps_set = set(all_results[0]['timesteps'])
        for res in all_results[1:]:
            common_timesteps_set &= set(res['timesteps'])
            
        if not common_timesteps_set:
            return None
            
        sorted_tsteps = sorted(list(common_timesteps_set))
        
        # 3. Aggregate
        # We assume domain names and count are consistent across systems for the same study/setup
        # Find index mapping for each result set to the common timesteps
        final_domains = []
        
        # Initialize final structures based on first result set
        ref_res = all_results[0]
        for ref_dom in ref_res['domains']:
            final_domains.append({
                'name': ref_dom['name'],
                'strains': [],
                'stds': [], # This will be the std dev ACROSS systems
                'source_index': ref_dom.get('source_index'),
                'parent_identity': ref_dom.get('parent_identity'),
                'color': ref_dom.get('color'),
                'style': ref_dom.get('style')
            })
            
        # For each common timestep, calculate mean and std across all systems
        target_strains_agg = []
        
        for ts in sorted_tsteps:
            # Aggregate target strains
            ts_target_strains = []
            for res in all_results:
                try:
                    idx = res['timesteps'].index(ts)
                    ts_target_strains.append(res['target_strains'][idx])
                except (ValueError, IndexError):
                    pass
            target_strains_agg.append(np.mean(ts_target_strains) if ts_target_strains else np.nan)
            
            # Aggregate domain strains
            for d_idx, f_dom in enumerate(final_domains):
                ts_dom_strains = []
                for res in all_results:
                    try:
                        idx = res['timesteps'].index(ts)
                        # We match by domain index/name
                        ts_dom_strains.append(res['domains'][d_idx]['strains'][idx])
                    except (ValueError, IndexError):
                        pass
                
                valid_strains = [s for s in ts_dom_strains if not np.isnan(s)]
                if valid_strains:
                    f_dom['strains'].append(np.mean(valid_strains))
                    f_dom['stds'].append(np.std(valid_strains, ddof=1) if len(valid_strains) > 1 else 0.0)
                else:
                    f_dom['strains'].append(np.nan)
                    f_dom['stds'].append(0.0)
                    
        return {
            'timesteps': sorted_tsteps,
            'target_strains': target_strains_agg,
            'domains': final_domains
        }
        