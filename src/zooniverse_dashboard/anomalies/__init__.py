"""Offline aggregate-only anomaly analysis, deliberately independent of Dash.

Dash imports ``catalog`` only; NumPy, SciPy and statsmodels are imported by the
separate offline ``compute`` module when rebuilding the anomaly catalog.
"""
