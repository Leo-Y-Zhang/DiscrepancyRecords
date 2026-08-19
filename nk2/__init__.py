"""nk2 - tooling for N(k,l), the discrepancy-style van der Waerden quantity.

Deliberately empty of imports. ``evaluator`` must stay reachable without pulling
in ``spec`` or any encoder, so that the solver-free arithmetic it performs can
never be contaminated by the threshold the encoders use.
"""

__all__: list[str] = []
