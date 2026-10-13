# minisci agent guide

- Library code lives in `minisci/`; tests live in `tests/` and run with `python -m pytest -q`.
- Follow NumPy semantics for statistics (`ddof`, empty inputs). Compare floats with `pytest.approx`.
- Numerical code must not overflow for large inputs: see `minisci/stats.py` for the style used.
