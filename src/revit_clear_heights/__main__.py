"""
CLI entry point for revit-clear-heights.
Allows running: python -m revit_clear_heights
"""

import sys
import argparse
from .core import main

__revit__: str


def cli():
    parser = argparse.ArgumentParser(description="Calculate clear heights for Revit rooms.")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    parser.add_argument("--grid-min", type=float, default=0.5, help="Minimum grid spacing in metres (default: 0.5)")
    parser.add_argument("--grid-max", type=float, default=1.5, help="Maximum grid spacing in metres (default: 1.5)")
    parser.add_argument("--view", type=str, default="RAY_CASTING_VIEW", help="Name of 3D view for ray casting")
    parser.add_argument("--filter", type=str, default=None, help="Name of ParameterFilterElement to ignore")
    parser.add_argument("--param-ch", type=str, default="Lichte_Raumhöhe", help="Parameter name for clear height")
    parser.add_argument("--param-dom", type=str, default=None, help="Parameter name for dominant clear height")
    parser.add_argument("--param-lower", type=str, default=None, help="Parameter name for lower elevation (floor)")
    parser.add_argument("--param-upper", type=str, default=None, help="Parameter name for upper elevation (ceiling)")

    args = parser.parse_args()

    print("Running revit-clear-heights...")
    main(
        debug=args.debug,
        grid_spacing_min_m=args.grid_min,
        grid_spacing_max_m=args.grid_max,
        ray_casting_view_name=args.view,
        filter_name=args.filter,
        clear_height_param_name=args.param_ch,
        dominant_clear_height_param_name=args.param_dom,
        lower_elevation_param_name=args.param_lower,
        upper_elevation_param_name=args.param_upper,
    )


if __name__ == "__main__":
    cli()

