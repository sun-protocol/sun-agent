from typing import (
    IO,
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
)

from requests import (
    PreparedRequest,
    Response,
    Session,
)
from requests.auth import AuthBase
from requests.cookies import RequestsCookieJar
from typing_extensions import TypeAlias

# Define common types alias
_ParamsMappingKeyType: TypeAlias = str | bytes | int | float
_ParamsMappingValueType: TypeAlias = str | bytes | int | float | Iterable[str | bytes | int | float] | None
if TYPE_CHECKING:
    from _typeshed import SupportsItems, SupportsRead

    _ParamsType: TypeAlias = (
        SupportsItems[_ParamsMappingKeyType, _ParamsMappingValueType]
        | tuple[_ParamsMappingKeyType, _ParamsMappingValueType]
        | Iterable[tuple[_ParamsMappingKeyType, _ParamsMappingValueType]]
        | str
        | bytes
        | None
    )
    _DataType: TypeAlias = (
        Iterable[bytes]
        | str
        | bytes
        | SupportsRead[str | bytes]
        | list[tuple[Any, Any]]
        | tuple[tuple[Any, Any], ...]
        | Mapping[Any, Any]
        | None
    )
    _FileContent: TypeAlias = SupportsRead[str | bytes] | str | bytes
else:
    _ParamsType: TypeAlias = (
        Mapping[_ParamsMappingKeyType, _ParamsMappingValueType]
        | tuple[_ParamsMappingKeyType, _ParamsMappingValueType]
        | Iterable[tuple[_ParamsMappingKeyType, _ParamsMappingValueType]]
        | str
        | bytes
        | None
    )
    _DataType: TypeAlias = (
        Iterable[bytes]
        | str
        | bytes
        | IO[str]
        | IO[bytes]
        | list[tuple[Any, Any]]
        | tuple[tuple[Any, Any], ...]
        | Mapping[Any, Any]
        | None
    )
    _FileContent: TypeAlias = IO[str] | IO[bytes] | str | bytes

_TextMapping: TypeAlias = MutableMapping[str, str]
_HeadersType: TypeAlias = Mapping[str, str | bytes | None] | None
_CookiesType: TypeAlias = RequestsCookieJar | _TextMapping | None
_FileName: TypeAlias = str | None
_FileContentType: TypeAlias = str
_FileCustomHeaders: TypeAlias = Mapping[str, str]
_FileSpecTuple2: TypeAlias = tuple[_FileName, _FileContent]
_FileSpecTuple3: TypeAlias = tuple[_FileName, _FileContent, _FileContentType]
_FileSpecTuple4: TypeAlias = tuple[_FileName, _FileContent, _FileContentType, _FileCustomHeaders]
_FileSpec: TypeAlias = _FileContent | _FileSpecTuple2 | _FileSpecTuple3 | _FileSpecTuple4
_FilesType: TypeAlias = Mapping[str, _FileSpec] | Iterable[tuple[str, _FileSpec]] | None
_AuthType: TypeAlias = tuple[str, str] | AuthBase | Callable[[PreparedRequest], PreparedRequest] | None
_TimeoutType: TypeAlias = float | tuple[float, float] | tuple[float, None] | None
_Hook: TypeAlias = Callable[[Response], Any]
_HooksType: TypeAlias = Mapping[str, Iterable[_Hook] | _Hook] | None
_CertType: TypeAlias = str | tuple[str, str] | None


class TimeoutSession(Session):
    def __init__(self, timeout: _TimeoutType = None) -> None:
        super().__init__()
        self.timeout = timeout  # Default timeout for all requests

    def request(
        self,
        method: str | bytes,
        url: str | bytes,
        params: _ParamsType = None,
        data: _DataType = None,
        headers: _HeadersType = None,
        cookies: _CookiesType = None,
        files: _FilesType = None,
        auth: _AuthType = None,
        timeout: _TimeoutType = None,
        allow_redirects: bool = True,
        proxies: _TextMapping | None = None,
        hooks: _HooksType = None,
        stream: bool | None = None,
        verify: bool | str | None = None,
        cert: _CertType = None,
        json: Any = None,
    ) -> Response:
        # Use the session's timeout if not specified in the request
        if timeout is None:
            timeout = self.timeout
        return super().request(
            method,
            url,
            params,
            data,
            headers,
            cookies,
            files,
            auth,
            timeout,
            allow_redirects,
            proxies,
            hooks,
            stream,
            verify,
            cert,
            json,
        )
