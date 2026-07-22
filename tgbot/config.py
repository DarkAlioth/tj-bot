import json
from dataclasses import dataclass

from environs import Env

env = Env()
env.read_env(".env")
DB_HOST = env.str("DB_HOST")
POSTGRES_PASSWORD = env.str("POSTGRES_PASSWORD")
POSTGRES_USER = env.str("POSTGRES_USER")
POSTGRES_DB = env.str("POSTGRES_DB")
DB_PORT = env.int("DB_PORT", 5432)


@dataclass
class TgBot:
    token: str
    admin_ids: list[int]

    @staticmethod
    def from_env(env: Env):
        token = env.str("BOT_TOKEN")
        admin_ids = env.list("ADMINS", subcast=int)
        return TgBot(token=token, admin_ids=admin_ids)


@dataclass
class JackettApi:
    jackett_url: str
    jackett_key: str

    @staticmethod
    def from_config(config_path: str):
        jackett_url = "http://jackett:9117"

        with open(config_path, "r") as file:
            config = json.load(file)
            jackett_key = config.get("APIKey")

        return JackettApi(jackett_url=jackett_url, jackett_key=jackett_key)


@dataclass
class Miscellaneous:
    other_params: str = None


@dataclass
class Config:
    tg_bot: TgBot
    jackett: JackettApi
    misc: Miscellaneous


def load_config(path: str = None) -> Config:
    env = Env()
    env.read_env(path)

    jackett_config_path = "/config/ServerConfig.json"

    return Config(
        tg_bot=TgBot.from_env(env),
        jackett=JackettApi.from_config(jackett_config_path),
        misc=Miscellaneous(),
    )
