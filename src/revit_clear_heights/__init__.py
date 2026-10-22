"""
revit-clear-heights: Automated clear height calculation for Autodesk Revit rooms.
"""

from .core import calculate_clear_heights, main
from .geometry import (
    robust_difference,
    robust_intersection,
    robust_union,
    robust_contains,
    get_face_polygon,
    get_room_polygon,
    get_z_on_planar_face,
    draw_shapely_polygon,
    get_subtracted_polygon,
    get_intersection_polygon,
)
from .ray_casting import (
    BOUNDARY_CATEGORIES,
    get_3d_view_by_name,
    get_closest_valid_ref,
    auto_find_room_faces,
    get_room_face_pairs,
)
from .evaluation import (
    round_to_half_cm,
    format_height_value,
    evaluate_face_pair,
    evaluate_room_clear_heights,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "calculate_clear_heights",
    "main",
    "robust_difference",
    "robust_intersection",
    "robust_union",
    "robust_contains",
    "get_face_polygon",
    "get_room_polygon",
    "get_z_on_planar_face",
    "draw_shapely_polygon",
    "get_subtracted_polygon",
    "get_intersection_polygon",
    "BOUNDARY_CATEGORIES",
    "get_3d_view_by_name",
    "get_closest_valid_ref",
    "auto_find_room_faces",
    "get_room_face_pairs",
    "round_to_half_cm",
    "format_height_value",
    "evaluate_face_pair",
    "evaluate_room_clear_heights",
]

