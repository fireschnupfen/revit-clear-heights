import pytest
from revit_rpc import revit
from main import main

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
    main()

    return(room.LookupParameter("Lichte_Raumhöhe").AsString(), room.LookupParameter("CLEAR_HEIGHT_VALIDATION").AsString())


@pytest.mark.parametrize("room_id", [5498143,11398635,58817722,11129429,5850929,6477863,58817722,58817722,7609512,10843640])
def test_calculate_clear_heights(room_id):
    value_calculated, value_actual = set_clear_heights(room_id)
    assert value_calculated == value_actual
