import pytest
from revit_rpc import revit
from face_intersection_drawer import main

try:
    from System.Collections.Generic import List
    from Autodesk.Revit.DB import ElementId
except ImportError:
    pass

__revit__: str


@revit
def set_clear_heights(room_id):
    uidoc = __revit__.ActiveUIDocument

    id = ElementId(room_id)

    ids = List[ElementId]()
    ids.Add(id)
    uidoc.Selection.SetElementIds(ids)

    room = uidoc.Document.GetElement(id)
    main(debug=True, create_debug_lines=True, create_ray_lines=True, grid_spacing_min_m=0.5, grid_spacing_max_m=1.5,
         area_min_m2=10.0, area_max_m2=100.0)

    return room.LookupParameter("Lichte_Raumhöhe").AsString()


# Test data : (room_id, expected_value)
# Format: "X,XXX m" or "X,XXX m - Y,YYY m" (3 decimal places, space before m)
TEST_ROOMS = [
    (5761, "2,700 m"),
    (5762, "2,700 m"),
    (5763, "2,700 m"),
    (5764, "2,700 m"),
    (5768, "3,815 m - 5,647 m"),
    (5769, "3,953 m - 4,647 m"),
    (5770, "2,815 m - 4,934 m"),
]


# Test data from PES_Gebäudemodell: (room_id, expected_value)
# Format: "X,XXX m" or "X,XXX m - Y,YYY m" (3 decimal places, space before m)
TEST_ROOMS_PES = [
    (5852412, "3,130 m"),
    (5139071, "2,600 m - 2,630 m"),
    (5498143, "2,600 m"),
    (5850933, "3,200 m"),
    (11398635, "3,100 m"),
    (58817722, "3,410 m"),
    (11129429, "5,475 m"),
    (5850929, "3,170 m"),
    (6477863, "2,660 m"),
    (7609512, "4,760 m - 8,260 m"),
    (10843640, "4,760 m"),
    (6457698, "3,410 m - 4,010 m"),
    (61816302, "3,190 m - 3,410 m")
]


@pytest.mark.parametrize("room_id,expected", TEST_ROOMS_PES)
def test_calculate_clear_heights(room_id, expected):
    value_calculated = set_clear_heights(room_id)
    assert value_calculated == expected
