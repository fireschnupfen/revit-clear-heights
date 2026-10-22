# revit-clear-heights

A Python package and toolkit for Autodesk Revit that automatically calculates the **clear height** (*lichte Raumhöhe*) of rooms and writes the results directly into Revit room parameters. It also includes utilities for room volume solid geometry (`DirectShape`) creation.

---

## Features

- **Automatic clear height calculation** — Casts rays from a grid of points inside each room to detect floor/ceiling face pairs using Revit's `ReferenceIntersector` API.
- **Polygon intersection geometry** — Uses [Shapely](https://shapely.readthedocs.io/) to compute the 2D overlap area between floor and ceiling projections, giving an accurate area-weighted height distribution.
- **Adaptive grid spacing** — Ray grid density scales automatically based on room floor area.
- **Smart height aggregation** — Groups height results into buckets, filters negligible areas (by percentage or absolute m²), and identifies dominant height zones.
- **Parameter write-back** — Writes the calculated height range to `Lichte_Raumhöhe` and dominant height to `Lichte_Raumhöhe_Bestimmend`.
- **DirectShape creation** — Creates 3D solid `DirectShape` massing/geometry elements from room clear height volumes.
- **Full CLI & Python API** — Usable as an installed Python package, via CLI (`python -m revit_clear_heights`), or as a script (`main.py`).

---

## Package Structure

```
revit-clear-heights/
├── pyproject.toml                 # Package definition, build configuration & dependencies
├── README.md                      # Documentation
├── AGENT.md                       # Assistant guidelines and Revit environment rules
├── main.py                        # Script entry point & re-export module
├── src/
│   └── revit_clear_heights/
│       ├── __init__.py            # Package root, exports & __version__
│       ├── __main__.py            # CLI entry point (python -m revit_clear_heights)
│       ├── core.py                # Main workflow: calculate_clear_heights() & main()
│       ├── geometry.py            # Shapely polygon operations & 2D geometry helpers
│       ├── ray_casting.py         # Revit ray-casting & floor/ceiling face detection
│       ├── evaluation.py          # Height sampling, bucketing & formatting
│       └── shapes.py              # DirectShape extrusion utilities for room volumes
└── tests/
    ├── conftest.py                # Pytest fixtures and Revit session configuration
    ├── main_test.py               # Integration tests for main()
    └── test_methods.py            # Unit and component tests
```

---

## Requirements

| Requirement | Details |
|---|---|
| **Revit** | Autodesk Revit 2026 (or compatible) |
| **Python** | Python 3.8+ |
| **Shapely** | 2D polygon operations (`shapely>=2.0.0`) |

> **Note:** Revit API namespaces (`Autodesk.Revit.DB`, `System.Collections.Generic`, etc.) are only available inside the Revit process. Module-level imports are safely wrapped in `try/except ImportError` blocks so the package can be imported, tested, and inspected in standard Python environments.

---

## Installation

Install the package in editable mode with development dependencies:

```bash
pip install -e ".[dev]"
```

Or standard installation:

```bash
pip install .
```

---

## Usage

### 1. As a Python Library

```python
import revit_clear_heights as rch

# Run standard calculation
rch.main(debug=True)

# Or call calculate_clear_heights directly with custom parameters
rch.calculate_clear_heights(
    debug=True,
    grid_spacing_min_m=0.5,
    grid_spacing_max_m=1.5,
    area_min_m2=10.0,
    area_max_m2=100.0,
    area_threshold_pct=5.0,
    area_threshold_m2=10,
    dominant_area_pct_threshold=50.0,
    dominant_n_times=2.0,
    clear_height_param_name="Lichte_Raumhöhe",
    dominant_clear_height_param_name="Lichte_Raumhöhe_Bestimmend",
    ray_casting_view_name="RAY_CASTING_VIEW",
)
```

### 2. From the Command Line (CLI)

```bash
python -m revit_clear_heights --debug --grid-min 0.5 --grid-max 1.5
```

### 3. Via `main.py`

```bash
python main.py
```

### 4. Create 3D DirectShapes from Clear Height Volumes

Select rooms in Revit and execute:

```python
from revit_clear_heights import create_clear_height_shapes

create_clear_height_shapes(debug=True, extrusion_height_m=2.1)
```

---

## Configuration & Parameters

| Parameter | Default | Description |
|---|---|---|
| `debug` | `False` | Print detailed progress and summary to console |
| `create_ray_lines` | `False` | Draw model lines along ray paths for visual debugging |
| `create_debug_lines` | `False` | Draw intersection polygon outlines as model lines |
| `grid_spacing_min_m` | `0.5` | Minimum ray grid spacing in metres (used for small rooms) |
| `grid_spacing_max_m` | `1.5` | Maximum ray grid spacing in metres (used for large rooms) |
| `area_min_m2` | `10.0` | Room area below which minimum grid spacing is used |
| `area_max_m2` | `100.0` | Room area above which maximum grid spacing is used |
| `area_threshold_pct` | `5.0` | Minimum area percentage for a height zone to be included |
| `area_threshold_m2` | `10` | Minimum absolute area (m²) for a height zone to be included |
| `dominant_area_pct_threshold` | `50.0` | Minimum area percentage for a zone to be considered dominant |
| `dominant_n_times` | `2.0` | Multiplier above other zones required to qualify as dominant |
| `ray_length_multiplicator` | `1.5` | Multiplier on the room's unbounded height for max ray distance |
| `filter_name` | `None` | Name of a Revit `ParameterFilterElement` to restrict/ignore ray targets |
| `clear_height_param_name` | `"Lichte_Raumhöhe"` | Target parameter name for calculated clear height |
| `dominant_clear_height_param_name` | `"Lichte_Raumhöhe_Bestimmend"` | Target parameter name for dominant height zone |
| `ray_casting_view_name` | `"RAY_CASTING_VIEW"` | Dedicated 3D view used by `ReferenceIntersector` |

---

## Testing

Run tests with `pytest`:

```bash
# Run all tests
python -m pytest -v

# Run a specific test
python -m pytest -k "test_get_room_polygon"
```
