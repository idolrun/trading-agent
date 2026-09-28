"""Imports every `models.py` so `SQLModel.metadata` (Alembic autogenerate) sees all tables."""

from app.alerts import models as alerts_models
from app.auth import models as auth_models
from app.evalkit import models as evalkit_models
from app.inbox import models as inbox_models
from app.interpret import models as interpret_models
from app.llm import models as llm_models
from app.manual import models as manual_models
from app.notify import models as notify_models
from app.runtime_config import models as runtime_config_models
from app.system import models as system_models

__all__ = [
    "alerts_models",
    "auth_models",
    "evalkit_models",
    "inbox_models",
    "interpret_models",
    "llm_models",
    "manual_models",
    "notify_models",
    "runtime_config_models",
    "system_models",
]
