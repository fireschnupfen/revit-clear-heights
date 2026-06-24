import pytest
from unittest.mock import MagicMock
import sys

from revit_rpc import revit

# Mock imports if needed, or just let them fail and be mocked
# Let's mock the Revit elements and call the functions

from face_intersection_drawer import (
    get_3d_view_by_name,
    get_closest_valid_ref,
    auto_find_room_faces,
    get_face_polygon,
    get_room_polygon,
    get_z_on_planar_face,
    evaluate_face_pair,
    main
)


@revit
def test_get_room_polygon(transaction, selected_room):
    try:
        from Autodesk.Revit.DB import Plane, SketchPlane, XYZ
        doc = selected_room.Document
        
        origin = XYZ(0, 0, 0)
        plane = Plane.CreateByNormalAndOrigin(XYZ.BasisZ, origin)
        sketch_plane = SketchPlane.Create(doc, plane)
        
        get_room_polygon(selected_room, doc=doc, sketch_plane=sketch_plane, z_height=0.0, create_model_lines=True)
    except Exception as e:
        pytest.fail(f"get_room_polygon raised {e}")
