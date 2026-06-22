from revit_rpc import revit

try:
    from Autodesk.Revit.DB import (
        FilteredElementCollector, View3D, ReferenceIntersector,
        FindReferenceTarget, XYZ, SpatialElementBoundaryOptions,
        SpatialElementBoundaryLocation, Transaction,
        BuiltInParameter, BuiltInCategory, ElementMulticategoryFilter
    )
    from System.Collections.Generic import List
except ImportError:
    pass

__revit__: str

# Grid spacing in feet (approximately 0.1m = ~0.328 feet)
GRID_SPACING = 0.328 * 10

# Minimum distance from boundary to avoid edge issues (in feet, ~10cm)
BOUNDARY_INSET = 0.328

# Threshold for filtering clear heights (7.6% = 0.076)
# Heights representing less than 7.6% of the room area are filtered out
THRESHOLD = 0.076


def get_room_boundary_points(room, options):
    """Extract boundary curves from a room and return as list of points."""
    boundary_segments = room.GetBoundarySegments(options)
    if not boundary_segments or boundary_segments.Count == 0:
        return []
    
    points = []
    for segment_list in boundary_segments:
        for segment in segment_list:
            curve = segment.GetCurve()
            points.append(curve.GetEndPoint(0))
    return points


def is_point_in_polygon(px, py, polygon_points):
    """Check if a point is inside a polygon using ray casting algorithm."""
    n = len(polygon_points)
    if n < 3:
        return False
    
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon_points[i].X, polygon_points[i].Y
        xj, yj = polygon_points[j].X, polygon_points[j].Y
        
        if ((yi > py) != (yj > py)) and \
           (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    
    return inside


def point_to_segment_distance(px, py, x1, y1, x2, y2):
    """Calculate distance from point to line segment."""
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return ((px - proj_x) ** 2 + (py - proj_y) ** 2) ** 0.5


def min_distance_to_boundary(px, py, boundary_points):
    """Calculate minimum distance from point to any boundary edge."""
    min_dist = float('inf')
    n = len(boundary_points)
    for i in range(n):
        x1, y1 = boundary_points[i].X, boundary_points[i].Y
        x2, y2 = boundary_points[(i + 1) % n].X, boundary_points[(i + 1) % n].Y
        dist = point_to_segment_distance(px, py, x1, y1, x2, y2)
        if dist < min_dist:
            min_dist = dist
    return min_dist


def create_grid_points_in_polygon(boundary_points, grid_spacing, boundary_inset, z_height):
    """Create a dense grid of points within the polygon boundary at given z height.
    
    Points are placed at least boundary_inset distance from any edge.
    """
    if not boundary_points:
        return []
    
    # Find bounding box of the polygon
    min_x = min(p.X for p in boundary_points)
    max_x = max(p.X for p in boundary_points)
    min_y = min(p.Y for p in boundary_points)
    max_y = max(p.Y for p in boundary_points)
    
    # Generate grid points
    grid_points = []
    x = min_x + grid_spacing / 2
    while x < max_x:
        y = min_y + grid_spacing / 2
        while y < max_y:
            # Check if point is inside polygon and far enough from boundary
            if is_point_in_polygon(x, y, boundary_points):
                dist_to_boundary = min_distance_to_boundary(x, y, boundary_points)
                if dist_to_boundary >= boundary_inset:
                    grid_points.append(XYZ(x, y, z_height))
            y += grid_spacing
        x += grid_spacing
    
    return grid_points


def get_3d_view_by_name(doc, view_name):
    """Get a 3D view by its name."""
    collector = FilteredElementCollector(doc).OfClass(View3D)
    for view in collector:
        if view.Name == view_name and not view.IsTemplate:
            return view
    return None


def find_closest_room_bounding_hit(doc, refs, max_distance, require_upward_normal, debug=False):
    """Find the closest hit on a room-bounding element with correct face normal.
    
    Args:
        doc: Revit document
        refs: List of ReferenceWithContext from ray cast
        max_distance: Maximum distance to consider
        require_upward_normal: If True, require face normal Z > 0; if False, require Z < 0
        debug: If True, print debug information
    
    Returns:
        Tuple of (distance, element_info) or (None, None) if no valid hit
    """
    closest_distance = None
    elem_info = None
    
    for ref_with_context in refs:
        ref = ref_with_context.GetReference()
        if ref is None:
            continue
            
        proximity = ref_with_context.Proximity
        
        # Skip if we already found a closer valid hit
        if closest_distance is not None and proximity >= closest_distance:
            continue
        
        # Filter by max distance
        if proximity > max_distance:
            continue
        
        # Check if element is room bounding
        elem_id = ref.ElementId
        elem = doc.GetElement(elem_id)
        if not elem:
            continue
            
        param = elem.get_Parameter(BuiltInParameter.WALL_ATTR_ROOM_BOUNDING)
        if not param or param.AsInteger() != 1:
            if debug:
                print(f"    Skipped {elem.Category.Name} (ID: {elem_id.Value}) - not room bounding, dist={proximity * 0.3048:.2f}m")
            continue
        
        # Check face normal direction
        normal_z = None
        try:
            face = elem.GetGeometryObjectFromReference(ref)
            if face:
                uv = ref.UVPoint
                if uv:
                    normal = face.ComputeNormal(uv)
                else:
                    normal = face.ComputeNormal(face.GetBoundingBox().Min)
                normal_z = normal.Z
                
                # Check normal direction
                if require_upward_normal and normal.Z <= 0:
                    if debug:
                        print(f"    Skipped {elem.Category.Name} (ID: {elem_id.Value}) - wrong normal Z={normal.Z:.2f}, dist={proximity * 0.3048:.2f}m")
                    continue
                if not require_upward_normal and normal.Z >= 0:
                    if debug:
                        print(f"    Skipped {elem.Category.Name} (ID: {elem_id.Value}) - wrong normal Z={normal.Z:.2f}, dist={proximity * 0.3048:.2f}m")
                    continue
        except:
            pass  # If we can't check normal, accept the hit
        
        if debug:
            print(f"    Accepted {elem.Category.Name} (ID: {elem_id.Value}) - normal Z={normal_z}, dist={proximity * 0.3048:.2f}m")
        
        # This is a valid hit
        closest_distance = proximity
        elem_info = f"{elem.Category.Name} (ID: {elem_id.Value}, RoomBounding=True)"
    
    return closest_distance, elem_info


def calculate_clear_heights_for_room(doc, room, intersector, boundary_options, grid_spacing, boundary_inset, max_ray_distance, debug=False):
    """Calculate clear heights at grid points within a room.
    
    Returns:
        List of clear heights in meters
    """
    # Get room's level elevation
    level = doc.GetElement(room.LevelId)
    level_elevation = level.Elevation if level else 0
    
    # Get room's base offset
    base_offset_param = room.get_Parameter(BuiltInParameter.ROOM_LOWER_OFFSET)
    base_offset = base_offset_param.AsDouble() if base_offset_param else 0
    
    # Calculate z height for grid points (slightly above floor level)
    small_offset = 0.01  # ~3mm above floor level
    z_height = level_elevation + base_offset + small_offset
    
    if debug:
        print(f"  level_elevation={level_elevation * 0.3048:.2f}m, base_offset={base_offset * 0.3048:.2f}m, z_height={z_height * 0.3048:.2f}m")
    
    # Get room boundary points
    boundary_points = get_room_boundary_points(room, boundary_options)
    if not boundary_points:
        return []
    
    # Create grid points within the room boundary
    grid_points = create_grid_points_in_polygon(boundary_points, grid_spacing, boundary_inset, z_height)
    if not grid_points:
        return []
    
    if debug:
        print(f"  Generated {len(grid_points)} grid points")
    
    # Calculate clear heights at each grid point
    clear_heights = []
    up_direction = XYZ(0, 0, 1)
    down_direction = XYZ(0, 0, -1)
    
    debug_count = 0
    for point in grid_points:
        should_debug = debug and debug_count < 1
        
        if should_debug:
            print(f"  --- Point {debug_count} at z={point.Z * 0.3048:.2f}m ---")
            print(f"  Casting UP:")
        
        # Cast ray upward - we need to find both:
        # 1. The floor's top surface (upward-facing normal) - this is where we measure FROM
        # 2. The ceiling's bottom surface (downward-facing normal) - this is where we measure TO
        up_refs = intersector.Find(point, up_direction)
        
        # Find ceiling (downward-facing normal)
        ceiling_distance, ceiling_info = find_closest_room_bounding_hit(doc, up_refs, max_ray_distance, require_upward_normal=False, debug=should_debug)
        
        # Find floor top surface above us (upward-facing normal) - this handles cases where
        # we start slightly below the floor surface
        floor_top_distance, floor_top_info = find_closest_room_bounding_hit(doc, up_refs, max_ray_distance, require_upward_normal=True, debug=should_debug)
        
        if should_debug:
            print(f"  Casting DOWN:")
        
        # Cast ray downward - find floor's top surface (upward-facing normal)
        down_refs = intersector.Find(point, down_direction)
        floor_below_distance, floor_below_info = find_closest_room_bounding_hit(doc, down_refs, max_ray_distance, require_upward_normal=True, debug=should_debug)
        
        # Calculate clear height
        # If there's a floor surface above us (we started below it) AND it's closer than the ceiling,
        # use that as the floor reference. Otherwise, use the floor surface below us.
        if floor_top_distance is not None and ceiling_distance is not None and floor_top_distance < ceiling_distance:
            # Floor top is above our starting point but below the ceiling - measure from floor top to ceiling
            clear_height = ceiling_distance - floor_top_distance
            floor_info = floor_top_info
            floor_dist = -floor_top_distance  # negative because it's above us
        elif floor_below_distance is not None and ceiling_distance is not None:
            # Floor is below our starting point - measure from floor to ceiling
            clear_height = ceiling_distance + floor_below_distance
            floor_info = floor_below_info
            floor_dist = floor_below_distance
        else:
            clear_height = None
            floor_info = None
            floor_dist = None
        
        if debug and debug_count < 3:
            ceiling_m = ceiling_distance * 0.3048 if ceiling_distance else None
            floor_m = floor_dist * 0.3048 if floor_dist is not None else None
            clear_m = clear_height * 0.3048 if clear_height else None
            print(f"  Point {debug_count}: ceiling={ceiling_m:.2f}m ({ceiling_info}), floor={floor_m:.2f}m ({floor_info}), clear={clear_m:.2f}m")
            debug_count += 1
        
        # Add clear height if valid
        if clear_height is not None and clear_height > 0:
            # Convert from feet to meters and round to 2 decimal places
            clear_height_m = round(clear_height * 0.3048, 2)
            clear_heights.append(clear_height_m)
    
    return clear_heights


def filter_heights_by_threshold(clear_heights, threshold, debug=False):
    """Filter clear heights by threshold percentage.
    
    Heights within 0.05m tolerance are grouped together.
    Heights representing less than threshold percentage of total points are filtered out.
    
    Special case: If one height group is dominant (>50%), only that height is returned
    unless other heights are within 0.20m of it (to capture intentional height variations).
    
    Returns:
        List of filtered heights (representative value for each group)
    """
    if not clear_heights:
        return []
    
    # Group heights within tolerance (0.05m)
    tolerance = 0.05
    sorted_heights = sorted(clear_heights)
    
    # Create groups of similar heights
    groups = []
    current_group = [sorted_heights[0]]
    
    for h in sorted_heights[1:]:
        # Check if this height is within tolerance of the group's first element
        if h - current_group[0] <= tolerance:
            current_group.append(h)
        else:
            groups.append(current_group)
            current_group = [h]
    groups.append(current_group)
    
    # For each group, use the most common value (mode) as representative
    # and count total occurrences
    group_info = []
    for group in groups:
        # Find mode (most common value) in the group
        value_counts = {}
        for v in group:
            value_counts[v] = value_counts.get(v, 0) + 1
        mode_value = max(value_counts.keys(), key=lambda x: value_counts[x])
        group_info.append((mode_value, len(group)))
    
    total_points = len(clear_heights)
    
    if debug:
        print(f"  Height distribution ({total_points} points, threshold={threshold*100:.1f}%):")
        for representative, count in sorted(group_info):
            pct = count / total_points * 100
            print(f"    {representative:.2f}m: {count} points ({pct:.1f}%)")
    
    # Check for dominant height (>50%)
    dominant_height = None
    dominant_count = 0
    for representative, count in group_info:
        percentage = count / total_points
        if percentage > 0.50:
            dominant_height = representative
            dominant_count = count
            break
    
    # Filter heights by threshold
    filtered_heights = []
    for representative, count in group_info:
        percentage = count / total_points
        if percentage >= threshold:
            # If there's a dominant height, only include heights within 0.20m of it
            # This filters out outliers (like beams, ducts) while keeping intentional variations
            if dominant_height is not None:
                if abs(representative - dominant_height) <= 0.20:
                    filtered_heights.append(representative)
            else:
                filtered_heights.append(representative)
    
    # If all heights are filtered out, use the dominant height or all heights
    if not filtered_heights:
        if dominant_height is not None:
            filtered_heights = [dominant_height]
        else:
            filtered_heights = [rep for rep, _ in group_info]
    
    if debug:
        print(f"  Dominant height: {dominant_height}")
        print(f"  Filtered heights: {sorted(filtered_heights)}")
    
    return filtered_heights


def format_clear_height_result(filtered_heights):
    """Format clear heights as string with German locale (comma as decimal separator).
    
    Returns single value if all heights are the same, or range if different.
    """
    if not filtered_heights:
        return ""
    
    unique_heights = sorted(set(filtered_heights))
    
    if len(unique_heights) == 1:
        return f"{unique_heights[0]:.2f}m".replace(".", ",")
    else:
        min_height = min(unique_heights)
        max_height = max(unique_heights)
        return f"{min_height:.2f}m - {max_height:.2f}m".replace(".", ",")


@revit
def main():
    doc = __revit__.ActiveUIDocument.Document
    uidoc = __revit__.ActiveUIDocument
    
    # Get selected elements
    selection = uidoc.Selection.GetElementIds()
    if selection.Count == 0:
        print("No elements selected.")
        return
    
    # Get the 3D view for ray casting
    view_3d = get_3d_view_by_name(doc, "RAY_CASTING_VIEW")
    if view_3d is None:
        print("3D view 'RAY_CASTING_VIEW' not found.")
        return
    
    # Create category filter for Floor, Ceiling, and Roof
    categories = List[BuiltInCategory]()
    categories.Add(BuiltInCategory.OST_Floors)
    categories.Add(BuiltInCategory.OST_Ceilings)
    categories.Add(BuiltInCategory.OST_Roofs)
    category_filter = ElementMulticategoryFilter(categories)
    
    # Create reference intersector
    intersector = ReferenceIntersector(category_filter, FindReferenceTarget.Face, view_3d)
    
    # Boundary options
    boundary_options = SpatialElementBoundaryOptions()
    boundary_options.SpatialElementBoundaryLocation = SpatialElementBoundaryLocation.Finish
    
    # Start transaction
    t = Transaction(doc, "Calculate Clear Heights")
    t.Start()
    
    try:
        for element_id in selection:
            room = doc.GetElement(element_id)
            
            # Check if it's a room
            if room.Category is None or room.Category.Id.Value != int(BuiltInCategory.OST_Rooms):
                continue
            
            # Get room's unbounded height for max ray distance
            unbounded_height_param = room.get_Parameter(BuiltInParameter.ROOM_HEIGHT)
            unbounded_height = unbounded_height_param.AsDouble() if unbounded_height_param else 10
            max_ray_distance = unbounded_height * 1.5
            
            # Calculate clear heights for this room
            clear_heights = calculate_clear_heights_for_room(
                doc, room, intersector, boundary_options,
                GRID_SPACING, BOUNDARY_INSET, max_ray_distance, debug=True
            )
            
            if not clear_heights:
                print(f"Room {room.Id} has no valid clear heights.")
                continue
            
            # Filter heights by threshold
            filtered_heights = filter_heights_by_threshold(clear_heights, THRESHOLD, debug=True)
            
            # Format result
            result = format_clear_height_result(filtered_heights)
            
            # Write to parameter
            param = room.LookupParameter("Lichte_Raumhöhe")
            if param:
                param.Set(result)
                print(f"Room {room.Id}: {result}")
            else:
                print(f"Room {room.Id}: Parameter 'Lichte_Raumhöhe' not found.")
        
        t.Commit()
        print("Command executed successfully.")
    except Exception as e:
        t.RollBack()
        print(f"Error during execution: {e}")
        raise


if __name__ == "__main__":
    main()
