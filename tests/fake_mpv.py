#!/usr/bin/env python3
"""A stand-in for mpv that speaks just enough of its JSON IPC protocol for tests.

It opens the socket named by --input-ipc-server, logs every command it is
sent next to the socket, and answers ``loadfile`` the way mpv does: a
start-file event, then property changes. Loading a path that contains
"missing" ends with an error; one that contains "short" ends on its own.
"""

import json
import os
import socket
import sys


def main() -> None:
    path = next(arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--input-ipc-server="))
    log = open(os.path.join(os.path.dirname(path), "commands.log"), "w")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    conn, _ = server.accept()
    buffer = b""

    def send(message: dict) -> None:
        conn.sendall((json.dumps(message) + "\n").encode())

    while True:
        chunk = conn.recv(4096)
        if not chunk:
            break
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            command = json.loads(line)["command"]
            log.write(json.dumps(command) + "\n")
            log.flush()
            send({"error": "success", "data": None})
            if command[0] == "quit":
                return
            if command[0] == "loadfile":
                target = command[1]
                send({"event": "start-file", "playlist_entry_id": 1})
                if "missing" in target:
                    send({"event": "end-file", "reason": "error", "file_error": "loading failed"})
                    continue
                send({"event": "property-change", "id": 2, "name": "duration", "data": 123.5})
                send({"event": "property-change", "id": 3, "name": "media-title", "data": "A Title From The Stream"})
                send({"event": "property-change", "id": 4, "name": "metadata", "data": {"Artist": "Some Band"}})
                send({"event": "property-change", "id": 1, "name": "time-pos", "data": 1.25})
                if "short" in target:
                    send({"event": "end-file", "reason": "eof"})
            elif command[0] == "seek":
                send({"event": "property-change", "id": 1, "name": "time-pos", "data": float(command[1])})


if __name__ == "__main__":
    main()
