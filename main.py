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

# Base grid spacing in feet (approximately 0.5m = ~1.64 feet)
# This serves as the finest grid spacing - coarser grids will be multiples of this
BASE_GRID_SPACING = 0.328 * 30

# Minimum distance from boundary to avoid edge issues (in feet, ~10cm)
BOUNDARY_INSET = 0.328

# Threshold for filtering clear heights (7.6% = 0.076)
# Heights representing less than 7.6% of the room area are filtered out
THRESHOLD = 0.076

# Convergence threshold for adaptive grid refinement
# If height ratios differ by less than this, consider converged
CONVERGENCE_THRESHOLD = 0.005  # 2% difference allowed

# Maximum grid refinement iterations
MAX_REFINEMENT_ITERATIONS = 4

# Initial coarse grid multiplier (start with grid this many times larger than base)
INITIAL_GRID_MULTIPLIER = 4

# Initial coarse grid multiplier (start with grid this many times larger than base)
MINIMAL_GRID_MULTIPLIER = 0.5

def get_room_boundary_points(room, options):
    """Extract boundary curves from a room and return as list of boundary loops.
    
    Returns:
        List of boundary loops, where each loop is a list of points.
        The first loop is the outer boundary, subsequent loops are holes.
    """
    boundary_segments = room.GetBoundarySegments(options)
    if not boundary_segments or boundary_segments.Count == 0:
        return []
    
    boundary_loops = []
    for segment_list in boundary_segments:
        loop_points = []
        for segment in segment_list:
            curve = segment.GetCurve()
            loop_points.append(curve.GetEndPoint(0))
        if loop_points:
            boundary_loops.append(loop_points)
    return boundary_loops


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


def min_distance_to_boundary(px, py, boundary_loops):
    """Calculate minimum distance from point to any boundary edge in any loop."""
    min_dist = float('inf')
    for loop in boundary_loops:
        n = len(loop)
        for i in range(n):
            x1, y1 = loop[i].X, loop[i].Y
            x2, y2 = loop[(i + 1) % n].X, loop[(i + 1) % n].Y
            dist = point_to_segment_distance(px, py, x1, y1, x2, y2)
            if dist < min_dist:
                min_dist = dist
    return min_dist


def calculate_height_ratios(clear_heights, tolerance=0.05):
    """Calculate the ratio distribution of clear heights.
    
    Groups heights within tolerance and returns a dictionary of 
    representative height -> ratio (percentage of total).
    
    Args:
        clear_heights: List of clear heights in meters
        tolerance: Heights within this tolerance are grouped together
        
    Returns:
        Dictionary mapping representative height to its ratio (0-1)
    """
    if not clear_heights:
        return {}
    
    sorted_heights = sorted(clear_heights)
    
    # Create groups of similar heights
    groups = []
    current_group = [sorted_heights[0]]
    
    for h in sorted_heights[1:]:
        if h - current_group[0] <= tolerance:
            current_group.append(h)
        else:
            groups.append(current_group)
            current_group = [h]
    groups.append(current_group)
    
    # Calculate ratios for each group
    total_points = len(clear_heights)
    ratios = {}
    for group in groups:
        # Use the mode (most common value) as representative
        value_counts = {}
        for v in group:
            value_counts[v] = value_counts.get(v, 0) + 1
        representative = max(value_counts.keys(), key=lambda x: value_counts[x])
        ratios[representative] = len(group) / total_points
    
    return ratios


def compare_height_ratios(ratios1, ratios2, threshold=0.02):
    """Compare two height ratio distributions for convergence.
    
    Args:
        ratios1: First ratio dictionary (height -> ratio)
        ratios2: Second ratio dictionary (height -> ratio)
        threshold: Maximum allowed difference in ratios
        
    Returns:
        True if the distributions are similar enough (converged)
    """
    if not ratios1 or not ratios2:
        return False
    
    # Get all unique heights from both distributions
    all_heights = set(ratios1.keys()) | set(ratios2.keys())
    
    # For each height, check if the ratio difference is within threshold
    for height in all_heights:
        ratio1 = ratios1.get(height, 0)
        ratio2 = ratios2.get(height, 0)
        
        # Find matching height in the other distribution (within tolerance)
        if height not in ratios1:
            # Try to find a close match
            for h1 in ratios1:
                if abs(h1 - height) <= 0.05:
                    ratio1 = ratios1[h1]
                    break
        if height not in ratios2:
            for h2 in ratios2:
                if abs(h2 - height) <= 0.05:
                    ratio2 = ratios2[h2]
                    break
        
        if abs(ratio1 - ratio2) > threshold:
            return False
    
    return True


def create_grid_points_in_polygon(boundary_loops, grid_spacing, boundary_inset, z_height):
    """Create a dense grid of points within the polygon boundary at given z height.
    
    Points are placed at least boundary_inset distance from any edge.
    Handles multiple boundary loops: first loop is outer boundary, rest are holes.
    
    Args:
        boundary_loops: List of boundary loops (each loop is a list of points)
        grid_spacing: Distance between grid points
        boundary_inset: Minimum distance from any boundary edge
        z_height: Z coordinate for all grid points
    """
    if not boundary_loops:
        return []
    
    # First loop is the outer boundary
    outer_boundary = boundary_loops[0]
    # Remaining loops are holes (inner boundaries)
    holes = boundary_loops[1:] if len(boundary_loops) > 1 else []
    
    # Find bounding box of the outer polygon
    min_x = min(p.X for p in outer_boundary)
    max_x = max(p.X for p in outer_boundary)
    min_y = min(p.Y for p in outer_boundary)
    max_y = max(p.Y for p in outer_boundary)
    
    # Generate grid points
    grid_points = []
    x = min_x + grid_spacing / 2
    while x < max_x:
        y = min_y + grid_spacing / 2
        while y < max_y:
            # Check if point is inside outer polygon
            if is_point_in_polygon(x, y, outer_boundary):
                # Check if point is outside all holes
                in_hole = False
                for hole in holes:
                    if is_point_in_polygon(x, y, hole):
                        in_hole = True
                        break
                
                if not in_hole:
                    # Check distance to all boundaries (outer + holes)
                    dist_to_boundary = min_distance_to_boundary(x, y, boundary_loops)
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


def create_debug_model_line(doc, start_point, end_point):
    """Create a model line for debugging ray paths.
    
    Creates a vertical model line by generating an appropriate sketch plane
    for each line based on its position.
    """
    from Autodesk.Revit.DB import Line, Plane, SketchPlane, XYZ
    try:
        line = Line.CreateBound(start_point, end_point)
        
        # For vertical lines, we need a vertical sketch plane
        # Create a plane that contains the line - use a horizontal normal
        # The plane normal should be perpendicular to the line direction
        line_direction = (end_point - start_point).Normalize()
        
        # For vertical lines (Z direction), use X or Y as the normal
        if abs(line_direction.Z) > 0.9:  # Nearly vertical
            # Use Y-axis as normal for the sketch plane
            plane_normal = XYZ(0, 1, 0)
        else:
            # For non-vertical lines, use Z cross line_direction
            plane_normal = XYZ.BasisZ.CrossProduct(line_direction).Normalize()
        
        plane = Plane.CreateByNormalAndOrigin(plane_normal, start_point)
        sketch_plane = SketchPlane.Create(doc, plane)
        
        model_line = doc.Create.NewModelCurve(line, sketch_plane)
        print(f"    Created debug line sucessfully: {model_line.Id.Value}")

        return model_line
    except Exception as e:
        print(f"    Error creating debug line: {e}")
        return None


def evaluate_heights_at_grid(doc, intersector, boundary_loops, grid_spacing, boundary_inset, z_height, max_ray_distance, debug=False, create_debug_lines=False):
    """Evaluate clear heights at grid points within a room boundary.
    
    This is the core evaluation function that casts rays at each grid point.
    
    Args:
        doc: Revit document
        intersector: ReferenceIntersector for ray casting
        boundary_loops: List of boundary loops from get_room_boundary_points
        grid_spacing: Distance between grid points
        boundary_inset: Minimum distance from boundary
        z_height: Z coordinate for grid points
        max_ray_distance: Maximum ray cast distance
        debug: Print debug information
        create_debug_lines: Create model lines for visualization
    
    Returns:
        List of clear heights in meters
    """
    # Create grid points within the room boundary
    grid_points = create_grid_points_in_polygon(boundary_loops, grid_spacing, boundary_inset, z_height)
    if not grid_points:
        return []
    
    if debug:
        print(f"  Generated {len(grid_points)} grid points (spacing={grid_spacing * 0.3048:.2f}m)")
    
    # Calculate clear heights at each grid point
    clear_heights = []
    up_direction = XYZ(0, 0, 1)
    down_direction = XYZ(0, 0, -1)
    
    debug_count = 0
    debug_lines_created = 0
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
            floor_point = XYZ(point.X, point.Y, point.Z + floor_top_distance)
        elif floor_below_distance is not None and ceiling_distance is not None:
            # Floor is below our starting point - measure from floor to ceiling
            clear_height = ceiling_distance + floor_below_distance
            floor_info = floor_below_info
            floor_dist = floor_below_distance
            floor_point = XYZ(point.X, point.Y, point.Z - floor_below_distance)
        else:
            clear_height = None
            floor_info = None
            floor_dist = None
            floor_point = None

        # Create debug model lines for ray paths
        if create_debug_lines and ceiling_distance is not None:
            ceiling_point = XYZ(point.X, point.Y, point.Z + ceiling_distance)
            # Create line from floor to ceiling (the clear height measurement)
            if floor_point is not None:
                create_debug_model_line(doc, floor_point, ceiling_point)
                debug_lines_created += 1

        if debug and debug_count < 3:
            ceiling_str = f"{ceiling_distance * 0.3048:.2f}" if ceiling_distance is not None else "None"
            floor_str = f"{floor_dist * 0.3048:.2f}" if floor_dist is not None else "None"
            clear_str = f"{clear_height * 0.3048:.2f}" if clear_height is not None else "None"
            print(
                f"  Point {debug_count}: ceiling={ceiling_str}m ({ceiling_info}), floor={floor_str}m ({floor_info}), clear={clear_str}m")
            debug_count += 1

        # Add clear height if valid
        if clear_height is not None and clear_height > 0:
            # Convert from feet to meters and round to 2 decimal places
            clear_height_m = round(clear_height * 0.3048, 2)
            clear_heights.append(clear_height_m)
    
    if create_debug_lines:
        print(f"  Created {debug_lines_created} debug model lines")
    
    return clear_heights


def calculate_clear_heights_for_room(doc, room, intersector, boundary_options, base_grid_spacing, boundary_inset, max_ray_distance, debug=False, create_debug_lines=False):
    """Calculate clear heights for a room using adaptive grid refinement.
    
    Starts with a coarse grid and progressively refines until the height
    distribution ratios converge, ensuring we don't miss important details
    while optimizing performance for different room sizes.
    
    Args:
        doc: Revit document
        room: Room element
        intersector: ReferenceIntersector for ray casting
        boundary_options: SpatialElementBoundaryOptions
        base_grid_spacing: Base grid spacing (finest level)
        boundary_inset: Minimum distance from boundary
        max_ray_distance: Maximum ray cast distance
        debug: Print debug information
        create_debug_lines: Create model lines for visualization
    
    Returns:
        List of clear heights in meters
    """
    # Get room's level elevation and computation height
    level = doc.GetElement(room.LevelId)
    level_elevation = level.Elevation if level else 0
    
    # Get level's computation height parameter (LEVEL_ROOM_COMPUTATION_HEIGHT)
    computation_height_param = level.get_Parameter(BuiltInParameter.LEVEL_ROOM_COMPUTATION_HEIGHT) if level else None
    computation_height = computation_height_param.AsDouble() if computation_height_param else 0
    
    # Calculate z height for grid points using level elevation + computation height
    small_offset = 0.01  # ~3mm above floor level
    z_height = level_elevation + computation_height + small_offset
    
    if debug:
        print(f"  level_elevation={level_elevation * 0.3048:.2f}m, computation_height={computation_height * 0.3048:.2f}m, z_height={z_height * 0.3048:.2f}m")
    
    # Get room boundary loops
    boundary_loops = get_room_boundary_points(room, boundary_options)
    if not boundary_loops:
        return []
    
    # Start with coarse grid and refine until convergence
    current_multiplier = INITIAL_GRID_MULTIPLIER
    previous_ratios = None
    final_heights = []
    
    for iteration in range(MAX_REFINEMENT_ITERATIONS):
        current_spacing = base_grid_spacing * current_multiplier
        
        if debug:
            print(f"  Iteration {iteration + 1}: grid_spacing={current_spacing * 0.3048:.2f}m (multiplier={current_multiplier}x)")
        
        # Evaluate heights at current grid spacing
        # Only create debug lines on the final iteration (when converged or last iteration)
        heights = evaluate_heights_at_grid(
            doc, intersector, boundary_loops, current_spacing, boundary_inset,
            z_height, max_ray_distance, debug=(debug and iteration == 0), 
            create_debug_lines=False  # We'll create lines after convergence
        )
        
        if not heights:
            if debug:
                print(f"  No heights found at iteration {iteration + 1}")
            # Try finer grid
            current_multiplier = max(MINIMAL_GRID_MULTIPLIER, current_multiplier // 2)
            continue
        
        # Calculate height ratios
        current_ratios = calculate_height_ratios(heights)
        
        if debug:
            print(f"  Ratios: {', '.join(f'{h:.2f}m:{r*100:.1f}%' for h, r in sorted(current_ratios.items()))}")
        
        # Check for convergence
        if previous_ratios is not None:
            if compare_height_ratios(previous_ratios, current_ratios, CONVERGENCE_THRESHOLD):
                if debug:
                    print(f"  Converged at iteration {iteration + 1}!")
                final_heights = heights
                break
        
        # Store for next iteration
        previous_ratios = current_ratios
        final_heights = heights
        
        # Refine grid (halve the multiplier, but don't go below 1)
        if current_multiplier <= MINIMAL_GRID_MULTIPLIER:
            if debug:
                print(f"  Reached finest grid level")
            break
        current_multiplier = max(MINIMAL_GRID_MULTIPLIER, current_multiplier // 2)
    
    # Create debug lines with the final grid spacing if requested
    if create_debug_lines and final_heights:
        if debug:
            print(f"  Creating debug lines with final grid...")
        # Re-run with debug lines enabled at the converged spacing
        final_spacing = base_grid_spacing * max(MINIMAL_GRID_MULTIPLIER, current_multiplier)
        final_heights = evaluate_heights_at_grid(
            doc, intersector, boundary_loops, final_spacing, boundary_inset,
            z_height, max_ray_distance, debug=False, create_debug_lines=True
        )
    
    return final_heights


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
            # If there's a dominant height, only include heights within 0.25m of it
            # This filters out outliers (like beams, ducts) while keeping intentional variations
            if dominant_height is not None:
                if abs(representative - dominant_height) <= 0.25:
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
def main(debug=True, create_debug_lines=False):
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
            # Set create_debug_lines=True to visualize ray paths as model lines
            clear_heights = calculate_clear_heights_for_room(
                doc, room, intersector, boundary_options,
                BASE_GRID_SPACING, BOUNDARY_INSET, max_ray_distance, 
                debug=debug, create_debug_lines=create_debug_lines
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
