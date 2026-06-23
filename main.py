from revit_rpc import revit
import numpy as np
from matplotlib.path import Path

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

# Grid spacing in feet (approximately 1m = ~3.28 feet)
# Larger spacing = fewer points = faster computation
GRID_SPACING = 3.28 * 0.8

# Minimum distance from boundary to avoid edge issues (in feet, ~10cm)
BOUNDARY_INSET = 0.328

# Angle tolerance for boundary simplification (in radians, ~5 degrees)
# Points where the angle deviation is less than this are considered collinear
SIMPLIFICATION_ANGLE_TOLERANCE = 0.087

# Threshold for filtering clear heights (7.6% = 0.076)
# Heights representing less than 7.6% of the room area are filtered out
THRESHOLD = 0.076

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


def simplify_boundary_loop(points, angle_tolerance):
    """Simplify a boundary loop by removing nearly-collinear points.
    
    Uses the Douglas-Peucker-like approach based on angle deviation.
    Points where the angle between incoming and outgoing edges is nearly 180°
    (i.e., the point is nearly collinear) are removed.
    
    Args:
        points: List of XYZ points forming a closed loop
        angle_tolerance: Maximum angle deviation (in radians) to consider collinear
        
    Returns:
        Simplified list of XYZ points
    """
    import math
    
    if len(points) <= 3:
        return points
    
    n = len(points)
    keep = [True] * n
    
    for i in range(n):
        if not keep[i]:
            continue
            
        # Find previous kept point
        prev_idx = (i - 1) % n
        while not keep[prev_idx] and prev_idx != i:
            prev_idx = (prev_idx - 1) % n
        
        # Find next kept point
        next_idx = (i + 1) % n
        while not keep[next_idx] and next_idx != i:
            next_idx = (next_idx + 1) % n
        
        if prev_idx == i or next_idx == i:
            continue
        
        p_prev = points[prev_idx]
        p_curr = points[i]
        p_next = points[next_idx]
        
        # Calculate vectors
        v1x = p_curr.X - p_prev.X
        v1y = p_curr.Y - p_prev.Y
        v2x = p_next.X - p_curr.X
        v2y = p_next.Y - p_curr.Y
        
        # Calculate lengths
        len1 = math.sqrt(v1x * v1x + v1y * v1y)
        len2 = math.sqrt(v2x * v2x + v2y * v2y)
        
        if len1 < 1e-10 or len2 < 1e-10:
            keep[i] = False
            continue
        
        # Normalize
        v1x, v1y = v1x / len1, v1y / len1
        v2x, v2y = v2x / len2, v2y / len2
        
        # Calculate dot product (cos of angle between vectors)
        dot = v1x * v2x + v1y * v2y
        
        # Clamp to avoid numerical issues with acos
        dot = max(-1.0, min(1.0, dot))
        
        # Angle between vectors (0 = same direction = collinear)
        angle = math.acos(dot)
        
        # If angle is very small (vectors nearly parallel), point is collinear
        if angle < angle_tolerance:
            keep[i] = False
    
    # Build simplified list
    simplified = [points[i] for i in range(n) if keep[i]]
    
    # Ensure we have at least 3 points
    if len(simplified) < 3:
        return points
    
    return simplified


def simplify_boundary_loops(boundary_loops, angle_tolerance):
    """Simplify all boundary loops.
    
    Args:
        boundary_loops: List of boundary loops (each is a list of XYZ points)
        angle_tolerance: Maximum angle deviation to consider collinear
        
    Returns:
        List of simplified boundary loops
    """
    return [simplify_boundary_loop(loop, angle_tolerance) for loop in boundary_loops]


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


def calculate_inset_direction(p_prev, p_curr, p_next):
    """Calculate the inward direction at a corner point (bisector of the angle).
    
    Args:
        p_prev: Previous point on boundary
        p_curr: Current point (corner)
        p_next: Next point on boundary
        
    Returns:
        Tuple (dx, dy) representing the normalized inward direction
    """
    # Vectors along edges
    v1x = p_curr.X - p_prev.X
    v1y = p_curr.Y - p_prev.Y
    v2x = p_next.X - p_curr.X
    v2y = p_next.Y - p_curr.Y
    
    # Normalize vectors
    len1 = (v1x**2 + v1y**2)**0.5
    len2 = (v2x**2 + v2y**2)**0.5
    
    if len1 < 1e-10 or len2 < 1e-10:
        return (0, 0)
    
    v1x, v1y = v1x/len1, v1y/len1
    v2x, v2y = v2x/len2, v2y/len2
    
    # Inward normals (rotate 90 degrees clockwise for outer boundary)
    n1x, n1y = v1y, -v1x
    n2x, n2y = v2y, -v2x
    
    # Bisector direction (average of the two normals)
    bx = n1x + n2x
    by = n1y + n2y
    blen = (bx**2 + by**2)**0.5
    
    if blen < 1e-10:
        return (n1x, n1y)
    
    return (bx/blen, by/blen)


def inset_boundary_loop(boundary_points, inset_distance):
    """Create an inset version of a boundary loop.
    
    Args:
        boundary_points: List of XYZ points forming the boundary
        inset_distance: Distance to inset inward
        
    Returns:
        List of XYZ points forming the inset boundary, or empty list if collapsed
    """
    n = len(boundary_points)
    if n < 3:
        return []
    
    inset_points = []
    for i in range(n):
        p_prev = boundary_points[(i - 1) % n]
        p_curr = boundary_points[i]
        p_next = boundary_points[(i + 1) % n]
        
        dx, dy = calculate_inset_direction(p_prev, p_curr, p_next)
        
        # Move point inward
        new_x = p_curr.X + dx * inset_distance
        new_y = p_curr.Y + dy * inset_distance
        inset_points.append((new_x, new_y))
    
    # Check if the inset polygon is still valid (not self-intersecting or collapsed)
    # Simple check: ensure area is positive and reasonable
    area = 0
    for i in range(len(inset_points)):
        x1, y1 = inset_points[i]
        x2, y2 = inset_points[(i + 1) % len(inset_points)]
        area += (x2 - x1) * (y2 + y1)
    area = abs(area) / 2
    
    # If area is too small, the polygon has collapsed
    if area < inset_distance * inset_distance:
        return []
    
    return inset_points


def generate_points_along_boundary(boundary_points, segment_length, z_height):
    """Generate points along a boundary by dividing the entire perimeter into equal segments.
    
    Args:
        boundary_points: List of (x, y) tuples or XYZ points forming a closed loop
        segment_length: Target distance between points
        z_height: Z coordinate for all points
        
    Returns:
        List of XYZ points along the boundary
    """
    points = []
    n = len(boundary_points)
    if n < 2:
        return points
        
    # Get total perimeter and edge lengths
    edges = []
    total_perimeter = 0
    for i in range(n):
        p1 = boundary_points[i]
        p2 = boundary_points[(i + 1) % n]
        
        x1 = p1.X if hasattr(p1, 'X') else p1[0]
        y1 = p1.Y if hasattr(p1, 'Y') else p1[1]
        x2 = p2.X if hasattr(p2, 'X') else p2[0]
        y2 = p2.Y if hasattr(p2, 'Y') else p2[1]
        
        length = ((x2 - x1)**2 + (y2 - y1)**2)**0.5
        if length > 1e-10:
            edges.append({'p1': (x1, y1), 'p2': (x2, y2), 'len': length})
            total_perimeter += length
            
    if not edges or total_perimeter < 1e-10:
        return points
        
    # Pre-calculate cumulative distances
    cum_distances = [0.0]
    accum = 0
    for edge in edges:
        accum += edge['len']
        cum_distances.append(accum)
        
    # Target number of points based on perimeter
    num_points = max(1, int(round(total_perimeter / segment_length)))
    # Actual spacing to close the loop perfectly
    actual_spacing = total_perimeter / num_points
    
    current_edge_idx = 0
    
    for i in range(num_points):
        # Current distance from start of perimeter
        target_dist = i * actual_spacing
        
        # Find the edge that contains this distance
        # We can keep incrementing current_edge_idx since target_dist is non-decreasing
        while current_edge_idx < len(edges) and cum_distances[current_edge_idx + 1] < target_dist - 1e-8:
            current_edge_idx += 1
            
        if current_edge_idx >= len(edges):
             current_edge_idx = len(edges) - 1
             
        # Interpolate point on edge
        edge = edges[current_edge_idx]
        edge_start_dist = cum_distances[current_edge_idx]
        dist_on_edge = target_dist - edge_start_dist
        
        t = max(0, min(1, dist_on_edge / edge['len']))
        px = edge['p1'][0] + (edge['p2'][0] - edge['p1'][0]) * t
        py = edge['p1'][1] + (edge['p2'][1] - edge['p1'][1]) * t
        
        points.append(XYZ(px, py, z_height))
        
    return points


def create_grid_points_in_polygon(boundary_loops, inset_step_distance, segment_length, boundary_inset, z_height):
    """Create points within the polygon using boundary inset approach.
    
    Points are generated by:
    1. Starting at the boundary (inset by boundary_inset)
    2. Creating points along the entire boundary (spaced by segment_length)
    3. Insetting the boundary further by inset_step_distance and repeating
    
    Args:
        boundary_loops: List of boundary loops (each loop is a list of points)
        inset_step_distance: Distance between inset levels
        segment_length: Target distance between points along the perimeter
        boundary_inset: Initial inset distance from boundary
        z_height: Z coordinate for all grid points
    """
    if not boundary_loops:
        return []
    
    # First loop is the outer boundary
    outer_boundary = boundary_loops[0]
    # Remaining loops are holes (inner boundaries)
    holes = boundary_loops[1:] if len(boundary_loops) > 1 else []
    
    all_points = []
    
    # Start with the outer boundary
    current_boundary = [(p.X, p.Y) for p in outer_boundary]
    
    # Check if boundary points are clockwise or counter-clockwise
    area = 0
    for i in range(len(current_boundary)):
        p1 = current_boundary[i]
        p2 = current_boundary[(i + 1) % len(current_boundary)]
        area += (p2[0] - p1[0]) * (p2[1] + p1[1])
    
    # If area > 0, it's clockwise, which might mean our inset logic (which expects CCW for inward)
    # is actually outsetting. Let's ensure it's CCW.
    if area > 0:
        current_boundary.reverse()
    
    # Calculate initial perimeter for check
    perimeter = 0
    for i in range(len(current_boundary)):
        x1, y1 = current_boundary[i]
        x2, y2 = current_boundary[(i + 1) % len(current_boundary)]
        perimeter += ((x2 - x1)**2 + (y2 - y1)**2)**0.5
    
    # Continue insetting until boundary collapses or becomes larger than original
    initial_perimeter = perimeter
    level = 0
    previous_perimeter = initial_perimeter
    
    while True:
        # Calculate inset distance for this level
        current_inset_dist = boundary_inset + level * inset_step_distance
        
        # Inset the boundary from original
        inset_boundary = inset_boundary_loop_tuples(current_boundary, current_inset_dist)
        
        if not inset_boundary:
            break
        
        # Recalculate perimeter for this level
        level_perimeter = 0
        for i in range(len(inset_boundary)):
            x1, y1 = inset_boundary[i]
            x2, y2 = inset_boundary[(i + 1) % len(inset_boundary)]
            level_perimeter += ((x2 - x1)**2 + (y2 - y1)**2)**0.5
        
        # Safety check: if inset boundary is larger than original or previous
        if level > 0 and (level_perimeter > initial_perimeter or level_perimeter > previous_perimeter):
            break
        
        previous_perimeter = level_perimeter
        
        # Generate points along this inset boundary
        boundary_points = generate_points_along_boundary(inset_boundary, segment_length, z_height)
        
        # Filter out points that are inside holes
        for point in boundary_points:
            in_hole = False
            for hole in holes:
                if is_point_in_polygon(point.X, point.Y, hole):
                    in_hole = True
                    break
            
            if not in_hole:
                # Also check minimum distance to holes
                if holes:
                    min_dist_to_hole = float('inf')
                    for hole in holes:
                        dist = min_distance_to_boundary_single(point.X, point.Y, hole)
                        min_dist_to_hole = min(min_dist_to_hole, dist)
                    if min_dist_to_hole < boundary_inset:
                        continue
                
                all_points.append(point)
        
        level += 1
    
    return all_points


def inset_boundary_loop_tuples(boundary_tuples, inset_distance):
    """Create an inset version of a boundary loop (using tuples).
    
    Args:
        boundary_tuples: List of (x, y) tuples forming the boundary
        inset_distance: Distance to inset inward
        
    Returns:
        List of (x, y) tuples forming the inset boundary, or empty list if collapsed
    """
    n = len(boundary_tuples)
    if n < 3:
        return []
    
    inset_points = []
    for i in range(n):
        p_prev = boundary_tuples[(i - 1) % n]
        p_curr = boundary_tuples[i]
        p_next = boundary_tuples[(i + 1) % n]
        
        # Vectors along edges
        v1x = p_curr[0] - p_prev[0]
        v1y = p_curr[1] - p_prev[1]
        v2x = p_next[0] - p_curr[0]
        v2y = p_next[1] - p_curr[1]
        
        # Normalize vectors
        len1 = (v1x**2 + v1y**2)**0.5
        len2 = (v2x**2 + v2y**2)**0.5
        
        if len1 < 1e-10 or len2 < 1e-10:
            inset_points.append(p_curr)
            continue
        
        v1x, v1y = v1x/len1, v1y/len1
        v2x, v2y = v2x/len2, v2y/len2
        
        # Inward normals (rotate 90 degrees counter-clockwise for outer boundary)
        # For CW winding (typical in Revit), inward is to the left of the edge direction
        n1x, n1y = -v1y, v1x
        n2x, n2y = -v2y, v2x
        
        # Bisector direction (average of the two normals)
        bx = n1x + n2x
        by = n1y + n2y
        blen = (bx**2 + by**2)**0.5
        
        if blen < 1e-10:
            bx, by = n1x, n1y
        else:
            bx, by = bx/blen, by/blen
        
        # Calculate the actual inset distance along the bisector
        # This accounts for the angle at the corner
        dot = v1x * (-v2x) + v1y * (-v2y)
        angle_factor = 1.0
        if dot > -0.99:  # Not a straight line
            # sin(half_angle) determines how much further we need to go
            half_angle_sin = ((1 - dot) / 2) ** 0.5
            if half_angle_sin > 0.1:
                angle_factor = 1.0 / half_angle_sin
                angle_factor = min(angle_factor, 3.0)  # Limit to avoid extreme values
        
        # Move point inward
        new_x = p_curr[0] + bx * inset_distance * angle_factor
        new_y = p_curr[1] + by * inset_distance * angle_factor
        inset_points.append((new_x, new_y))
    
    # Check if the inset polygon is still valid (positive area)
    area = 0
    for i in range(len(inset_points)):
        x1, y1 = inset_points[i]
        x2, y2 = inset_points[(i + 1) % len(inset_points)]
        area += (x2 - x1) * (y2 + y1)
    
    # For CCW winding, area should be negative; for CW, positive
    # If sign flipped, polygon collapsed
    original_area = 0
    for i in range(len(boundary_tuples)):
        x1, y1 = boundary_tuples[i]
        x2, y2 = boundary_tuples[(i + 1) % len(boundary_tuples)]
        original_area += (x2 - x1) * (y2 + y1)
    
    # If areas have different signs or new area is too small, polygon collapsed
    if original_area * area < 0 or abs(area) < inset_distance * inset_distance * 0.1:
        return []
    
    return inset_points


def min_distance_to_boundary_single(px, py, boundary_points):
    """Calculate minimum distance from point to a single boundary loop."""
    min_dist = float('inf')
    n = len(boundary_points)
    for i in range(n):
        p1 = boundary_points[i]
        p2 = boundary_points[(i + 1) % n]
        x1 = p1.X if hasattr(p1, 'X') else p1[0]
        y1 = p1.Y if hasattr(p1, 'Y') else p1[1]
        x2 = p2.X if hasattr(p2, 'X') else p2[0]
        y2 = p2.Y if hasattr(p2, 'Y') else p2[1]
        dist = point_to_segment_distance(px, py, x1, y1, x2, y2)
        if dist < min_dist:
            min_dist = dist
    return min_dist


def create_grid_points_bounding_box(boundary_loops, grid_spacing, boundary_inset, z_height, profile=False):
    """Create grid points within the polygon using a bounding box approach.
    
    This is a performance-optimized alternative to create_grid_points_in_polygon.
    Instead of following the boundary shape with insets, it creates a regular
    rectangular grid and filters points to those inside the boundary.
    
    Additionally, points are generated along the first inset boundary curve to ensure
    the boundary edges are fully taken into account, including all corner points.
    
    Performance optimizations:
    1. Uses matplotlib Path.contains_points for vectorized point-in-polygon checks
    2. Uses numpy for fast array operations
    3. Generates all grid points at once and filters in batch
    
    Args:
        boundary_loops: List of boundary loops (each loop is a list of points)
        grid_spacing: Distance between grid points
        boundary_inset: Minimum distance from boundary for valid points
        z_height: Z coordinate for all grid points
        profile: Print timing information for performance analysis
        
    Returns:
        List of XYZ points within the boundary
    """
    import time
    
    if not boundary_loops:
        return []
    
    t_start = time.time()
    
    # First loop is the outer boundary
    outer_boundary = boundary_loops[0]
    # Remaining loops are holes (inner boundaries)
    holes = boundary_loops[1:] if len(boundary_loops) > 1 else []
    
    # Convert outer boundary to numpy array for matplotlib Path
    outer_vertices = np.array([(p.X, p.Y) for p in outer_boundary])
    outer_path = Path(outer_vertices)
    
    # Convert holes to matplotlib Paths
    hole_paths = []
    for hole in holes:
        hole_vertices = np.array([(p.X, p.Y) for p in hole])
        hole_paths.append(Path(hole_vertices))
    
    t_path_setup = time.time()
    if profile:
        print(f"    [PROFILE] Path setup: {t_path_setup - t_start:.4f}s")
    
    all_points = []
    
    # Generate points along the first inset boundary curve
    # This ensures the boundary edges are fully taken into account
    original_boundary_tuples = [(p.X, p.Y) for p in outer_boundary]
    inset_boundary = inset_boundary_loop_tuples(original_boundary_tuples, boundary_inset)
    
    t_inset = time.time()
    if profile:
        print(f"    [PROFILE] Boundary inset: {t_inset - t_path_setup:.4f}s")
    
    if inset_boundary:
        # First, add all corner points (vertices) of the inset boundary
        # This ensures corners are always sampled even if grid_spacing is large
        corner_coords = np.array(inset_boundary)
        
        # Check which corners are inside holes using vectorized operations
        corners_valid = np.ones(len(corner_coords), dtype=bool)
        for hole_path in hole_paths:
            corners_in_hole = hole_path.contains_points(corner_coords)
            corners_valid &= ~corners_in_hole
        
        # Add valid corner points
        for i, (cx, cy) in enumerate(corner_coords):
            if corners_valid[i]:
                all_points.append(XYZ(cx, cy, z_height))
        
        t_corners = time.time()
        if profile:
            print(f"    [PROFILE] Corner points ({len(corner_coords)} corners, {sum(corners_valid)} valid): {t_corners - t_inset:.4f}s")
        
        # Generate additional points along the inset boundary with segment length = grid_spacing
        boundary_points = generate_points_along_boundary(inset_boundary, grid_spacing, z_height)
        
        t_boundary_gen = time.time()
        if profile:
            print(f"    [PROFILE] Boundary point generation ({len(boundary_points) if boundary_points else 0} points): {t_boundary_gen - t_corners:.4f}s")
        
        if boundary_points:
            # Convert boundary points to numpy array for vectorized hole check
            boundary_coords = np.array([(p.X, p.Y) for p in boundary_points])
            
            # Check which boundary points are inside holes
            boundary_valid = np.ones(len(boundary_coords), dtype=bool)
            for hole_path in hole_paths:
                in_hole = hole_path.contains_points(boundary_coords)
                boundary_valid &= ~in_hole
            
            # Add valid boundary points
            for i, point in enumerate(boundary_points):
                if boundary_valid[i]:
                    all_points.append(point)
        
        t_boundary_filter = time.time()
        if profile:
            print(f"    [PROFILE] Boundary point filtering: {t_boundary_filter - t_boundary_gen:.4f}s")
    
    # Calculate bounding box of outer boundary
    min_x = outer_vertices[:, 0].min()
    max_x = outer_vertices[:, 0].max()
    min_y = outer_vertices[:, 1].min()
    max_y = outer_vertices[:, 1].max()
    
    # Inset the bounding box by boundary_inset to provide margin from edges
    min_x += boundary_inset
    max_x -= boundary_inset
    min_y += boundary_inset
    max_y -= boundary_inset
    
    # Check if bounding box is still valid
    if min_x >= max_x or min_y >= max_y:
        return all_points  # Return boundary points even if grid area is too small
    
    # Generate all grid points at once using numpy
    x_coords = np.arange(min_x, max_x + grid_spacing, grid_spacing)
    y_coords = np.arange(min_y, max_y + grid_spacing, grid_spacing)
    
    # Create meshgrid and flatten to get all point coordinates
    xx, yy = np.meshgrid(x_coords, y_coords)
    grid_coords = np.column_stack([xx.ravel(), yy.ravel()])
    
    t_grid_gen = time.time()
    if profile:
        print(f"    [PROFILE] Grid generation ({len(grid_coords)} candidates): {t_grid_gen - (t_boundary_filter if inset_boundary else t_inset):.4f}s")
    
    # Use matplotlib Path.contains_points for vectorized point-in-polygon check
    inside_outer = outer_path.contains_points(grid_coords)
    
    t_pip_outer = time.time()
    if profile:
        print(f"    [PROFILE] Point-in-polygon (outer): {t_pip_outer - t_grid_gen:.4f}s")
    
    # Check which points are inside holes
    inside_any_hole = np.zeros(len(grid_coords), dtype=bool)
    for hole_path in hole_paths:
        inside_any_hole |= hole_path.contains_points(grid_coords)
    
    t_pip_holes = time.time()
    if profile:
        print(f"    [PROFILE] Point-in-polygon (holes): {t_pip_holes - t_pip_outer:.4f}s")
    
    # Points are valid if inside outer boundary and not inside any hole
    valid_mask = inside_outer & ~inside_any_hole
    
    # Convert valid grid points to XYZ objects
    valid_coords = grid_coords[valid_mask]
    for x, y in valid_coords:
        all_points.append(XYZ(x, y, z_height))
    
    t_xyz_creation = time.time()
    if profile:
        print(f"    [PROFILE] XYZ object creation ({len(valid_coords)} grid + {len(all_points) - len(valid_coords)} boundary): {t_xyz_creation - t_pip_holes:.4f}s")
        print(f"    [PROFILE] Total create_grid_points_bounding_box: {t_xyz_creation - t_start:.4f}s")
    
    return all_points


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


def evaluate_heights_at_grid(doc, intersector, boundary_loops, boundary_inset, z_height, max_ray_distance, debug=False, create_debug_lines=False, profile=False):
    """Evaluate clear heights at grid points within a room boundary.
    
    This is the core evaluation function that casts rays at each grid point.
    
    Args:
        doc: Revit document
        intersector: ReferenceIntersector for ray casting
        boundary_loops: List of boundary loops from get_room_boundary_points
        boundary_inset: Minimum distance from boundary
        z_height: Z coordinate for grid points
        max_ray_distance: Maximum ray cast distance
        debug: Print debug information
        create_debug_lines: Create model lines for visualization
        profile: Print timing information for performance analysis
    
    Returns:
        List of clear heights in meters
    """
    import time
    
    # Debug: Print boundary information
    if debug and boundary_loops:
        outer_boundary = boundary_loops[0]
        print(f"  Boundary has {len(outer_boundary)} points:")
        min_x = min(p.X for p in outer_boundary)
        max_x = max(p.X for p in outer_boundary)
        min_y = min(p.Y for p in outer_boundary)
        max_y = max(p.Y for p in outer_boundary)
        print(f"  Boundary bounding box: X=[{min_x * 0.3048:.2f}m, {max_x * 0.3048:.2f}m], Y=[{min_y * 0.3048:.2f}m, {max_y * 0.3048:.2f}m]")
    
    # Create grid points within the room boundary using bounding box approach
    # This is much faster for complex boundaries as it's O(grid_size) instead of O(vertices * inset_levels)
    # Use GRID_SPACING constant for optimal performance (larger spacing = fewer points = faster)
    t_grid_start = time.time()
    grid_points = create_grid_points_bounding_box(boundary_loops, GRID_SPACING, boundary_inset, z_height, profile=profile)
    t_grid_end = time.time()
    if profile:
        print(f"  [PROFILE] Grid point generation: {t_grid_end - t_grid_start:.3f}s")
    
    if not grid_points:
        return []
    
    if debug or profile:
        print(f"  Generated {len(grid_points)} grid points")
    
    # Calculate clear heights at each grid point
    clear_heights = []
    up_direction = XYZ(0, 0, 1)
    down_direction = XYZ(0, 0, -1)
    
    debug_count = 0
    debug_lines_created = 0
    
    # Profiling accumulators
    t_ray_cast_total = 0.0
    t_hit_process_total = 0.0
    
    t_loop_start = time.time()
    for point in grid_points:
        should_debug = debug and debug_count < 1
        
        if should_debug:
            print(f"  --- Point {debug_count} at z={point.Z * 0.3048:.2f}m ---")
            print(f"  Casting UP:")
        
        # Cast ray upward - we need to find both:
        # 1. The floor's top surface (upward-facing normal) - this is where we measure FROM
        # 2. The ceiling's bottom surface (downward-facing normal) - this is where we measure TO
        t_ray_start = time.time()
        up_refs = intersector.Find(point, up_direction)
        t_ray_cast_total += time.time() - t_ray_start
        
        # Find ceiling (downward-facing normal)
        t_hit_start = time.time()
        ceiling_distance, ceiling_info = find_closest_room_bounding_hit(doc, up_refs, max_ray_distance, require_upward_normal=False, debug=should_debug)
        
        # Find floor top surface above us (upward-facing normal) - this handles cases where
        # we start slightly below the floor surface
        floor_top_distance, floor_top_info = find_closest_room_bounding_hit(doc, up_refs, max_ray_distance, require_upward_normal=True, debug=should_debug)
        t_hit_process_total += time.time() - t_hit_start
        
        if should_debug:
            print(f"  Casting DOWN:")
        
        # Cast ray downward - find floor's top surface (upward-facing normal)
        t_ray_start = time.time()
        down_refs = intersector.Find(point, down_direction)
        t_ray_cast_total += time.time() - t_ray_start
        
        t_hit_start = time.time()
        floor_below_distance, floor_below_info = find_closest_room_bounding_hit(doc, down_refs, max_ray_distance, require_upward_normal=True, debug=should_debug)
        t_hit_process_total += time.time() - t_hit_start
        
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
            # Convert from feet to meters and round to 3 decimal places
            clear_height_m = round(clear_height * 0.3048, 3)
            clear_heights.append(clear_height_m)
    
    t_loop_end = time.time()
    
    if profile:
        print(f"  [PROFILE] Ray casting loop total: {t_loop_end - t_loop_start:.3f}s")
        print(f"  [PROFILE]   - Ray casting (intersector.Find): {t_ray_cast_total:.3f}s ({t_ray_cast_total/(t_loop_end - t_loop_start)*100:.1f}%)")
        print(f"  [PROFILE]   - Hit processing (find_closest_room_bounding_hit): {t_hit_process_total:.3f}s ({t_hit_process_total/(t_loop_end - t_loop_start)*100:.1f}%)")
        other_time = (t_loop_end - t_loop_start) - t_ray_cast_total - t_hit_process_total
        print(f"  [PROFILE]   - Other (loop overhead, height calc): {other_time:.3f}s ({other_time/(t_loop_end - t_loop_start)*100:.1f}%)")
    
    if create_debug_lines:
        print(f"  Created {debug_lines_created} debug model lines")
    
    return clear_heights


def calculate_clear_heights_for_room(doc, room, intersector, boundary_options, boundary_inset, max_ray_distance, debug=False, create_debug_lines=False, profile=False):
    """Calculate clear heights for a room using bounding box grid approach.
    
    Args:
        doc: Revit document
        room: Room element
        intersector: ReferenceIntersector for ray casting
        boundary_options: SpatialElementBoundaryOptions
        boundary_inset: Minimum distance from boundary
        max_ray_distance: Maximum ray cast distance
        debug: Print debug information
        create_debug_lines: Create model lines for visualization
        profile: Print timing information for performance analysis
    
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
    
    # Evaluate heights using bounding box grid approach
    final_heights = evaluate_heights_at_grid(
        doc, intersector, boundary_loops, boundary_inset,
        z_height, max_ray_distance, debug=debug, 
        create_debug_lines=False,  # We'll create lines after if requested
        profile=profile
    )
    
    # Create debug lines if requested
    if create_debug_lines:
        if debug:
            print(f"  Creating debug lines for boundary...")
        
        # Create boundary lines
        boundary_lines_count = 0
        for loop in boundary_loops:
            for i in range(len(loop)):
                p1 = loop[i]
                p2 = loop[(i + 1) % len(loop)]
                # Create points at calculation z_height
                p1_z = XYZ(p1.X, p1.Y, z_height)
                p2_z = XYZ(p2.X, p2.Y, z_height)
                if create_debug_model_line(doc, p1_z, p2_z):
                    boundary_lines_count += 1
        
        if debug:
            print(f"  Created {boundary_lines_count} boundary debug lines")

        # Create ray path lines (floor to ceiling)
        if final_heights:
            evaluate_heights_at_grid(
                doc, intersector, boundary_loops, boundary_inset,
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
    
    # Group heights within tolerance (0.01m instead of 0.05m for better precision)
    # INCREASED: We still want to group 2.603 and 2.598 for example.
    tolerance = 0.01
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
                # Use a smaller window to be more strict about what's considered a variation
                if abs(representative - dominant_height) <= 0.20:
                    filtered_heights.append(representative)
            else:
                filtered_heights.append(representative)
        elif percentage > 0.15: # Also include significant secondary heights even if below threshold? No, threshold IS for that.
            pass
    
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
def main(debug=False, create_debug_lines=False, profile=False):
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
                BOUNDARY_INSET, max_ray_distance, 
                debug=debug, create_debug_lines=create_debug_lines,
                profile=profile
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
                
                # If room is room 10843640, output more info for debugging
                if room.Id.Value == 10843640:
                     print(f"DEBUG ROOM 10843640: {len(clear_heights)} points evaluated.")
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
