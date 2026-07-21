
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Set
from pathlib import Path
from trajectory_parser import TrajectoryParser

class DSDDataManager:
    def __init__(self):
        # Storage: {study_name: {system_name: TrajectoryParser}}
        self.parsers: Dict[str, Dict[str, TrajectoryParser]] = {}
        self.current_study = None
        self.current_system = None
        self.cache = {} 
        self.slicing_results = {} 
        self.strain_evolution_cache = {} # Key: (Study, System, DomainIdentity) -> {strains, stds}

    def load_project_data(self, studies: Dict[str, List[str]], root_path: Path, keywords: List[str], file_map: Dict[str, Path] = None):
        self.parsers.clear()
        self.cache.clear()
        self.strain_evolution_cache.clear()
        warnings = []
        successful_keywords = set()

        if file_map is None: file_map = {}

        for study_name, system_list in studies.items():
            self.parsers[study_name] = {}
            for system_name in system_list:
                target_files = []
                if study_name == '.' and system_name in file_map:
                    target_files = [file_map[system_name]]
                else:
                    system_path = root_path / study_name / system_name
                    if system_path.is_dir():
                        for keyword in keywords:
                            found = list(system_path.glob(f"*{keyword}*"))
                            target_files.extend(found)
                
                if not target_files: continue
                target_files.sort()
                
                for fpath in target_files:
                    try:
                        parser = TrajectoryParser(fpath)
                        if parser.get_column_names():
                            self.parsers[study_name][system_name] = parser
                            for kw in keywords:
                                if kw in fpath.name: successful_keywords.add(kw)
                            break 
                    except Exception: pass

        if not self.parsers:
             warnings.append("No valid trajectory files found.")

        return warnings, successful_keywords

    def get_parser(self, study, system):
        return self.parsers.get(study, {}).get(system)

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
        """
        Wrapper that handles domain splitting.
        Returns a list of DataFrames (one per segment).
        """
        splits = settings.get('splits', [])
        active_segments = settings.get('active_segments', [])
        
        results = []
        
        if not splits:
            # Single domain
            res = self._slice_disp_single_domain(
                df_initial, df_curr, settings, slice_axis, observe_axis, box_curr, 
                z_col, z_ref, z_ranges, df_final, box_init
            )
            if res is not None:
                results.append(res)
        else:
            # Splits exist
            # Define segments
            # Sort splits just in case
            sorted_splits = sorted(splits)
            
            # We need the full range to define the first and last segments?
            # Or assume -inf to split[0], split[0] to split[1], ..., split[n] to inf?
            # Or use data min/max?
            # Let's use the full data range or box_edges if defined, otherwise dynamic.
            # Ideally, if box_edges not defined, we use min/max of data.
            # But here we need to force boundaries.
            
            # The segments are:
            # (-inf, s0), (s0, s1), ..., (sn, inf)
            # We pass these as `box_edges` to the single domain function.
            
            # We need to respect the original box_edges if they were set?
            # If user set manual box edges, the splits should ideally be within them.
            # But the split widget assumes full control.
            
            # Let's construct segment ranges.
            # We use None to indicate "use data min/max" for the open ends.
            
            current_min = None
            
            # Prepare segments list
            segments = []
            
            # First segment
            segments.append((None, sorted_splits[0]))
            
            # Middle segments
            for i in range(len(sorted_splits) - 1):
                segments.append((sorted_splits[i], sorted_splits[i+1]))
                
            # Last segment
            segments.append((sorted_splits[-1], None))
            
            # Ensure active_segments length matches
            if len(active_segments) < len(segments):
                active_segments.extend([True] * (len(segments) - len(active_segments)))
                
            for i, (seg_min, seg_max) in enumerate(segments):
                if not active_segments[i]:
                    continue
                    
                # Create a modified settings dict
                seg_settings = settings.copy()
                
                # Let's try to fetch global min/max if needed.
                if seg_min is None or seg_max is None:
                    # We need global bounds of the ATOMS selected
                    atom_types = settings.get('atom_types', [])
                    if atom_types:
                        sub = df_initial[df_initial['type'].isin(atom_types)]
                    else:
                        sub = df_initial
                    
                    if not sub.empty:
                        data_min = sub[slice_axis].min()
                        data_max = sub[slice_axis].max()
                    else:
                        continue # No data
                        
                    eff_min = seg_min if seg_min is not None else data_min
                    eff_max = seg_max if seg_max is not None else data_max
                    
                    # Ensure range is valid
                    if eff_min >= eff_max: continue
                    
                    seg_settings['box_edges'] = (eff_min, eff_max)
                else:
                    seg_settings['box_edges'] = (seg_min, seg_max)
                
                # Call inner
                res = self._slice_disp_single_domain(
                    df_initial, df_curr, seg_settings, slice_axis, observe_axis, box_curr, 
                    z_col, z_ref, z_ranges, df_final, box_init
                )
                
                if res is not None:
                    # Mark this result with segment info if needed
                    res.attrs['segment_index'] = i
                    results.append(res)
                    
        return results

    def _get_slice_cache_key(self, df_initial, df_curr, settings, slice_axis, observe_axis, 
                             z_col, z_ref, z_ranges, df_final):
        # Create a stable identity for the settings dictionary
        # Only include keys that affect calculation
        relevant_keys = [
            'atom_types', 'box_edges', 'number_boxes', 'num_boxes', 
            'box_arrangement', 'arrangement', 'overlap_percentage', 'overlap',
            'pbc', 'weighting_shape', 'weighted', 'bary_mid_twoside_weight', 'fit_outer_box'
        ]
        
        settings_tuple = []
        for k in sorted(relevant_keys):
            val = settings.get(k)
            if isinstance(val, list):
                val = tuple(sorted(val)) if k == 'atom_types' else tuple(val)
            settings_tuple.append((k, val))
            
        z_ranges_tuple = tuple(sorted(z_ranges)) if z_ranges else None
        
        return (
            id(df_initial), 
            id(df_curr), 
            tuple(settings_tuple), 
            slice_axis, 
            observe_axis,
            z_col, 
            z_ref, 
            z_ranges_tuple, 
            id(df_final) if df_final is not None else None
        )

    def _slice_disp_single_domain(self, df_initial, df_curr, settings, slice_axis, observe_axis, box_curr,
                        z_col=None, z_ref='Current', z_ranges=None, df_final=None, box_init=None):
        """
        Implements the slice_disp_mean logic with full triclinic (shear) support.
        Separates Geometry Definition (from Atom Types) and Population Filtering (from Z/Property).
        """
        
        # Check cache first
        cache_key = self._get_slice_cache_key(df_initial, df_curr, settings, slice_axis, observe_axis, 
                                              z_col, z_ref, z_ranges, df_final)
        
        if cache_key in self.slicing_results:
            cached = self.slicing_results[cache_key]
            return cached.copy() if cached is not None else None

        # --- 1. GEOMETRY PHASE: Filter by Atom Type ---
        # This defines the "stable" domain and box boundaries.
        atom_types = settings.get('atom_types', [])
        
        # Prepare Geometry DataFrames (filtered by Type only)
        if atom_types:
            df_init_geo = df_initial[df_initial['type'].isin(atom_types)].copy()
            df_curr_geo = df_curr[df_curr['id'].isin(df_init_geo['id'])].copy()
            
            # Reindex for alignment
            df_init_geo = df_init_geo.set_index('id').sort_index()
            df_curr_geo = df_curr_geo.set_index('id').sort_index()
            
            common_ids = df_init_geo.index.intersection(df_curr_geo.index)
            df_init_geo = df_init_geo.loc[common_ids]
            df_curr_geo = df_curr_geo.loc[common_ids]
        else:
            df_init_geo = df_initial.set_index('id').sort_index()
            df_curr_geo = df_curr.set_index('id').sort_index()

        if df_init_geo.empty:
            self.slicing_results[cache_key] = None
            return None

        # --- 2. POPULATION PHASE: Property (Z) Filtering ---
        # This reduces the number of particles but MUST NOT change the box definitions.
        
        df_init_pop = df_init_geo
        df_curr_pop = df_curr_geo

        if z_col and z_col != "No Z-Filter" and z_ranges:
            z_vals = None
            filter_ids = None

            if z_ref == 'Initial':
                # Filter based on values in the Initial Frame (Geometry subset)
                if z_col in df_init_geo.columns:
                    z_vals = df_init_geo[z_col].values
                    
            elif z_ref == 'Final':
                # Filter based on values in the Final Frame
                if df_final is not None:
                     # Join Final frame on the existing Geometry IDs
                     df_final_sub = df_final[df_final['id'].isin(df_init_geo.index)]
                     df_final_sub = df_final_sub.set_index('id').reindex(df_init_geo.index)
                     if z_col in df_final_sub.columns:
                        z_vals = df_final_sub[z_col].values
                        
            else: # Current
                # Filter based on values in the Current Frame (Geometry subset)
                if z_col in df_curr_geo.columns:
                    z_vals = df_curr_geo[z_col].values
            
            # Apply Filter Mask
            if z_vals is not None:
                mask = np.zeros(len(z_vals), dtype=bool)
                for (z_min, z_max) in z_ranges:
                    mask |= (z_vals >= z_min) & (z_vals <= z_max)
                
                df_init_pop = df_init_geo[mask]
                df_curr_pop = df_curr_geo[mask]

        if df_init_pop.empty:
            self.slicing_results[cache_key] = None
            return None

        # --- 3. PREPARE COORDINATES (Population) ---
        # We use the filtered POPULATION for displacement calculation
        def get_all_coords(df):
            return df['x'].values, df['y'].values, df['z'].values

        xi, yi, zi = get_all_coords(df_init_pop)
        xc, yc, zc = get_all_coords(df_curr_pop)
        
        coords_i = {'x': xi, 'y': yi, 'z': zi}
        coords_c = {'x': xc, 'y': yc, 'z': zc}
        
        # Population arrays for calculation
        pos_slice_init_pop = coords_i[slice_axis.lower()]
        pos_slice_curr_pop = coords_c[slice_axis.lower()]
        
        # --- 4. BOX SETUP (Geometry) ---
        # We use the UNFILTERED GEOMETRY (df_init_geo) to define auto-bounds if needed
        
        box_edges = settings.get('box_edges', float('inf'))
        num_boxes = settings.get('number_boxes', settings.get('num_boxes', 10))
        arrangement = settings.get('box_arrangement', settings.get('arrangement', 'inside'))
        overlap_pct = settings.get('overlap_percentage', settings.get('overlap', 0.0))
        pbc_active = settings.get('pbc', False)
        
        # Robust Helper to extract Global Orthogonal Lengths and Tilts
        def get_box_params(box):
            if not box: return {}
            
            if len(box) >= 4:
                xy, xz, yz = box[3][0], box[3][1], box[3][2]
                xb, yb, zb = box[0], box[1], box[2]
            else:
                xy = box[0][2] if len(box[0]) > 2 else 0.0
                xz = box[1][2] if len(box[1]) > 2 else 0.0
                yz = box[2][2] if len(box[2]) > 2 else 0.0
                xb, yb, zb = box[0], box[1], box[2]

            lz = zb[1] - zb[0]
            zlo = zb[0]
            
            ly = (yb[1] - yb[0]) - abs(yz)
            ylo = yb[0] - min(0.0, yz)
            
            mx = min(0.0, xy, xz, xy+xz)
            gx = max(0.0, xy, xz, xy+xz)
            lx = (xb[1] - xb[0]) - (gx - mx)
            xlo = xb[0] - mx
            
            return {'lx':lx, 'ly':ly, 'lz':lz, 
                    'xy':xy, 'xz':xz, 'yz':yz, 
                    'xlo':xlo, 'ylo':ylo, 'zlo':zlo}

        pi = get_box_params(box_init)
        pc = get_box_params(box_curr)

        # Pre-calculate Orthogonal Lengths and Tilt shifts
        s_ax = slice_axis.lower()
        
        shift_vec_i = np.array([0.,0.,0.])
        shift_vec_c = np.array([0.,0.,0.])
        
        if s_ax == 'x':
            shift_vec_i = np.array([pi['lx'], 0.0, 0.0])
            shift_vec_c = np.array([pc['lx'], 0.0, 0.0])
        elif s_ax == 'y':
            shift_vec_i = np.array([pi['xy'], pi['ly'], 0.0])
            shift_vec_c = np.array([pc['xy'], pc['ly'], 0.0])
        elif s_ax == 'z':
            shift_vec_i = np.array([pi['xz'], pi['yz'], pi['lz']])
            shift_vec_c = np.array([pc['xz'], pc['yz'], pc['lz']])
            
        L_slice_init = pi.get(f'l{s_ax}', 1.0)
        
        # Determine Box Boundaries (Initial)
        if (box_edges == float('inf') or box_edges is None):
            # Use GEOMETRY data (unfiltered by property) to find bounds
            pos_slice_geo = df_init_geo[slice_axis].values
            
            if pbc_active and L_slice_init > 0:
                s_idx = {'x':0, 'y':1, 'z':2}.get(slice_axis.lower())
                box_edges = (box_init[s_idx][0], box_init[s_idx][1])
            else:
                box_edges = (np.min(pos_slice_geo), np.max(pos_slice_geo))
        
        # Handle partially None box_edges (e.g. (None, 50.0) from splits)
        if isinstance(box_edges, (list, tuple)) and (box_edges[0] is None or box_edges[1] is None):
            pos_slice_geo = df_init_geo[slice_axis].values
            if pbc_active and L_slice_init > 0:
                s_idx = {'x':0, 'y':1, 'z':2}.get(slice_axis.lower())
                fallback = (box_init[s_idx][0], box_init[s_idx][1])
            else:
                fallback = (np.min(pos_slice_geo), np.max(pos_slice_geo))
            
            be_list = list(box_edges)
            if be_list[0] is None: be_list[0] = fallback[0]
            if be_list[1] is None: be_list[1] = fallback[1]
            box_edges = tuple(be_list)

        box_len_total = box_edges[1] - box_edges[0]
        target_width = box_len_total / num_boxes if num_boxes > 0 else box_len_total
        
        # Generate Box Centers
        pairs = [] 
        if arrangement == 'combined':
            c1 = np.linspace(box_edges[0] + target_width/2, box_edges[1] - target_width/2, num_boxes)
            for c in c1: pairs.append({'c': c, 'l': target_width, 'type': 'inside'})
            c2 = np.linspace(box_edges[0], box_edges[1], num_boxes + 1)
            for c in c2: pairs.append({'c': c, 'l': target_width, 'type': 'protrude'})
            pairs.sort(key=lambda x: x['c'])
        elif arrangement == 'protrude':
            c2 = np.linspace(box_edges[0], box_edges[1], num_boxes + 1)
            for c in c2: pairs.append({'c': c, 'l': target_width, 'type': 'protrude'})
        else: # inside
            c1 = np.linspace(box_edges[0] + target_width/2, box_edges[1] - target_width/2, num_boxes)
            for c in c1: pairs.append({'c': c, 'l': target_width, 'type': 'inside'})
                                               
        results = []
        weight_shape = settings.get('weighting_shape', 1)
        fit_outer = settings.get('fit_outer_box', False)
        bary_mode = settings.get('bary_mid_twoside_weight', False)
        use_weighted = settings.get('weighted', False) 

        # --- 5. CALCULATION LOOP (Population) ---
        for p_data in pairs:
            center, curr_len, box_type = p_data['c'], p_data['l'], p_data['type']
            half_width = (curr_len / 2) * (1 + overlap_pct / 100.0)
            b_min, b_max = center - half_width, center + half_width
            
            # Filter Main Particles (using POPULATION data)
            mask_main = (pos_slice_init_pop >= b_min) & (pos_slice_init_pop <= b_max)
            
            # Accumulate full coordinates for bias calculation
            list_xi, list_yi, list_zi = [xi[mask_main]], [yi[mask_main]], [zi[mask_main]]
            list_xc, list_yc, list_zc = [xc[mask_main]], [yc[mask_main]], [zc[mask_main]]
            list_slice_for_binning = [pos_slice_init_pop[mask_main]]
            list_slice_curr = [pos_slice_curr_pop[mask_main]]

            # 4b. PBC Ghost Particles
            if pbc_active and L_slice_init > 0 and box_type == 'protrude':
                ghost_pos_L = pos_slice_init_pop - L_slice_init
                mask_gL = (ghost_pos_L >= b_min) & (ghost_pos_L <= b_max)
                if np.any(mask_gL):
                    list_slice_for_binning.append(ghost_pos_L[mask_gL])
                    # Shift current slice position using shift vector of current box
                    s_idx = {'x':0, 'y':1, 'z':2}[s_ax]
                    L_slice_curr_val = shift_vec_c[s_idx]
                    list_slice_curr.append(pos_slice_curr_pop[mask_gL] - L_slice_curr_val)
                    
                    list_xi.append(xi[mask_gL] - shift_vec_i[0])
                    list_yi.append(yi[mask_gL] - shift_vec_i[1])
                    list_zi.append(zi[mask_gL] - shift_vec_i[2])
                    
                    list_xc.append(xc[mask_gL] - shift_vec_c[0])
                    list_yc.append(yc[mask_gL] - shift_vec_c[1])
                    list_zc.append(zc[mask_gL] - shift_vec_c[2])

                ghost_pos_R = pos_slice_init_pop + L_slice_init
                mask_gR = (ghost_pos_R >= b_min) & (ghost_pos_R <= b_max)
                if np.any(mask_gR):
                    list_slice_for_binning.append(ghost_pos_R[mask_gR])
                    s_idx = {'x':0, 'y':1, 'z':2}[s_ax]
                    L_slice_curr_val = shift_vec_c[s_idx]
                    list_slice_curr.append(pos_slice_curr_pop[mask_gR] + L_slice_curr_val)
                    
                    list_xi.append(xi[mask_gR] + shift_vec_i[0])
                    list_yi.append(yi[mask_gR] + shift_vec_i[1])
                    list_zi.append(zi[mask_gR] + shift_vec_i[2])
                    
                    list_xc.append(xc[mask_gR] + shift_vec_c[0])
                    list_yc.append(yc[mask_gR] + shift_vec_c[1])
                    list_zc.append(zc[mask_gR] + shift_vec_c[2])

            # Concatenate
            all_xi, all_yi, all_zi = np.concatenate(list_xi), np.concatenate(list_yi), np.concatenate(list_zi)
            all_xc, all_yc, all_zc = np.concatenate(list_xc), np.concatenate(list_yc), np.concatenate(list_zc)
            p_pos_for_w = np.concatenate(list_slice_for_binning)
            p_pos_curr_for_cross = np.concatenate(list_slice_curr)
            
            if len(all_xi) == 0: continue

            # Extract Observation Axis Arrays
            obs_map_i = {'x':all_xi, 'y':all_yi, 'z':all_zi}
            obs_map_c = {'x':all_xc, 'y':all_yc, 'z':all_zc}
            p_init = obs_map_i[observe_axis.lower()]
            p_curr = obs_map_c[observe_axis.lower()]

            # --- Displacement Calculation with Full 3D Affine Bias ---
            p_disp = p_curr - p_init
            
            # Periodicity length for Observation Axis (for MIC)
            o_ax = observe_axis.lower()
            L_obs_curr = pc.get(f'l{o_ax}', 1.0)
            
            if pbc_active and L_obs_curr > 0:
                # 1. Crossing Correction (Slice Axis Wrap)
                s_idx = {'x':0, 'y':1, 'z':2}[s_ax]
                L_s_curr_val = shift_vec_c[s_idx]
                
                # Determine tilt_curr (shift in O when S wraps)
                o_idx = {'x':0, 'y':1, 'z':2}[o_ax]
                tilt_curr = shift_vec_c[o_idx]
                tilt_init = shift_vec_i[o_idx]

                if L_s_curr_val > 0:
                    d_slice = p_pos_curr_for_cross - p_pos_for_w
                    crossings = np.round(d_slice / L_s_curr_val)
                    if np.any(crossings != 0) and tilt_curr != 0:
                        p_disp -= crossings * tilt_curr

                # 2. Affine-Biased MIC (Observation Axis Wrap)
                w_rel = (all_zi - pi['zlo']) / pi['lz']
                v_rel = (all_yi - (pi['ylo'] + w_rel * pi['yz'])) / pi['ly']
                u_rel = (all_xi - (pi['xlo'] + v_rel * pi['xy'] + w_rel * pi['xz'])) / pi['lx']
                
                # Change in box parameters
                d_lx, d_ly, d_lz = pc['lx']-pi['lx'], pc['ly']-pi['ly'], pc['lz']-pi['lz']
                d_xy, d_xz, d_yz = pc['xy']-pi['xy'], pc['xz']-pi['xz'], pc['yz']-pi['yz']
                
                d_bias = np.zeros_like(p_disp)
                if o_ax == 'x':
                    d_bias = u_rel * d_lx + v_rel * d_xy + w_rel * d_xz
                elif o_ax == 'y':
                    d_bias = v_rel * d_ly + w_rel * d_yz
                elif o_ax == 'z':
                    d_bias = w_rel * d_lz
                
                # Use strict Orthogonal Periodicity Length for MIC
                p_disp -= L_obs_curr * np.round((p_disp - d_bias) / L_obs_curr)

            # Weighting and Stats
            eff_center = (np.min(pos_slice_init_pop[mask_main]) + np.max(pos_slice_init_pop[mask_main])) / 2 if (fit_outer and len(mask_main) > 0 and np.any(mask_main)) else center
                
            w = None
            if not bary_mode:
                sigma = (b_max - b_min) / 5.0
                if sigma == 0: w = np.ones_like(p_init)
                else:
                    dist = p_pos_for_w - eff_center
                    w = np.exp(-(dist**2)/(2*sigma**2)) ** weight_shape
            else:
                bary_center = np.mean(p_pos_for_w)
                w = np.zeros_like(p_pos_for_w)
                mask_left, mask_right = p_pos_for_w <= bary_center, p_pos_for_w > bary_center
                sigma_left, sigma_right = (bary_center - b_min)/2.5, (b_max - bary_center)/2.5
                if sigma_left > 0: w[mask_left] = np.exp(-((p_pos_for_w[mask_left]-bary_center)**2)/(2*sigma_left**2))**weight_shape
                else: w[mask_left] = 1.0
                if sigma_right > 0: w[mask_right] = np.exp(-((p_pos_for_w[mask_right]-bary_center)**2)/(2*sigma_right**2))**weight_shape
                else: w[mask_right] = 1.0
                eff_center = bary_center
            
            if not use_weighted: w = np.ones_like(p_init)
            
            if (sum_w_total := np.sum(w)) == 0: continue

            n_main = len(list_slice_for_binning[0])
            sum_w_display = np.sum(w[:n_main])
            
            if use_weighted:
                mean_disp = np.average(p_disp, weights=w)
                variance = np.average((p_disp - mean_disp)**2, weights=w)
            else:
                mean_disp = np.mean(p_disp)
                variance = np.var(p_disp)
                
            results.append({
                'center': eff_center,
                'mean_disp': mean_disp,
                'std_dev': np.sqrt(variance),
                'count': n_main,
                'sum_weights': sum_w_display
            })
        
        final_df = pd.DataFrame(results)
        self.slicing_results[cache_key] = final_df
        return final_df

    def calculate_strain_evolution_cached(self, domains, timesteps, study, system, slice_axis, observe_axis, options):
        """
        Pre-computes strain and target strain for ALL timesteps.
        Uses an internal cache to skip recalculating domains that haven't changed.
        """
        # 1. Expand domains into segments (virtual domains)
        virtual_domains = []
        for d_idx, domain in enumerate(domains):
            if domain.get('is_optimal_line'): continue
            
            d_base = domain.copy()
            d_base['_source_index'] = d_idx
            # Identity key for matching after row swaps
            parent_identity = (domain.get('name'), tuple(domain.get('splits', [])), tuple(domain.get('active_segments', [])))
            d_base['_parent_identity'] = parent_identity

            splits = domain.get('splits', [])
            active_segments = domain.get('active_segments', [])
            
            if not splits:
                virtual_domains.append( (d_base, domain.get('name', f"Domain {d_idx}")) )
            else:
                sorted_splits = sorted(splits)
                segments = []
                segments.append((None, sorted_splits[0]))
                for i in range(len(sorted_splits) - 1):
                    segments.append((sorted_splits[i], sorted_splits[i+1]))
                segments.append((sorted_splits[-1], None))
                
                if len(active_segments) < len(segments):
                    active_segments.extend([True] * (len(segments) - len(active_segments)))
                
                active_count = sum(active_segments)
                active_rank = 0
                for i, (seg_min, seg_max) in enumerate(segments):
                    if active_segments[i]:
                        active_rank += 1
                        d_new = d_base.copy()
                        d_new['box_edges'] = (seg_min, seg_max)
                        d_new['_is_split'] = True
                        d_new['_split_rank'] = active_rank
                        # d_new already has _parent_identity from d_base.copy()
                        
                        base_name = domain.get('name', f"Domain {d_idx+1}")
                        name = f"{base_name}{active_rank}" if active_count > 1 else base_name
                        virtual_domains.append( (d_new, name) )

        if not timesteps: return None
        df_init, box_init = self.load_frame(study, system, timesteps[0])
        if df_init is None: return None

        # Check PBC (Logic from DSDController)
        any_domain_pbc = False
        for d in domains:
             if d.get('is_optimal_line'): continue
             if d.get('pbc', False): 
                 any_domain_pbc = True
                 break

        # 2. Identify missing data vs cached data
        def get_calc_identity(v_domain):
            # All parameters that affect the numerical strain calculation
            return (
                study, system, slice_axis, observe_axis,
                tuple(v_domain.get('box_edges', (None, None))),
                tuple(sorted(v_domain.get('atom_types', []))),
                v_domain.get('pbc', False),
                v_domain.get('weighted', False),
                v_domain.get('bary_mid_twoside_weight', False),
                options.get('z_filter_col'),
                options.get('z_filter_ref'),
                tuple(sorted(options.get('z_ranges'))) if options.get('z_ranges') else None
            )

        # Map each virtual domain to its cache key and check if we have it
        domain_tasks = [] # (index_in_results, v_domain, identity_key)
        
        # Pre-allocate results with cached data where available
        final_domains_data = []
        for i, (v_domain, name) in enumerate(virtual_domains):
            ident = get_calc_identity(v_domain)
            cached = self.strain_evolution_cache.get(ident)
            
            d_res = {
                'name': name,
                'strains': [],
                'stds': [],
                'min_x': [], 'max_x': [],
                'y_at_min_x': [], 'y_at_max_x': [],
                'color': v_domain.get('color', 'blue'),
                'style': v_domain.get('style', '-'),
                'source_index': v_domain.get('_source_index'),
                'parent_identity': v_domain.get('_parent_identity')
            }
            
            if cached and len(cached['strains']) == len(timesteps):
                d_res['strains'] = cached['strains']
                d_res['stds'] = cached['stds']
                # Retrieve new fields with fallback for old cache entries
                d_res['min_x'] = cached.get('min_x', [np.nan]*len(timesteps))
                d_res['max_x'] = cached.get('max_x', [np.nan]*len(timesteps))
                d_res['y_at_min_x'] = cached.get('y_at_min_x', [np.nan]*len(timesteps))
                d_res['y_at_max_x'] = cached.get('y_at_max_x', [np.nan]*len(timesteps))
            else:
                domain_tasks.append((i, v_domain, ident))
            
            final_domains_data.append(d_res)

        # 3. Calculate Missing Data (Only if needed)
        if domain_tasks:
            s_idx = {'x':0,'y':1,'z':2}.get(slice_axis.lower())
            o_idx = {'x':0,'y':1,'z':2}.get(observe_axis.lower())

            # We iterate timesteps ONCE and calculate all missing domains for that step
            for ts_idx, ts in enumerate(timesteps):
                df_curr, box_curr = self.load_frame(study, system, ts)
                if df_curr is None:
                    for res_idx, _, _ in domain_tasks:
                        final_domains_data[res_idx]['strains'].append(np.nan)
                        final_domains_data[res_idx]['stds'].append(0.0)
                        final_domains_data[res_idx]['min_x'].append(np.nan)
                        final_domains_data[res_idx]['max_x'].append(np.nan)
                        final_domains_data[res_idx]['y_at_min_x'].append(np.nan)
                        final_domains_data[res_idx]['y_at_max_x'].append(np.nan)
                    continue

                for res_idx, v_domain, _ in domain_tasks:
                    res = self._slice_disp_single_domain(
                        df_init, df_curr, v_domain, slice_axis, observe_axis, box_curr,
                        z_col=options.get('z_filter_col'), z_ref=options.get('z_filter_ref'),
                        z_ranges=options.get('z_ranges'), box_init=box_init
                    )
                    
                    mean_strain, std_strain = np.nan, 0.0
                    bx_min, bx_max, by_min, by_max = np.nan, np.nan, np.nan, np.nan

                    if res is not None and not res.empty:
                        # ALWAYS use the connecting line (Secant) between min/max center
                        centers = res['center'].values
                        disps = res['mean_disp'].values
                        if len(centers) >= 2:
                            idxs = np.argsort(centers)
                            x_sorted = centers[idxs]
                            y_sorted = disps[idxs]
                            
                            bx_min, bx_max = x_sorted[0], x_sorted[-1]
                            by_min, by_max = y_sorted[0], y_sorted[-1]
                            
                            if bx_max > bx_min:
                                mean_strain = (by_max - by_min) / (bx_max - bx_min)
                                std_strain = 0.0 
                            else:
                                mean_strain = 0.0
                        else:
                            mean_strain = 0.0
                    
                    final_domains_data[res_idx]['strains'].append(mean_strain)
                    final_domains_data[res_idx]['stds'].append(std_strain)
                    final_domains_data[res_idx]['min_x'].append(bx_min)
                    final_domains_data[res_idx]['max_x'].append(bx_max)
                    final_domains_data[res_idx]['y_at_min_x'].append(by_min)
                    final_domains_data[res_idx]['y_at_max_x'].append(by_max)

            # Store new results in cache
            for res_idx, _, ident in domain_tasks:
                self.strain_evolution_cache[ident] = {
                    'strains': final_domains_data[res_idx]['strains'],
                    'stds': final_domains_data[res_idx]['stds'],
                    'min_x': final_domains_data[res_idx]['min_x'],
                    'max_x': final_domains_data[res_idx]['max_x'],
                    'y_at_min_x': final_domains_data[res_idx]['y_at_min_x'],
                    'y_at_max_x': final_domains_data[res_idx]['y_at_max_x']
                }

        # 4. Target Strain Calculation (Harmonized with Controller)
        # Note: Target strain is global for the whole system state, so we always re-verify it?
        # Actually, if we have at least one domain result for EACH timestep, we can calc it.
        # Let's just calculate it for simplicity, it's cheap.
        target_strains = []
        s_idx = {'x':0,'y':1,'z':2}.get(slice_axis.lower())
        o_idx = {'x':0,'y':1,'z':2}.get(observe_axis.lower())

        for ts_idx, ts in enumerate(timesteps):
            # We need the segments for THIS timestep to compute data-based target
            if any_domain_pbc:
                _, box_curr = self.load_frame(study, system, ts)
                target_strains.append(self._calculate_target_strain(box_init, box_curr, s_idx, o_idx))
            else:
                # Non-PBC: Target strain is the slope of the line connecting 
                # the global min and max X of the system (Optimal Line).
                g_min_x, g_max_x = np.inf, -np.inf
                val_at_min, val_at_max = np.nan, np.nan
                
                found_data = False
                
                # Check all domains for this timestep
                for d_data in final_domains_data:
                    # Skip if no data for this timestep
                    if ts_idx >= len(d_data['min_x']): continue
                    
                    x_min = d_data['min_x'][ts_idx]
                    x_max = d_data['max_x'][ts_idx]
                    y_min = d_data['y_at_min_x'][ts_idx]
                    y_max = d_data['y_at_max_x'][ts_idx]
                    
                    if np.isnan(x_min) or np.isnan(x_max): continue
                    
                    if x_min < g_min_x:
                        g_min_x = x_min
                        val_at_min = y_min
                        found_data = True
                        
                    if x_max > g_max_x:
                        g_max_x = x_max
                        val_at_max = y_max
                        found_data = True
                
                if found_data and g_max_x > g_min_x:
                     ts_val = (val_at_max - val_at_min) / (g_max_x - g_min_x)
                     target_strains.append(ts_val)
                else:
                     target_strains.append(0.0)

        return {
            'timesteps': timesteps,
            'target_strains': target_strains,
            'domains': final_domains_data
        }



    def _calculate_target_strain(self, box_init, box_curr, s_idx, o_idx):
        if not box_init or not box_curr or s_idx is None or o_idx is None:
            return 0.0
        
        def get_box_params(box_data):
            xb, yb, zb = box_data[0], box_data[1], box_data[2]
            xy = xb[2] if len(xb) > 2 else 0.0
            xz = yb[2] if len(yb) > 2 else 0.0 
            yz = zb[2] if len(zb) > 2 else 0.0
            zlo, lz = zb[0], zb[1] - zb[0]
            ylo, ly = yb[0] - min(0.0, yz), (yb[1] - yb[0]) - abs(yz)
            mins_x = min(0.0, xy, xz, xy+xz)
            maxs_x = max(0.0, xy, xz, xy+xz)
            xlo, lx = xb[0] - mins_x, (xb[1] - xb[0]) - (maxs_x - mins_x)
            return {'xlo': xlo, 'ylo': ylo, 'zlo': zlo, 'lx': max(1e-9, lx), 'ly': max(1e-9, ly), 'lz': max(1e-9, lz), 'xy': xy, 'xz': xz, 'yz': yz}

        p_i = get_box_params(box_init)
        p_c = get_box_params(box_curr)

        def calc_u(obj_idx, x_i, y_i, z_i):
            w0 = (z_i - p_i['zlo']) / p_i['lz']
            v0 = (y_i - (p_i['ylo'] + w0 * p_i['yz'])) / p_i['ly']
            u0 = (x_i - (p_i['xlo'] + v0 * p_i['xy'] + w0 * p_i['xz'])) / p_i['lx']
            x_c = p_c['xlo'] + u0 * p_c['lx'] + v0 * p_c['xy'] + w0 * p_c['xz']
            y_c = p_c['ylo'] + v0 * p_c['ly'] + w0 * p_c['yz']
            z_c = p_c['zlo'] + w0 * p_c['lz']
            return [x_c - x_i, y_c - y_i, z_c - z_i][obj_idx]

        c_i = [p_i['xlo'] + 0.5*p_i['lx'], p_i['ylo'] + 0.5*p_i['ly'], p_i['zlo'] + 0.5*p_i['lz']]
        x1, x2 = box_init[s_idx][0], box_init[s_idx][1]
        
        pos1 = list(c_i); pos1[s_idx] = x1
        pos2 = list(c_i); pos2[s_idx] = x2
        u1, u2 = calc_u(o_idx, *pos1), calc_u(o_idx, *pos2)
        
        return (u2 - u1) / (x2 - x1) if x2 != x1 else 0.0

    def _calculate_segment_strain(self, centers, displacements):
        if len(centers) < 2:
            return np.nan, 0.0
        # pairwise slopes (MATLAB equivalent)
        slopes = np.diff(displacements) / np.diff(centers)
        return float(np.nanmean(slopes)), float(np.nanstd(slopes))

    def calculate_full_evolution(self, domains, timesteps, study, system, slice_axis, observe_axis, options):
        output = []
        
        # 1. Expand domains into "virtual domains" (splits)
        virtual_domains = []
        for d_idx, domain in enumerate(domains):
            splits = domain.get('splits', [])
            active_segments = domain.get('active_segments', [])
            
            if not splits:
                virtual_domains.append( (domain, None) ) # None = full
            else:
                 # Same segment logic as above
                 sorted_splits = sorted(splits)
                 segments = []
                 segments.append((None, sorted_splits[0]))
                 for i in range(len(sorted_splits) - 1):
                    segments.append((sorted_splits[i], sorted_splits[i+1]))
                 segments.append((sorted_splits[-1], None))
                 
                 if len(active_segments) < len(segments):
                    active_segments.extend([True] * (len(segments) - len(active_segments)))
                 
                 active_count = sum(active_segments)
                 active_rank = 0
                 for i, (seg_min, seg_max) in enumerate(segments):
                     if active_segments[i]:
                         active_rank += 1
                         d_new = domain.copy()
                         d_new['box_edges'] = (seg_min, seg_max)
                         d_new['_is_split'] = True
                         d_new['_split_rank'] = active_rank
                         d_new['_has_multiple_active'] = (active_count > 1)
                         virtual_domains.append( (d_new, domain.get('name', f"Domain {d_idx+1}")) )

        # 2. Iterate
        # Pre-load init
        df_init, box_init = self.load_frame(study, system, timesteps[0])
        if df_init is None: return []

        strain_history = { i: [] for i in range(len(virtual_domains)) }
        valid_steps = []
        
        for ts in timesteps:
            df_curr, box_curr = self.load_frame(study, system, ts)
            if df_curr is None: continue
            
            valid_steps.append(ts)
            
            for i, (v_domain, orig_name) in enumerate(virtual_domains):
                # Resolve box edges if needed
                edges = v_domain.get('box_edges')
                if edges and (edges[0] is None or edges[1] is None):
                    # Resolve using df_init
                    atom_types = v_domain.get('atom_types', [])
                    if atom_types:
                        sub = df_init[df_init['type'].isin(atom_types)]
                    else:
                        sub = df_init
                    if not sub.empty:
                        d_min, d_max = sub[slice_axis].min(), sub[slice_axis].max()
                        eff_min = edges[0] if edges[0] is not None else d_min
                        eff_max = edges[1] if edges[1] is not None else d_max
                        v_domain['box_edges'] = (eff_min, eff_max)
                
                # Slice
                res = self._slice_disp_single_domain(
                    df_init, df_curr, v_domain, slice_axis, observe_axis, box_curr,
                    z_col=options.get('z_filter_col'), z_ref=options.get('z_filter_ref'),
                    z_ranges=None, # Evolution usually no Z-filter? Or pass it?
                    box_init=box_init
                )
                
                mean_strain = np.nan
                std_strain = 0.0
                
                if res is not None and not res.empty:
                    derivs = self.calculate_derivatives(res)
                    if derivs is not None and not derivs.empty:
                        mean_strain = derivs['strain'].mean()
                
                strain_history[i].append(mean_strain)
                if 'std' not in v_domain: v_domain['std'] = []
                v_domain['std'].append(std_strain)

        # 3. Output
        for i, (v_domain, orig_name) in enumerate(virtual_domains):
            # Name alignment with dialog/displacement numbering
            if v_domain.get('_is_split') and v_domain.get('_has_multiple_active'):
                name = f"{orig_name}{v_domain.get('_split_rank', 1)}"
            else:
                name = orig_name
                
            output.append({
                'name': name,
                'x': valid_steps,
                'y': strain_history[i],
                'y_err': v_domain.get('std', [0.0]*len(valid_steps)),
                'color': v_domain.get('color', 'blue'),
                'style': v_domain.get('style', '-')
            })
            
        return output

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