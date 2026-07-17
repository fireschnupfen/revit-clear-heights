# revit-clear-heights

A Python toolkit for Autodesk Revit that automatically calculates the **clear height** (*lichte Raumhöhe*) of selected rooms and writes the result directly into room parameters. It also includes utilities for room visualization and color fill scheme management.

---

## Features

- **Automatic clear height calculation** — Casts rays from a grid of points inside each room to detect floor/ceiling face pairs using the Revit `ReferenceIntersector` API.
- **Polygon intersection geometry** — Uses [Shapely](https://shapely.readthedocs.io/) to compute the 2D overlap area between floor and ceiling projections, giving an accurate area-weighted height distribution.
- **Adaptive grid spacing** — Grid density scales automatically with room area (configurable min/max spacing and area thresholds).
- **Smart height aggregation** — Groups height results into buckets, filters out negligible areas (by percentage or absolute m²), and identifies a dominant height zone.
- **Parameter write-back** — Writes the calculated clear height range to the `Lichte_Raumhöhe` room parameter and the dominant height to `Lichte_Raumhöhe_Bestimmend`.
- **Room DirectShape creation** — Generates solid 3D `DirectShape` Generic Model elements from room volumes for visualization and clash detection.
- **Color fill scheme updater** — Applies colors from an AutoCAD color book (`.abc`/`.acb`) file to the active view's room/area color fill scheme, with automatic palette expansion via HSV interpolation.

---

## Project Structure

```
revit-clear-heights/
├── face_intersection_drawer.py   # Core clear height calculation engine + main entry point
├── create_room_directshape.py    # Creates DirectShape solids from room geometry
├── color_fill_scheme_updater.py  # Applies color book colors to the active color fill scheme
├── face_selection_utility.py     # Helper utilities for face selection
├── tests/
│   ├── conftest.py               # Pytest fixtures and Revit session setup
│   ├── main_test.py              # Integration test for the main entry point
│   └── test_methods.py           # Unit and integration tests for individual functions
└── AGENT.md                      # AI assistant guidelines for this project
```

---

## Requirements

| Requirement | Details |
|---|---|
| **Revit** | Autodesk Revit 2026 (or compatible) |
| **Python** | Python 3.x (CPython via `pythonnet`) |
| **revit-rpc** | Remote execution bridge between CPython and the Revit API |
| **Shapely** | 2D polygon operations (`pip install shapely`) |

> **Note:** The Revit API namespaces (`Autodesk.Revit.DB`, `System.Collections.Generic`, etc.) are only available inside the Revit process. All such imports are wrapped in `try/except ImportError` blocks so the scripts can be imported and tested locally without a live Revit session.

---

## Setup

1. **Install dependencies** in your local Python environment:
   ```bash
   pip install shapely revit-rpc
   ```

2. **Ensure `revit-rpc` is connected** to a running Revit session. Refer to the `revit-rpc` package documentation for server-side setup inside Revit.

3. **Create a 3D view named `RAY_CASTING_VIEW`** in your Revit model. The clear height engine uses this view as the context for `ReferenceIntersector` ray casting. The view must be a non-template 3D view with all relevant floor, ceiling, and roof elements visible.

4. **Add the required room parameters** to your Revit project:
   - `Lichte_Raumhöhe` — Text parameter on the Room category (receives the calculated height range, e.g. `2,600 m` or `2,600 m - 3,200 m`)
   - `Lichte_Raumhöhe_Bestimmend` — Text parameter on the Room category (receives the dominant height zone, e.g. `2,600 m (~75%)`)

---

## Usage

### Clear Height Calculation

1. Open your Revit model and select one or more **Room** elements.
2. Run `face_intersection_drawer.py` via `revit-rpc`:
   ```python
   from face_intersection_drawer import main
   main()
   ```
   Or with debug output and custom settings:
   ```python
   main(
       debug=True,
       grid_spacing_min_m=0.5,
       grid_spacing_max_m=1.5,
       area_min_m2=10.0,
       area_max_m2=100.0,
       area_threshold_pct=5.0,
       area_threshold_m2=10,
       dominant_area_pct_threshold=50.0,
       dominant_n_times=2.0,
       ray_length_multiplicator=1.5,
       create_ray_lines=False,
       create_debug_lines=False,
   )
   ```

The script will:
- Extract the room boundary polygon at the computation height.
- Cast vertical rays (up and down) from a grid of points inside the room.
- Identify floor/ceiling face pairs and compute their intersection polygon.
- Aggregate results by height group, filter out negligible zones, and determine the dominant height.
- Write the result to the `Lichte_Raumhöhe` and `Lichte_Raumhöhe_Bestimmend` parameters.

### `main()` Parameters

| Parameter | Default | Description |
|---|---|---|
| `debug` | `False` | Print detailed progress and summary to console |
| `create_ray_lines` | `False` | Draw model lines along ray paths (for visual debugging) |
| `create_debug_lines` | `False` | Draw intersection polygon outlines as model lines |
| `grid_spacing_min_m` | `0.5` | Minimum ray grid spacing in metres (used for small rooms) |
| `grid_spacing_max_m` | `1.5` | Maximum ray grid spacing in metres (used for large rooms) |
| `area_min_m2` | `10.0` | Room area below which minimum grid spacing is used |
| `area_max_m2` | `100.0` | Room area above which maximum grid spacing is used |
| `area_threshold_pct` | `5.0` | Minimum area percentage for a height zone to be included |
| `area_threshold_m2` | `10` | Minimum absolute area (m²) for a height zone to be included |
| `dominant_area_pct_threshold` | `50.0` | Minimum area percentage for a zone to be considered dominant |
| `dominant_n_times` | `2.0` | A zone must cover this multiple of any other zone to be dominant |
| `ray_length_multiplicator` | `1.5` | Multiplier on the room's unbounded height for max ray distance |
| `filter_name` | `None` | Name of a Revit `ParameterFilterElement` to restrict ray targets |

### Room DirectShape Creation

Select rooms in Revit and run:
```python
from create_room_directshape import main
main()
```
This creates a solid `DirectShape` Generic Model element for each selected room, representing its full 3D volume. Useful for visualization, exports, and clash detection.

### Color Fill Scheme Updater

Run the following while a room/area color fill scheme is active in the current view:
```python
from color_fill_scheme_updater import main
main()
```
A file dialog will prompt you to select an AutoCAD color book file (`.abc` or `.acb`). The script parses the RGB entries, expands the palette by interpolating midpoint colors, and applies them to the active scheme's entries using a multi-directional HSV shift to maximize contrast across repeated cycles.

---

## How the Clear Height Algorithm Works

```
For each selected room:
  1. Determine the Z height = Level Elevation + Computation Height + small offset
  2. Extract the room boundary as a 2D Shapely polygon
  3. Compute adaptive grid spacing based on room area
  4. Cast rays UP and DOWN from each grid point inside the room polygon
     → Targets: Floors, Ceilings, Roofs (room-bounding elements only)
  5. Group hits into (floor_face, ceiling_face) pairs
  6. For each pair:
     a. Project both faces onto the Z plane as 2D polygons
     b. Compute their intersection with the room polygon
     c. Sample Z values across the intersection to get actual heights
  7. Round heights to nearest 0.5 cm
  8. Group by height range, filter by area threshold
  9. Identify dominant height zone (if one zone covers ≥ 50% and ≥ 2× all others)
 10. Write result to room parameters
```

---

## Testing

Integration tests require a live Revit session accessible via `revit-rpc`.

```bash
# Run all tests
python -m pytest -x

# Run a specific parametrized test case
python -m pytest -x -k "6457698"
```

Tests validate:
- Individual helper functions (`get_face_polygon`, `get_room_polygon`, `get_subtracted_polygon`, etc.)
- End-to-end clear height evaluation for specific rooms by element ID, with expected height ranges.

---

## Notes

- All internal Revit API length values are in **feet** (Revit's internal unit). Conversions to metres use the factor `0.3048` (feet → metres) and `3.28084` (metres → feet).
- Height results are formatted with **German locale** (comma as decimal separator) to match the target Revit project's regional settings.
- The `__revit__` global variable (the active `UIApplication`) is only available inside `@revit`-decorated functions. It is type-hinted as `str` in module scope solely to suppress unresolved-reference warnings in IDEs.
