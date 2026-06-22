from functools import lru_cache

from pymongo import MongoClient

from .config import MONGO_POSITION_URI, MONGO_RQ_URI, POSITION_COLLECTION, POSITION_DB, RQ_DB


@lru_cache(maxsize=1)
def position_client() -> MongoClient:
    return MongoClient(MONGO_POSITION_URI, serverSelectionTimeoutMS=8000)


@lru_cache(maxsize=1)
def rq_client() -> MongoClient:
    return MongoClient(MONGO_RQ_URI, serverSelectionTimeoutMS=8000)


def position_col(strategy_tag: str | None = None):
    name = (strategy_tag or POSITION_COLLECTION).strip()
    return position_client()[POSITION_DB][name]


def rq_db():
    return rq_client()[RQ_DB]
