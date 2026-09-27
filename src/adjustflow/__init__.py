"""survey-adjust-workflow: least-squares adjustment workflow engine.

Build #2 of the SurveySuite desktop product. Reads ``.sfield.json`` job
files from survey-field, runs three adjustment paths (RTK weighted means,
level-net least squares via the survey-adjust engine, parametric traverse
adjustment) under a user-dictated stochastic model, detects blunders by
data snooping, and writes a plain-language justification report plus a
machine-readable adjusted-products contract for build #3.

Pure Python, zero third-party dependencies; the sibling engines
survey-adjust and survey-field are reused, not reimplemented.
"""

__version__ = "0.1.2"

from . import jobio, levelnet, report, rtk, stochastic, traverse
from .stochastic import default_weights, load_weights, validate_weights

__all__ = [
    "__version__",
    "jobio",
    "levelnet",
    "report",
    "rtk",
    "stochastic",
    "traverse",
    "default_weights",
    "load_weights",
    "validate_weights",
]
