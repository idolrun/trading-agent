"""Imports every `models.py` so `SQLModel.metadata` (Alembic autogenerate) sees all tables."""

from app.inbox import models as inbox_models
from app.interpret import models as interpret_models
from app.llm import models as llm_models
from app.system import models as system_models

__all__ = ["inbox_models", "interpret_models", "llm_models", "system_models"]
