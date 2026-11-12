from tj_bot.handlers.admin import admin_router
from tj_bot.handlers.admin_info import admin_info_router
from tj_bot.handlers.echo import echo_router
from tj_bot.handlers.errors import errors_router
from tj_bot.handlers.favorites import favorites_router
from tj_bot.handlers.inline import inline_router
from tj_bot.handlers.qbit_console import qbit_router
from tj_bot.handlers.server_download import server_download_router
from tj_bot.handlers.user import user_router
from tj_bot.handlers.users import users_router
from tj_bot.handlers.widgets_admin import widgets_admin_router

routers_list = [
    errors_router,
    qbit_router,
    # widget admin tooling first: its command handlers step aside for admins
    # on the classic UI, who then reach admin_info/users below
    widgets_admin_router,
    admin_info_router,
    users_router,
    admin_router,
    server_download_router,
    favorites_router,
    user_router,
    inline_router,
    echo_router,
]

__all__ = [
    "routers_list",
]
