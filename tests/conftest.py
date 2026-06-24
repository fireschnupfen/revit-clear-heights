import pytest
from revit_rpc import revit, start_session, end_session

__revit__: str


@pytest.fixture(scope="session", autouse=True)
def revit_session():
    """Start and end the Revit RPC session for all tests."""
    start_session()
    yield
    end_session()


@pytest.fixture(scope="session", autouse=False)
@revit
def transaction_group():
    """Create a transaction group that will be rolled back after each test."""
    from Autodesk.Revit.DB import TransactionGroup
    
    doc = __revit__.ActiveUIDocument.Document
    tg = TransactionGroup(doc, "Test Transaction Group")
    tg.Start()
    yield
    tg.RollBack()


@pytest.fixture()
@revit
def transaction():
    """Create a transaction group that will be rolled back after each test."""
    from Autodesk.Revit.DB import Transaction

    doc = __revit__.ActiveUIDocument.Document
    t = Transaction(doc, "Test Transaction")
    t.Start()
    yield
    t.Commit()


@pytest.fixture
@revit
def selected_room():
    """Return the first selected Room in the active Revit document."""
    from Autodesk.Revit.DB import BuiltInCategory
    
    uidoc = __revit__.ActiveUIDocument
    doc = uidoc.Document
    selection = uidoc.Selection.GetElementIds()
    
    for eid in selection:
        elem = doc.GetElement(eid)
        if elem.Category and elem.Category.Id.Value == int(BuiltInCategory.OST_Rooms):
            return elem
    return None
