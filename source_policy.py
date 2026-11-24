import ipaddress
import os
from queue import Empty, Queue
import re
import socket
from threading import Thread
from http.client import HTTPConnection, HTTPSConnection
from time import monotonic
from urllib.parse import urlsplit
from urllib.request import (
    HTTPHandler, HTTPRedirectHandler, HTTPSHandler, ProxyHandler, build_opener,
)


PORTAL = "opendata.trudvsem.ru"
HOST = re.compile(r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")


def parse_source(url):
    if any(ord(char) < 33 or ord(char) == 127 for char in url) or "\\" in url:
        raise ValueError("URL источника содержит недопустимые символы.")
    try:
        source = urlsplit(url)
        host = source.hostname
        port = source.port
    except ValueError:
        raise ValueError("Некорректный адрес источника.") from None
    if source.scheme not in ("http", "https") or not host or source.fragment or (
        source.username is not None or source.password is not None
    ):
        raise ValueError("Укажите URL HTTP(S) без учётных данных и фрагмента.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if any(not label or len(label) > 63 or not HOST.fullmatch(label) for label in host.split(".")):
            raise ValueError("Некорректное имя хоста источника.") from None
    if port == 0 or source.netloc.endswith(":"):
        raise ValueError("Некорректный порт источника.")
    return source, (source.scheme, host, port or (443 if source.scheme == "https" else 80))


def configured_origins(name):
    origins = set()
    for value in os.environ.get(name, "").split(","):
        if not value.strip():
            continue
        source, origin = parse_source(value.strip())
        if source.path not in ("", "/") or source.query:
            raise ValueError(f"{name}: укажите только схему, хост и порт.")
        origins.add(origin)
    return origins


def source_settings():
    allowed = {(scheme, PORTAL, port) for scheme, port in (("https", 443), ("http", 80))}
    allowed.update(configured_origins("VACANCY_ALLOWED_ORIGINS"))
    private = configured_origins("VACANCY_PRIVATE_ORIGINS")
    return allowed | private, private, os.environ.get("VACANCY_ALLOW_HTTP") == "1"


def validate_source(url, settings):
    source, origin = parse_source(url)
    allowed, _, allow_http = settings
    if origin not in allowed:
        raise ValueError("Источник отсутствует в списке разрешённых адресов.")
    if source.scheme == "http" and not allow_http:
        raise ValueError("HTTP требует явной настройки VACANCY_ALLOW_HTTP=1.")
    return source, origin


def is_public(address):
    ip = ipaddress.ip_address(address)
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def check_addresses(addresses, allow_private):
    if not addresses or (not allow_private and any(
        not is_public(entry[4][0]) for entry in addresses
    )):
        raise ValueError("DNS источника вернул адрес, запрещённый политикой сети.")


def resolve_addresses(host, port, timeout):
    result = Queue(maxsize=1)

    def resolve():
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError as error:
            result.put(error)

    Thread(target=resolve, daemon=True).start()
    try:
        addresses = result.get(timeout=timeout)
    except Empty:
        raise TimeoutError("Истекло время DNS-запроса.") from None
    if isinstance(addresses, OSError):
        raise addresses
    return addresses


def connect_checked(address, timeout, source_address=None, *, allow_private, deadline):
    host, port = address
    connection_deadline = min(deadline, monotonic() + timeout)
    remaining = connection_deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("Истекло время соединения.")
    addresses = resolve_addresses(host, port, remaining)
    check_addresses(addresses, allow_private)
    error = None
    for family, kind, proto, _, target in addresses:
        remaining = connection_deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("Истекло время соединения.")
        connection = socket.socket(family, kind, proto)
        try:
            connection.settimeout(min(timeout, remaining))
            if source_address:
                connection.bind(source_address)
            connection.connect(target)
            return connection
        except OSError as exc:
            connection.close()
            error = exc
    raise error


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise ValueError(f"Перенаправление HTTP {code} запрещено.")


def open_source(request, *, timeout, settings, deadline):
    source, origin = validate_source(request.full_url, settings)

    def connect(address, timeout, source_address=None):
        return connect_checked(address, timeout, source_address,
                               allow_private=origin in settings[1], deadline=deadline)

    class HTTP(HTTPHandler):
        def http_open(self, req):
            return self.do_open(connection_factory(HTTPConnection), req)

    class HTTPS(HTTPSHandler):
        def https_open(self, req):
            return self.do_open(connection_factory(HTTPSConnection), req, context=self._context)

    def connection_factory(kind):
        def create(*args, **kwargs):
            connection = kind(*args, **kwargs)
            connection._create_connection = connect
            return connection
        return create

    handler = HTTPS() if source.scheme == "https" else HTTP()
    return build_opener(ProxyHandler({}), NoRedirect(), handler).open(request, timeout=timeout)
