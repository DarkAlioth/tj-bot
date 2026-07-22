from tj_bot.handlers.echo import echo_router
from tj_bot.handlers.errors import errors_router
from tj_bot.handlers.user import user_router

routers_list = [
    errors_router,
    user_router,
    echo_router,
]

__all__ = [
    "routers_list",
]
