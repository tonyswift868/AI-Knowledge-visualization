"""Enterprise boundary packages shared by Core-v2 services.

Business features must depend on the versioned contracts below instead of
declaring competing actor, tenant, resource-scope, or audit models.
"""

from .contracts_v1 import ENTERPRISE_CONTEXT_CONTRACT_VERSION

__all__ = ["ENTERPRISE_CONTEXT_CONTRACT_VERSION"]
