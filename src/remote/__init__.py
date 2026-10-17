from src.remote.base import RemoteService
from src.remote.fake import FakeRemote
from src.remote.restapi import RestApi as Remote

__all__ = ["FakeRemote", "Remote", "RemoteService"]
