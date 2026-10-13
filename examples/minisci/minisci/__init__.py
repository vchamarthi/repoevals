"""minisci: a tiny numerical library used to demonstrate agentexam end to end."""
from .stats import mean, var, logsumexp
from .distance import chebyshev

__all__ = ["mean", "var", "logsumexp", "chebyshev"]
__version__ = "0.1.0"
