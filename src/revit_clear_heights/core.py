"""
Core clear height calculation engine and main Revit command entry point.
"""

try:
    from Autodesk.Revit.DB import (
        Transaction, XYZ, Plane, SketchPlane, BuiltInCategory,
        BuiltInParameter, FilteredElementCollector, ParameterFilterElement
    )
except ImportError:
    pass

from .geometry import get_room_polygon
from .evaluation import evaluate_room_clear_heights, round_to_half_cm, format_height_value

doc: object


def calculate_clear_heights(debug=False, create_debug_lines=False, create_ray_lines=False,
                            grid_spacing_min_m=0.5, grid_spacing_max_m=1.5,
                            area_min_m2=10.0, area_max_m2=100.0, area_threshold_pct=5.0,
                            filter_name=None, area_threshold_m2=10,
                            dominant_area_pct_threshold=50.0, dominant_n_times=2.0,
                            ray_length_multiplicator=1.5,
                            clear_height_param_name="Lichte_Raumhöhe",
                            dominant_clear_height_param_name="Lichte_Raumhöhe_Bestimmend",
                            ray_casting_view_name="RAY_CASTING_VIEW",
                            lower_elevation_param_name=None, upper_elevation_param_name=None,
                            range_delimiter="-"):
    """
    Calculate clear heights for all rooms with area > 0 in the active Revit document
    and write results back to room parameters.
    """
    # 1. Get all rooms in the document
    collector = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms)
    rooms = []
    for elem in collector:
        if getattr(elem, "Area", 0) > 0:
            rooms.append(elem)

    if not rooms:
        if debug:
            print("No rooms found in the document.")
        return

    # Find FilterElement if filter_name is provided
    element_filter = None
    if filter_name:
        filter_collector = FilteredElementCollector(doc).OfClass(ParameterFilterElement)
        for f in filter_collector:
            if f.Name == filter_name:
                element_filter = f.GetElementFilter()
                if debug:
                    print(f"Found filter '{filter_name}'.")
                break
        if not element_filter and debug:
            print(f"Warning: Filter '{filter_name}' not found.")

    # Start transaction and process rooms
    t = Transaction(doc, "Calculate Clear Heights")
    t.Start()

    try:
        for room in rooms:
            if debug:
                room_number_param = room.get_Parameter(BuiltInParameter.ROOM_NUMBER)
                room_name_param = room.get_Parameter(BuiltInParameter.ROOM_NAME)
                room_number = room_number_param.AsString() if room_number_param else "Unknown"
                room_name = room_name_param.AsString() if room_name_param else "Unknown"
                print(f"\n--- Processing Room: {room_name} ({room_number}) ---")

            # 2. Calculate the target Z height (Level Elevation + Computation Height)
            level = doc.GetElement(room.LevelId)
            level_elevation = level.Elevation if level else 0

            computation_height_param = level.get_Parameter(BuiltInParameter.LEVEL_ROOM_COMPUTATION_HEIGHT)
            computation_height = computation_height_param.AsDouble() if computation_height_param else 0

            small_offset = 0.01  # ~3mm above floor level
            z_height = level_elevation + computation_height + small_offset

            # 3. Extract room polygon
            room_poly = get_room_polygon(room)
            if not room_poly:
                if debug:
                    print("Could not extract valid boundaries from the room.")
                continue

            # 4. Determine dynamic grid spacing
            room_area_param = room.get_Parameter(BuiltInParameter.ROOM_AREA)
            room_area_sqft = room_area_param.AsDouble() if room_area_param else 0
            room_area_m2 = room_area_sqft * 0.092903

            if room_area_m2 <= area_min_m2:
                grid_spacing_m = grid_spacing_min_m
            elif room_area_m2 >= area_max_m2:
                grid_spacing_m = grid_spacing_max_m
            else:
                t_val = (room_area_m2 - area_min_m2) / (area_max_m2 - area_min_m2)
                grid_spacing_m = grid_spacing_min_m + t_val * (grid_spacing_max_m - grid_spacing_min_m)

            grid_spacing = grid_spacing_m * 3.28084  # convert to internal units (feet)

            if debug:
                print(f"Room area: {room_area_m2:.1f}m2 -> Adaptive Grid Spacing: {grid_spacing_m:.2f}m")

            # Create a flat SketchPlane at the exact calculated z_height
            origin = XYZ(0, 0, z_height)
            plane = Plane.CreateByNormalAndOrigin(XYZ.BasisZ, origin)
            sketch_plane = SketchPlane.Create(doc, plane)

            total_lines_created, evaluation_results = evaluate_room_clear_heights(
                doc, room, z_height, room_poly, grid_spacing,
                create_ray_lines=create_ray_lines,
                create_debug_lines=create_debug_lines,
                debug=debug,
                sketch_plane=sketch_plane,
                element_filter=element_filter,
                ray_length_multiplicator=ray_length_multiplicator,
                ray_casting_view_name=ray_casting_view_name,
                range_delimiter=range_delimiter
            )

            if debug and total_lines_created > 0:
                print(f"Success! Created {total_lines_created} model lines at Z={z_height * 0.3048:.3f}m showing the overlaps.")

            # Aggregate areas by height group and apply threshold filter
            height_areas = {}
            filtered_out_pct = 0.0
            filtered_out_area = 0.0

            for result in evaluation_results:
                area_pct = result['area_pct']
                heights = result['heights']
                is_ignored = result.get('is_ignored', False)
                f_elevs = result.get('f_elevs', [])
                c_elevs = result.get('c_elevs', [])

                if heights:
                    if is_ignored:
                        filtered_out_pct += area_pct
                        filtered_out_area += (area_pct / 100) * room_area_m2
                        continue

                    min_h = round_to_half_cm(min(heights))
                    max_h = round_to_half_cm(max(heights))
                    group_key = (min_h, max_h)

                    if group_key not in height_areas:
                        height_areas[group_key] = {'area_pct': 0.0, 'heights': [], 'f_elevs': [], 'c_elevs': []}

                    height_areas[group_key]['area_pct'] += area_pct
                    height_areas[group_key]['heights'].extend(heights)
                    height_areas[group_key]['f_elevs'].extend(f_elevs)
                    height_areas[group_key]['c_elevs'].extend(c_elevs)

            all_clear_heights = []
            all_floor_elevations = []
            all_ceiling_elevations = []

            total_included_pct = 0.0
            total_included_area = 0.0
            total_excluded_pct = 0.0
            total_excluded_area = 0.0

            dominant_height_result = None

            if debug and (height_areas or filtered_out_pct > 0):
                print("\n--- Clear Height Areas Summary ---")

            for (min_h, max_h), data in sorted(height_areas.items()):
                pct = data['area_pct']
                area_m2 = (pct / 100) * room_area_m2

                if min_h == max_h:
                    key_str = f"{format_height_value(min_h)} m"
                else:
                    key_str = f"{format_height_value(min_h)} m - {format_height_value(max_h)} m"

                if pct >= dominant_area_pct_threshold:
                    other_pcts = [d['area_pct'] for k, d in height_areas.items() if k != (min_h, max_h)]
                    if not other_pcts or all(pct >= p * dominant_n_times for p in other_pcts):
                        rounded_pct = int(round(pct / 5.0)) * 5
                        dominant_height_result = f"{key_str} (~{rounded_pct}%)"

                meets_pct_threshold = pct >= area_threshold_pct
                meets_area_threshold = (area_threshold_m2 is not None and area_m2 >= area_threshold_m2)

                if meets_pct_threshold or meets_area_threshold:
                    all_clear_heights.extend(data['heights'])
                    all_floor_elevations.extend(data['f_elevs'])
                    all_ceiling_elevations.extend(data['c_elevs'])
                    total_included_pct += pct
                    total_included_area += area_m2
                    if debug:
                        if meets_pct_threshold:
                            print(f"Clear height {key_str}: {area_m2:.2f} m² ({pct:.2f}%) -> INCLUDED (>= {area_threshold_pct}%)")
                        else:
                            print(f"Clear height {key_str}: {area_m2:.2f} m² ({pct:.2f}%) -> INCLUDED (>= {area_threshold_m2} m²)")
                else:
                    total_excluded_pct += pct
                    total_excluded_area += area_m2
                    if debug:
                        reason = f"< {area_threshold_pct}%"
                        if area_threshold_m2 is not None:
                            reason += f" and < {area_threshold_m2} m²"
                        print(f"Clear height {key_str}: {area_m2:.2f} m² ({pct:.2f}%) -> EXCLUDED ({reason})")

            if debug and (height_areas or filtered_out_pct > 0):
                total_sum_pct = total_included_pct + total_excluded_pct + filtered_out_pct
                total_sum_area = total_included_area + total_excluded_area + filtered_out_area
                print("---")
                print(f"Included: {total_included_area:.2f} m² ({total_included_pct:.2f}%)")
                excl_reason = f"< {area_threshold_pct}%"
                if area_threshold_m2 is not None:
                    excl_reason += f" and < {area_threshold_m2} m²"
                print(f"Excluded ({excl_reason}): {total_excluded_area:.2f} m² ({total_excluded_pct:.2f}%)")
                if filtered_out_pct > 0:
                    print(f"Filtered (Ignored): {filtered_out_area:.2f} m² ({filtered_out_pct:.2f}%)")
                print(f"Sum:      {total_sum_area:.2f} m² ({total_sum_pct:.2f}%)")
                print(f"Room Area:{room_area_m2:.2f} m²")

            if all_clear_heights:
                min_ch = round_to_half_cm(min(all_clear_heights))
                max_ch = round_to_half_cm(max(all_clear_heights))

                is_range = abs(max_ch - min_ch) >= 0.005

                if not is_range:
                    result = f"{format_height_value(min_ch)} m"
                else:
                    result = f"{format_height_value(min_ch)} m - {format_height_value(max_ch)} m"

                if debug:
                    print("\n--- Clear Heights Summary ---")
                    print(f"Evaluated {len(all_clear_heights)} valid points across all intersecting floor-ceiling pairs.")
                    print(f"Resulting Clear Height: {result}")
                    if is_range and dominant_height_result:
                        print(f"Dominant Clear Height (>= {dominant_area_pct_threshold}% and >= {dominant_n_times}x others): {dominant_height_result}")

                if clear_height_param_name:
                    param = room.LookupParameter(clear_height_param_name)
                    if param:
                        param.Set(result)
                        if debug:
                            print(f"Successfully updated parameter '{clear_height_param_name}'.")
                    else:
                        if debug:
                            print(f"Parameter '{clear_height_param_name}' not found.")

                if dominant_clear_height_param_name:
                    param_dom = room.LookupParameter(dominant_clear_height_param_name)
                    if param_dom:
                        if is_range and dominant_height_result:
                            param_dom.Set(dominant_height_result)
                            if debug:
                                print(f"Successfully updated parameter '{dominant_clear_height_param_name}'.")
                        else:
                            param_dom.Set("")
                            if debug:
                                print(f"No dominant clear height or single height room. Cleared parameter '{dominant_clear_height_param_name}'.")
                    else:
                        if debug:
                            print(f"Parameter '{dominant_clear_height_param_name}' not found.")

                if lower_elevation_param_name and all_floor_elevations:
                    min_floor = round_to_half_cm(min(all_floor_elevations))
                    max_floor = round_to_half_cm(max(all_floor_elevations))
                    is_floor_range = abs(max_floor - min_floor) >= 0.005
                    lower_result = f"{format_height_value(min_floor)} m" if not is_floor_range else f"{format_height_value(min_floor)} m - {format_height_value(max_floor)} m"
                    param_lower = room.LookupParameter(lower_elevation_param_name)
                    if param_lower:
                        param_lower.Set(lower_result)
                        if debug:
                            print(f"Successfully updated parameter '{lower_elevation_param_name}'.")
                    else:
                        if debug:
                            print(f"Parameter '{lower_elevation_param_name}' not found.")

                if upper_elevation_param_name and all_ceiling_elevations:
                    min_ceil = round_to_half_cm(min(all_ceiling_elevations))
                    max_ceil = round_to_half_cm(max(all_ceiling_elevations))
                    is_ceil_range = abs(max_ceil - min_ceil) >= 0.005
                    upper_result = f"{format_height_value(min_ceil)} m" if not is_ceil_range else f"{format_height_value(min_ceil)} m - {format_height_value(max_ceil)} m"
                    param_upper = room.LookupParameter(upper_elevation_param_name)
                    if param_upper:
                        param_upper.Set(upper_result)
                        if debug:
                            print(f"Successfully updated parameter '{upper_elevation_param_name}'.")
                    else:
                        if debug:
                            print(f"Parameter '{upper_elevation_param_name}' not found.")
            else:
                if debug:
                    print("\nNo valid clear heights could be evaluated at the intersection vertices.")

        t.Commit()

    except Exception as e:
        t.RollBack()
        if debug:
            print(f"Error processing intersections: {e}")
        raise


def main(debug=False, create_debug_lines=False, create_ray_lines=False,
         grid_spacing_min_m=0.5, grid_spacing_max_m=1.5,
         area_min_m2=10.0, area_max_m2=100.0, area_threshold_pct=5.0,
         filter_name=None, area_threshold_m2=10,
         dominant_area_pct_threshold=50.0, dominant_n_times=2.0,
         ray_length_multiplicator=1.5,
         clear_height_param_name="Lichte_Raumhöhe",
         dominant_clear_height_param_name="Lichte_Raumhöhe_Bestimmend",
         ray_casting_view_name="RAY_CASTING_VIEW",
         lower_elevation_param_name=None, upper_elevation_param_name=None,
         range_delimiter="-"):
    """
    Standard entry point function, delegating to calculate_clear_heights.
    """
    return calculate_clear_heights(
        debug=debug,
        create_debug_lines=create_debug_lines,
        create_ray_lines=create_ray_lines,
        grid_spacing_min_m=grid_spacing_min_m,
        grid_spacing_max_m=grid_spacing_max_m,
        area_min_m2=area_min_m2,
        area_max_m2=area_max_m2,
        area_threshold_pct=area_threshold_pct,
        filter_name=filter_name,
        area_threshold_m2=area_threshold_m2,
        dominant_area_pct_threshold=dominant_area_pct_threshold,
        dominant_n_times=dominant_n_times,
        ray_length_multiplicator=ray_length_multiplicator,
        clear_height_param_name=clear_height_param_name,
        dominant_clear_height_param_name=dominant_clear_height_param_name,
        ray_casting_view_name=ray_casting_view_name,
        lower_elevation_param_name=lower_elevation_param_name,
        upper_elevation_param_name=upper_elevation_param_name,
        range_delimiter=range_delimiter
    )

