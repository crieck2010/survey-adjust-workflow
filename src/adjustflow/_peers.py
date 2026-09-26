"""Lazy imports of the sibling survey-suite engines.

survey-adjust-workflow reuses the engines instead of reimplementing them:

* ``adjust``  (crieck2010/survey-adjust) -- least-squares engine, matrix
  toolkit, chi-square global test.
* ``field``   (crieck2010/survey-field)  -- .sfield.json job files.

Imports are lazy so ``import adjustflow`` never fails at module scope;
each accessor raises an ImportError naming the exact pip command when the
peer is missing.
"""

from __future__ import annotations

ADJUST_REQ = "survey-adjust @ git+https://github.com/crieck2010/survey-adjust@bc144832aeddc77db9c73b7240c300f1774436a6"
FIELD_REQ = "survey-field @ git+https://github.com/crieck2010/survey-field@v0.1.0"


def require_adjust():
    try:
        import adjust  # type: ignore
        import adjust.matrices  # type: ignore  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "survey-adjust is required for the adjustment workflow:\n"
            f"    pip install {ADJUST_REQ!r}") from exc
    return adjust


def require_adjust_matrices():
    """The pure-Python matrix toolkit (inverse/matmul/matvec/transpose/zeros)."""
    return require_adjust().matrices


def require_field():
    try:
        import field  # type: ignore
        import field.adapters  # type: ignore  # noqa: F401
        import field.jobfile  # type: ignore  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "survey-field is required to read .sfield.json job files:\n"
            f"    pip install {FIELD_REQ!r}") from exc
    return field
