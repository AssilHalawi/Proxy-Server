# ==================================================
# file: proxy_https.py
# primary contributor: ali rida
# contributions:
# - https connect tunnel handling
# - bidirectional tcp relay using select()
# team support:
# - assil halawi (integration in proxy_server.py handle_client)
# ==================================================

from __future__ import annotations

import select
import socket
from typing import Tuple


BUFFER_SIZE = 4096


def tunnel_connect(
    client_sock: socket.socket,
    client_addr: Tuple[str, int],
    host: str,
    port: int,
) -> None:
    """
    ali rida: handle an HTTP CONNECT request by establishing a transparent TCP tunnel.

    Flow:
      client ←TCP→ proxy ←TCP→ remote server

    The proxy does NOT decrypt the TLS traffic — it simply forwards raw bytes
    in both directions. This satisfies the HTTPS forwarding requirement without
    implementing a MITM CA.
    """
    # open a new tcp socket to the remote server (same as what the browser would do)
    remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # timeout prevents hanging forever if the remote server is unreachable
    remote.settimeout(10)
    remote.connect((host, port))

    # ali rida: tell the client that the tunnel is ready
    # after this 200, the client starts its TLS handshake — we just relay those bytes
    client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")

    # hand off to the bidirectional relay loop
    _relay_bidirectional(client_sock, remote)


def _relay_bidirectional(a: socket.socket, b: socket.socket) -> None:
    """
    ali rida: copy bytes between socket a and socket b in both directions until
    one side closes the connection or an error occurs.

    Uses select() so a single thread can multiplex both sockets efficiently
    without blocking on one side while the other has data waiting.
    """
    # non-blocking mode is required for select() to work correctly here
    a.setblocking(False)
    b.setblocking(False)

    sockets = [a, b]
    try:
        while True:
            # wait up to 30 seconds for either socket to become readable
            rlist, _wlist, xlist = select.select(sockets, [], sockets, 30)

            if xlist:
                # exception on one of the sockets — stop the tunnel
                return
            if not rlist:
                # 30-second idle timeout — close the tunnel
                return

            for s in rlist:
                try:
                    data = s.recv(BUFFER_SIZE)
                except BlockingIOError:
                    # race condition: select said readable but recv would block — retry
                    continue

                if not data:
                    # empty read means the peer closed the connection cleanly
                    return

                # forward bytes to the other socket (client→server or server→client)
                other = b if s is a else a
                other.sendall(data)
    finally:
        try:
            # close the remote socket; the client socket is closed by handle_client's finally block
            b.close()
        except Exception:
            pass
