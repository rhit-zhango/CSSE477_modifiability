# Copyright (C) 2007 Giampaolo Rodola' <g.rodola@gmail.com>.
# Use of this source code is governed by MIT license that can be
# found in the LICENSE file.

"""
Start a standalone anonymous FTP server from the command line:

$ python3 -m pyftpdlib
"""

import argparse
import codecs
import logging
import os
import sys
import time
import warnings

from . import servers
from .authorizers import DummyAuthorizer
from .filesystems import AbstractedFS
from .handlers import FTPHandler
from .handlers import DTPHandler
from .log import config_logging
from .log import logger
from .prefork import cpu_count
from .utils import hilite
from .utils import term_supports_colors

try:
    from .handlers import TLS_FTPHandler
except ImportError:
    TLS_FTPHandler = None  # requires PyOpenSSL


DEFAULT_PORT = 2121

MANIFEST_PREFIX = b"# EXAM_ID: 477-FINAL"
MANIFEST_ERROR = "550 Exam Integrity Manifest Header missing"


def _make_manifest():
    header = (
        f"# EXAM_ID: 477-FINAL | TIMESTAMP: {int(time.time())} | ENQ: NONE"
    ).encode("ascii")
    if len(header) > 63:
        raise ValueError("Exam manifest content does not fit in 64 bytes")
    return header.ljust(63, b" ") + b"\n"


class ManifestFileWrapper:
    """Inject a manifest on downloads and strip it from validated uploads."""

    def __init__(self, file_object, mode):
        if mode not in ("rb", "wb"):
            raise ValueError("mode must be 'rb' or 'wb'")
        self.file_object = file_object
        self.mode = mode
        self.header_injected = False
        self.header_verified = False
        self.buffer = bytearray()
        self.manifest_error = False

    def read(self, size=-1):
        if self.mode != "rb":
            raise OSError("File is not open for reading")
        if not self.header_injected:
            self.header_injected = True
            return _make_manifest() + self.file_object.read(size - 64)
        return self.file_object.read(size)

    def write(self, chunk):
        if self.mode != "wb":
            raise OSError("File is not open for writing")
        if self.header_verified:
            return self.file_object.write(chunk)

        self.buffer.extend(chunk)
        if len(self.buffer) < 64:
            return len(chunk)
        if not self.buffer.startswith(MANIFEST_PREFIX):
            self.manifest_error = True
            raise OSError(1, MANIFEST_ERROR)

        self.header_verified = True
        payload = self.buffer[64:]
        self.buffer.clear()
        if payload:
            self.file_object.write(payload)
        return len(chunk)

    def close(self):
        self.file_object.close()

    def __getattr__(self, name):
        return getattr(self.file_object, name)


class ManifestFS(AbstractedFS):
    """Wrap file objects used by FTP transfers."""

    def open(self, file, mode):
        opened_file = open(file, mode)
        if mode == "rb":
            return ManifestFileWrapper(opened_file, "rb")
        if mode in ("wb", "ab", "r+b"):
            return ManifestFileWrapper(opened_file, "wb")
        return opened_file


_manifest_dtp_handlers = {}


def _get_manifest_dtp_handler(base_handler):
    """Return a DTP handler that reports manifest failures as FTP 550."""
    if base_handler not in _manifest_dtp_handlers:
        class ManifestDTPHandler(base_handler):
            def handle_error(self):
                if getattr(self.file_obj, "manifest_error", False):
                    self._resp = (MANIFEST_ERROR, logger.warning)
                    self.close()
                    return
                super().handle_error()

            def close(self):
                file_object = self.file_obj
                if (
                    getattr(self, "receive", False)
                    and isinstance(file_object, ManifestFileWrapper)
                    and not file_object.header_verified
                    and not file_object.manifest_error
                ):
                    file_object.manifest_error = True
                    self.transfer_finished = False
                    self._resp = (MANIFEST_ERROR, logger.warning)
                super().close()

        _manifest_dtp_handlers[base_handler] = ManifestDTPHandler
    return _manifest_dtp_handlers[base_handler]


class ColorHelpFormatter(argparse.HelpFormatter):
    def start_section(self, heading):  # titles / groups
        heading = f"{hilite(heading.capitalize(), 'orange')}"
        super().start_section(heading)

    def _format_action_invocation(self, action):
        # colorize the flag part (e.g. "-i, --interface")
        if not action.option_strings:
            default = self._metavar_formatter(action, action.dest)(1)[0]
            return f"{hilite(default, 'white')}"

        parts = []
        for option in action.option_strings:
            parts.append(f"{hilite(option, 'lightblue')}")

        if action.nargs != 0:
            metavar = self._format_args(
                action, self._get_default_metavar_for_optional(action)
            )
            parts[-1] += " " + f"{hilite(metavar, 'green')}"

        return ", ".join(parts)


def parse_encoding(value):
    try:
        codecs.lookup(value)
    except LookupError:
        raise argparse.ArgumentTypeError(
            f"unknown encoding: {value!r}"
        ) from None
    return value


def parse_port_range(value):
    try:
        start, stop = value.split("-")
        start, stop = int(start), int(stop)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid port range: {value!r} (expected FROM-TO)"
        ) from None
    if not (1 <= start <= 65535 and 1 <= stop <= 65535):
        raise argparse.ArgumentTypeError(
            "port numbers must be between 1 and 65535"
        )
    if start >= stop:
        raise argparse.ArgumentTypeError(
            f"start port must be <= stop port (got {start}-{stop})"
        )
    return list(range(start, stop + 1))


def parse_concurrency(value):
    mapping = {
        "async": (servers.FTPServer, 1),
        "multi-thread": (servers.ThreadedFTPServer, 1),
    }

    if hasattr(servers, "MultiprocessFTPServer"):
        mapping["multi-proc"] = (servers.MultiprocessFTPServer, 1)

    if os.name == "posix":
        mapping["pre-fork"] = (servers.FTPServer, cpu_count())
        if cpu_count() == 1:
            warnings.warn(
                "ignoring 'pre-fork' concurrency model: there's only 1 CPU",
                stacklevel=2,
            )

    if value not in mapping:
        raise argparse.ArgumentTypeError(
            f"invalid concurrency {value!r}; choose between: "
            f"{', '.join([repr(x) for x in mapping])}"
        )

    server_class, ncpus = mapping[value]
    return (server_class, ncpus)


def parse_file_path(value):
    if not os.path.isfile(value):
        raise argparse.ArgumentTypeError(f"file {value!r} does not exist")
    return value


def parse_args(args=None):
    usage = "python3 -m pyftpdlib [options]"
    parser = argparse.ArgumentParser(
        usage=usage,
        description=main.__doc__,
        formatter_class=(
            ColorHelpFormatter
            if term_supports_colors()
            else argparse.HelpFormatter
        ),
    )

    # --- most important opts

    group_main = parser.add_argument_group("Main options")
    group_main.add_argument(
        "-i",
        "--interface",
        default=None,
        metavar="ADDRESS",
        help="specify the interface to run on (default: all interfaces)",
    )
    group_main.add_argument(
        "-p",
        "--port",
        type=int,
        default=DEFAULT_PORT,
        metavar="PORT",
        help=f"specify port number to run on (default: {DEFAULT_PORT})",
    )
    group_main.add_argument(
        "-w",
        "--write",
        action="store_true",
        default=False,
        help="grants write access for logged in user (default: read-only)",
    )
    group_main.add_argument(
        "-d",
        "--directory",
        default=os.getcwd(),
        metavar="PATH",
        help="specify the directory to share (default: current directory)",
    )
    group_main.add_argument(
        "-n",
        "--nat-address",
        default=None,
        metavar="ADDRESS",
        help="the NAT address to use for passive connections",
    )
    group_main.add_argument(
        "-r",
        "--range",
        type=parse_port_range,
        default=None,
        metavar="FROM-TO",
        help=(
            "the range of TCP ports to use for passive "
            "connections (e.g. -r 8000-9000)"
        ),
    )
    group_main.add_argument(
        "-D",
        "--debug",
        action="store_true",
        help="enable DEBUG logging level",
    )
    group_main.add_argument(
        "-u",
        "--username",
        type=str,
        default=None,
        help=(
            "specify username to login with (anonymous login "
            "will be disabled and password required "
            "if supplied)"
        ),
    )
    group_main.add_argument(
        "-P",
        "--password",
        type=str,
        default=None,
        help=(
            "specify a password to login with (username required to be useful)"
        ),
    )
    group_main.add_argument(
        "--concurrency",
        type=parse_concurrency,
        default="async",
        help=(
            "the FTP server concurrency model to use, either 'async'"
            " (default), 'pre-fork', 'multi-thread' or 'multi-proc'"
        ),
    )

    # --- TLS opts

    group_tls = parser.add_argument_group("TLS options")
    group_tls.add_argument(
        "--tls",
        default=False,
        action="store_true",
        help=(
            "whether to enable FTPS (FTP over TLS); requires --keyfile and"
            " --certfile args"
        ),
    )
    group_tls.add_argument(
        "--keyfile",
        type=parse_file_path,
        metavar="PATH",
        help="the TLS key file",
    )
    group_tls.add_argument(
        "--certfile",
        type=parse_file_path,
        metavar="PATH",
        help="the TLS certificate file",
    )
    group_tls.add_argument(
        "--tls-control-required",
        default=False,
        action="store_true",
        help="impose TLS for the control connection (before login)",
    )
    group_tls.add_argument(
        "--tls-data-required",
        default=False,
        action="store_true",
        help="impose TLS for the data connection",
    )

    # --- less important opts

    group_misc = parser.add_argument_group("Other options")
    group_misc.add_argument(
        "--timeout",
        type=int,
        metavar="SECONDS",
        default=FTPHandler.timeout,
        help=f"connection timeout (default: {FTPHandler.timeout} seconds)",
    )
    group_misc.add_argument(
        "--banner",
        type=str,
        default=FTPHandler.banner,
        help=(
            "the message sent when client connects (default:"
            f" {FTPHandler.banner!r})"
        ),
    )
    group_misc.add_argument(
        "--permit-foreign-addresses",
        default=FTPHandler.permit_foreign_addresses,
        action="store_true",
        help=(
            "allow data connections from an IP address different than the"
            " control connection"
        ),
    )
    group_misc.add_argument(
        "--permit-privileged-ports",
        default=FTPHandler.permit_privileged_ports,
        action="store_true",
        help="allow data connections (PORT) over privileged TCP ports",
    )
    group_misc.add_argument(
        "--encoding",
        type=parse_encoding,
        default="utf-8",
        help=(
            "the encoding used for client / server communication (default:"
            f" {FTPHandler.encoding})"
        ),
    )
    group_misc.add_argument(
        "--use-localtime",
        default=False,
        action="store_true",
        help=(
            "display directory listings with the time in your local time zone"
            " (default: use GMT)"
        ),
    )
    if hasattr(os, "sendfile"):
        group_misc.add_argument(
            "--disable-sendfile",
            default=False,
            action="store_true",
            help="accepted for compatibility; sendfile() is always disabled",
        )
    group_misc.add_argument(
        "--max-cons",
        type=int,
        default=servers.FTPServer.max_cons,
        help=(
            "max number of simultaneous connections (default:"
            f" {servers.FTPServer.max_cons})"
        ),
    )
    group_misc.add_argument(
        "--max-cons-per-ip",
        type=int,
        default=servers.FTPServer.max_cons,
        help=(
            "maximum number connections from the same IP address (default:"
            " unlimited)"
        ),
    )
    group_misc.add_argument(
        "--max-login-attempts",
        type=int,
        default=FTPHandler.max_login_attempts,
        help=(
            "max number of failed authentications before disconnect (default:"
            f" {FTPHandler.max_login_attempts})"
        ),
    )

    return parser.parse_args(args)


def main(args=None):
    """Start a standalone FTP server with manifest enforcement.

    With no command-line options, use the exam server defaults: student
    credentials, a writable ./workspace directory, and port 2121.
    """
    default_exam_server = args is None and len(sys.argv) == 1
    if default_exam_server:
        os.makedirs("./workspace", exist_ok=True)
        args = [
            "--interface",
            "0.0.0.0",
            "--directory",
            os.path.abspath("./workspace"),
            "--username",
            "student",
            "--password",
            "password",
            "--write",
        ]
    opts = parse_args(args=args)

    if opts.debug:
        config_logging(level=logging.DEBUG)

    # On recent Windows versions, if address is not specified and IPv6
    # is installed the socket will listen on IPv6 by default; in this
    # case we force IPv4 instead.
    if os.name in ("nt", "ce") and not opts.interface:
        opts.interface = "0.0.0.0"

    authorizer = DummyAuthorizer()
    perm = "elradfmwMT" if opts.write else "elr"
    if opts.username:
        if not opts.password:
            raise argparse.ArgumentTypeError(
                "if username (-u) is supplied, password (-P) is required"
            )
        authorizer.add_user(
            opts.username, opts.password, opts.directory, perm=perm
        )
    else:
        authorizer.add_anonymous(opts.directory, perm=perm)

    # FTP or FTPS?
    if opts.tls:
        if TLS_FTPHandler is None:
            raise argparse.ArgumentTypeError("PyOpenSSL not installed")
        if not opts.certfile or not opts.keyfile:
            raise argparse.ArgumentTypeError(
                "--tls requires --keyfile and --certfile args"
            )
        handler = TLS_FTPHandler
        handler.certfile = opts.certfile
        handler.keyfile = opts.keyfile
        if opts.tls_control_required:
            handler.tls_control_required = True
        if opts.tls_data_required:
            handler.tls_data_required = True
    else:
        if opts.certfile or opts.keyfile:
            raise argparse.ArgumentTypeError(
                "--keyfile and --certfile args requires --tls arg"
            )
        handler = FTPHandler

    handler = type("ManifestFTPHandler", (handler,), {})

    # Configure handler.
    handler.authorizer = authorizer
    handler.abstracted_fs = ManifestFS
    handler.dtp_handler = _get_manifest_dtp_handler(handler.dtp_handler)
    handler.masquerade_address = opts.nat_address
    handler.passive_ports = opts.range
    handler.timeout = opts.timeout
    handler.dtp_handler.timeout = opts.timeout
    handler.banner = opts.banner
    handler.max_login_attempts = opts.max_login_attempts
    handler.permit_foreign_addresses = opts.permit_foreign_addresses
    handler.permit_privileged_ports = opts.permit_privileged_ports
    handler.encoding = opts.encoding
    handler.use_gmt_times = not opts.use_localtime
    handler.use_sendfile = False

    # Configure server / acceptor.
    server_class, ncpus = opts.concurrency
    server = server_class((opts.interface, opts.port), handler)
    server.max_cons = opts.max_cons
    server.max_cons_per_ip = opts.max_cons_per_ip

    # On Windows specify a timeout for the underlying select() so
    # that the server can be interrupted with CTRL + C.
    timeout = 2 if os.name == "nt" else None

    try:
        server.serve_forever(timeout=timeout, worker_processes=ncpus)
    finally:
        server.close_all()

    if args:  # only used in unit tests
        return server


if __name__ == "__main__":
    main()
