import pytest
from revit_rpc import revit, start_session, end_session

__revit__: str


@pytest.fixture(scope="session", autouse=False)
def revit_session():
    """Start and end the Revit RPC session for all tests."""
    start_session()
    yield
    end_session()


@pytest.fixture(scope="function", autouse=False)
@revit
def transaction_group():
    """Create a transaction group that will be rolled back after each test."""
    from Autodesk.Revit.DB import TransactionGroup
    
    doc = __revit__.ActiveUIDocument.Document
    tg = TransactionGroup(doc, "Test Transaction Group")
    tg.Start()
    yield
    tg.RollBack()


@pytest.fixture
@revit
def transaction():
    """Create a transaction that will be rolled back after each test."""
    from Autodesk.Revit.DB import Transaction
    
    doc = __revit__.ActiveUIDocument.Document
    t = Transaction(doc, "Test Transaction")
    t.Start()
    yield t
    t.Commit()
