from tj_bot.handlers.admin import admin_router
from tj_bot.handlers.admin_info import admin_info_router
from tj_bot.handlers.echo import echo_router
from tj_bot.handlers.errors import errors_router
from tj_bot.handlers.qbit_console import qbit_router
from tj_bot.handlers.user import user_router
from tj_bot.handlers.users import users_router

routers_list = [
    errors_router,
    qbit_router,
    admin_info_router,
    users_router,
    admin_router,
    user_router,
    echo_router,
]

__all__ = [
    "routers_list",
]
