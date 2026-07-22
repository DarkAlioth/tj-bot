import logging
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import URL

logger = logging.getLogger(__name__)


def run_migrations(url: URL) -> None:
    """Apply pending Alembic migrations. Must run before the event loop starts."""
    config = AlembicConfig()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.attributes["sqlalchemy_url"] = url
    logger.info("Applying database migrations")
    command.upgrade(config, "head")
