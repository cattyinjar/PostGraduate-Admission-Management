# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

hiddenimports = ["keyring.backends.Windows"]
trafilatura_data = collect_data_files("trafilatura")

a = Analysis(
    ["../run_app.py"],
    pathex=[".."],
    binaries=[],
    datas=trafilatura_data,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2", "tkinter", "pytest", "pgam.tests", "numpy", "pandas", "matplotlib", "scipy", "sympy", "numba", "sklearn", "statsmodels", "seaborn", "plotly", "bokeh", "IPython", "ipykernel", "jupyter_client", "nbformat", "sphinx", "rich", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PostGraduateAdmissionMonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=r"..\installer\assets\app.ico",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PGAM")
