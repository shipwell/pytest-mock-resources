from typing import List

import pytest

from pytest_mock_resources.compat import pymongo
from pytest_mock_resources.container.base import get_container
from pytest_mock_resources.container.mongo import MongoConfig
from pytest_mock_resources.credentials import Credentials
from pymongo.errors import OperationFailure


@pytest.fixture(scope="session")
def pmr_mongo_config():
    """Override this fixture with a :class:`MongoConfig` instance to specify different defaults.

    Examples:
        >>> @pytest.fixture(scope='session')
        ... def pmr_mongo_config():
        ...     return MongoConfig(image="mongo:3.4", root_database="foo")
    """
    return MongoConfig()


@pytest.fixture(scope="session")
def pmr_mongo_container(pytestconfig, pmr_mongo_config):
    yield from get_container(pytestconfig, pmr_mongo_config)


def create_mongo_fixture(scope="function"):
    """Produce a mongo fixture.

    Any number of fixture functions can be created. Under the hood they will all share the same
    database server.

    Arguments:
        scope: Passthrough pytest's fixture scope.
    """

    @pytest.fixture(scope=scope)
    def _(pmr_mongo_container, pmr_mongo_config):
        return _create_clean_database(pmr_mongo_config)

    return _


def _create_clean_database(config: MongoConfig):
    if _replication_enabled(config):
        with pymongo.MongoClient(config.host, config.port, directConnection=True) as repl_client:
            container_args: List[str] = config.container_args
            repl_config = {
                "_id": container_args[container_args.index("--replSet") + 1],
                "members": [{"_id": 0, "host": f"{config.host}:{config.port}"}],
            }

            replset_initialized = False
            try:
                repl_status = repl_client.admin.command('replSetGetStatus')
                replset_initialized = bool(repl_status.get('ok', 0))
            except OperationFailure as of:
                if of.code == 94: # no replset config has been recevied
                    replset_initialized = False
                else:
                    raise of

            if not replset_initialized:
                repl_client.admin.command(
                    "replSetInitiate",
                    repl_config,
                    read_preference=pymongo.ReadPreference.PRIMARY_PREFERRED,
                )

    root_client = pymongo.MongoClient(config.host, config.port, timeoutMS=3000)
    root_db = root_client[config.root_database]

    # Create a collection called `pytestMockResourceDbs' in the admin tab if not already created.
    db_collection = root_db["pytestMockResourcesDbs"]

    # Create a Document in the `pytestMockResourcesDbs` collection.
    result = db_collection.insert_one({})

    #  Create a database where the name is equal to that ID.
    db_id = str(result.inserted_id)
    new_database = root_client[db_id]

    #  Create a user as that databases owner
    password = "password"  # noqa: S105
    new_database.command("createUser", db_id, pwd=password, roles=["dbOwner"])

    #  pass back an authenticated db connection
    limited_client = pymongo.MongoClient(
        config.host, config.port, username=db_id, password=password, authSource=db_id
    )
    limited_db = limited_client[db_id]

    Credentials.assign_from_credentials(
        limited_db,
        drivername="mongodb",
        host=config.host,
        port=config.port,
        username=db_id,
        password=password,
        database=db_id,
    )
    return limited_db


def _replication_enabled(config: MongoConfig):
    return any(arg == "--replSet" for arg in config.container_args or [])
