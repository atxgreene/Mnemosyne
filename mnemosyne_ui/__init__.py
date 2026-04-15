"""mnemosyne_ui — static assets for the dashboard.

This package only ships data files (HTML / CSS / JS / SVG) under
`mnemosyne_ui/static/`. There is no Python API; the assets are
loaded by `mnemosyne_serve._serve_ui_index` and `_serve_static`.

Existing as a package (rather than a bare directory) so the assets
ship with `pip install`-built wheels via package_data.
"""
