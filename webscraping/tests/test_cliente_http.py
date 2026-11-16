"""ClienteHttp contra um servidor local: robots.txt, Range, retomada e HTML de erro."""

import re
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from coletor.cliente_http import ClienteHttp, LimitadorPorHost
from coletor.erros import BloqueadoPorRobots, ErroColeta
from coletor.modelos import Tarefa

CONTEUDO = bytes(range(256)) * 40  # 10 KiB binários


class _Manipulador(BaseHTTPRequestHandler):
    """Serve ``/dados.bin`` com suporte a Range, um robots.txt e uma página HTML."""

    def do_GET(self):  # noqa: N802 — nome exigido por BaseHTTPRequestHandler
        if self.path == "/robots.txt":
            self._responder(200, b"User-agent: *\nDisallow: /privado\n", "text/plain")
        elif self.path == "/dados.bin":
            casamento = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
            if casamento:
                inicio = int(casamento[1])
                fim = int(casamento[2]) if casamento[2] else len(CONTEUDO) - 1
                self._responder(206, CONTEUDO[inicio:fim + 1], "application/octet-stream",
                                {"Content-Range": f"bytes {inicio}-{fim}/{len(CONTEUDO)}"})
            else:
                self._responder(200, CONTEUDO, "application/octet-stream")
        elif self.path == "/erro.zip":
            self._responder(200, b"<html>manutencao</html>", "text/html")
        else:
            self._responder(404, b"", "text/plain")

    def _responder(self, status, corpo, tipo, extras=None):
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        for nome, valor in (extras or {}).items():
            self.send_header(nome, valor)
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *args):  # silencia o log padrão do servidor
        pass


class TestClienteHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.servidor = ThreadingHTTPServer(("127.0.0.1", 0), _Manipulador)
        cls.base = f"http://127.0.0.1:{cls.servidor.server_port}"
        threading.Thread(target=cls.servidor.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.servidor.shutdown()
        cls.servidor.server_close()

    def setUp(self):
        self.cliente = ClienteHttp(user_agent="teste/1.0", limitador=LimitadorPorHost(0.0, {}))
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.parte = Path(self._tmp.name, "dados.bin.parte")

    def _tarefa(self, caminho: str) -> Tarefa:
        return Tarefa(url=self.base + caminho, destino=Path(caminho.lstrip("/")), dataset="T")

    def test_tamanho_remoto_por_range(self):
        self.assertEqual(self.cliente.tamanho_remoto(self.base + "/dados.bin"), len(CONTEUDO))

    def test_retoma_download_parcial(self):
        self.parte.write_bytes(CONTEUDO[:3000])
        self.cliente.baixar(self._tarefa("/dados.bin"), self.parte, threading.Event())
        self.assertEqual(self.parte.read_bytes(), CONTEUDO)

    def test_robots_bloqueia_caminho_proibido(self):
        with self.assertRaises(BloqueadoPorRobots):
            self.cliente.requisitar("GET", self.base + "/privado/x.csv")

    def test_rejeita_html_no_lugar_do_arquivo(self):
        with self.assertRaises(ErroColeta):
            self.cliente.baixar(self._tarefa("/erro.zip"), self.parte, threading.Event())

    def test_interrupcao_preserva_parte(self):
        parar = threading.Event()
        parar.set()
        with self.assertRaises(InterruptedError):
            self.cliente.baixar(self._tarefa("/dados.bin"), self.parte, parar)


if __name__ == "__main__":
    unittest.main()
