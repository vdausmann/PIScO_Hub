from flask_sock import Sock


def create_socket(app):
    sock = Sock(app)
    clients = set()

    @sock.route('/ws')
    def workflow_ws(ws):
        clients.add(ws)

        try:
            while True:
                ws.receive()

        except Exception:
            pass
        finally:
            clients.remove(ws)

    return sock, clients
