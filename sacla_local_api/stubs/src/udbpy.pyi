"""SACLA UserDatabaseAPI, udbpy 2.0.1 – store your own analysis results in the UDB.

NOTE: there is no Python 3.9 wheel of udbpy; this stub is for type checking only
and ``import udbpy`` fails locally. On the HPC it exists for Python 3.7.

Typical use::

    conn = udbpy.connector()                 # authenticate from your auth file
    aid = conn.get_new_aid(0)
    env = conn.create_env(aid)
    env.add_data(taghi, tag, 1, bytearray(b"..."))
    data = env.show_data(taghi, tag, 1)

All byte payloads must be ``bytearray`` (bytes raises TypeError).
Hand-written stub from UserDatabaseAPI/2.0.1/python/udbpy.cpp.
"""
from typing import Optional, final, overload

class APIError(Exception):
    """Raised on any API failure. ``args == (error_code, error_name)``;
    compare ``args[0]`` with the ``UDB_*`` constants."""
    args: tuple[int, str]

@final
class connector:
    """Connection to the User DataBase."""
    @overload
    def __init__(self) -> None:
        """Authenticate with the UDB auth file (udb_MakeAuthFile)."""
    @overload
    def __init__(self, name: str, passwd: str, /) -> None:
        """Authenticate with explicit user name and password."""
    def get_new_aid(self, start_analy_id: int, /) -> int:
        """Recommended new analysis ID at or after ``start_analy_id``."""
    def get_env(self, analy_id: int, /) -> env:
        """Existing environment of analysis ID ``analy_id``."""
    def create_env(self, analy_id: int, /) -> env:
        """Create the environment for ``analy_id``."""
    def drop_env(self, analy_id: int, /) -> None:
        """Delete the environment and all its data."""
    def defrag_env(self, analy_id: int, /) -> None: ...
    def get_space_size(self) -> tuple[int, int, int]:
        """(total, used, free) size of your UDB space."""
    def get_aidlist(self, server_type: int, /) -> tuple[int, ...]:
        """Analysis IDs; server_type 1 online, 2 offline, 3 both."""
    def get_server_type(self, analy_id: int, /) -> int:
        """1 online UDB, 2 offline UDB."""
    def get_aid_rec_count(self, analy_id: int, analy_type: int, /) -> int:
        """Record count; analy_type 1 parameters, 2 data."""
    def get_aid_size(self, analy_id: int, analy_type: int, /) -> int:
        """Size; analy_type 1 parameters, 2 data."""
    def get_aid_create_time(self, analy_id: int, analy_type: int, /) -> float: ...
    def get_aid_update_time(self, analy_id: int, analy_type: int, /) -> float: ...
    def get_user_type(self, udb_user_name: str, /) -> int:
        """1 UUR, 2 UU, 3 ROU."""
    def change_group(self, udb_group_name: str, /) -> None: ...
    def get_current_group_name(self) -> str: ...
    def get_default_group_name(self) -> str: ...
    def grant_read_auth(self, udb_user_name: str, analy_id: int, /) -> None: ...
    def revoke_read_auth(self, udb_user_name: str, analy_id: int, /) -> None: ...
    def get_group_name_list(self, udb_group_type: int, /) -> tuple[str, ...]:
        """udb_group_type: 0 all, 1 default, 2 read-only."""
    def get_user_name_list(self, udb_user_type: int, analy_id: int = ..., /) -> tuple[str, ...]:
        """udb_user_type: 0 all, 1 UUR, 2 UU, 3 ROU."""
    def get_granted_status_list(self, analy_id_list: tuple[int, ...], /) -> tuple[int, ...]:
        """Per analysis ID: -1 not existing, 0 not granted, 1 granted."""
    def get_granted_aid_list(self, udb_user_name: Optional[str] = None, /) -> tuple[int, ...]: ...

@final
class env:
    """UDB environment of one analysis ID (normally from connector.get_env/create_env)."""
    def __init__(self, conn: connector, analy_id: int, /) -> None: ...
    # --- analysis data (per tag)
    def add_data(self, tagHi: int, tagLo: int, valid_flag: int, data: bytearray, /) -> None: ...
    def show_data(self, tagHi: int, tagLo: int, valid_flag: int, /) -> bytearray: ...
    def mod_data(self, tagHi: int, tagLo: int, data: bytearray, /) -> None: ...
    def mod_data_valid_flag(self, tagHi: int, tagLo: int, valid_flag: int, /) -> None: ...
    def show_valid_flag(self, tagHi: int, tagLo: int, /) -> tuple[int, bytes]:
        """(valid_flag, data)."""
    def del_data(self, tagHi: int, tagLo: int, /) -> None: ...
    def del_data_in_bulk(self, tagHi: int, /) -> None: ...
    def del_data_by_valid_flag(self, tagHi: int, valid_flag: int, /) -> None: ...
    def del_data_in_range(self, tagHi: int, tagLoStart: int, tagLoStop: int, /) -> None: ...
    # --- analysis parameters (per tag range)
    def add_para(self, tagHi: int, tagLoStart: int, tagLoStop: int, data: bytearray, /) -> None: ...
    def show_para(self, tagHi: int, tagLo: int, /) -> bytearray:
        """Parameter valid at tagLo (tagLoStart <= tagLo <= tagLoStop)."""
    def mod_para(self, tagHi: int, tagLoStart: int, data: bytearray, /) -> None: ...
    def mod_para_stoptag(self, tagHi: int, tagLoStart: int, tagLoStop: int, /) -> None: ...
    def del_para(self, tagHi: int, tagLoStart: int, /) -> None: ...
    # --- listings
    def show_tag_hi_list(self) -> tuple[int, ...]: ...
    def show_tag_range_list(self, tagHi: int, /) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """(tagLoStart_list, tagLoStop_list) of the parameters."""
    # --- cursors: open_*, then retrieve_* until it returns None, then close_*
    def open_tag_hi_list(self) -> None: ...
    def retrieve_tag_hi(self) -> Optional[int]: ...
    def close_tag_hi_list(self) -> None: ...
    def open_tag_range_list(self, tagHi: int, /) -> None: ...
    def retrieve_tag_range(self) -> Optional[tuple[int, int]]: ...
    def close_tag_range_list(self) -> None: ...
    def open_data_list(self, tagHi: int, tagLoFirst: int, tagLoLast: int, valid_flag: int, /) -> None: ...
    def retrieve_data(self) -> Optional[tuple[int, bytes]]:
        """(tagLo, data) or None at the end."""
    def close_data_list(self) -> None: ...
    def open_valid_flag_list(self, tagHi: int, tagLoFirst: int, tagLoLast: int, /) -> None: ...
    def retrieve_valid_flag(self) -> Optional[tuple[int, int, bytes]]:
        """(tagLo, valid_flag, data) or None at the end."""
    def close_valid_flag_list(self) -> None: ...

def get_api_version() -> tuple[int, int, int]:
    """(major, minor, patch) of the udbpy API."""
