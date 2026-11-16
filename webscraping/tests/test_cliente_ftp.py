"""ClienteFtp com ``ftplib.FTP`` simulado: queda de conexão, REST e erro permanente."""

import ftplib
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from coletor.cliente_ftp import ClienteFtp
from coletor.erros import ErroColeta
from coletor.modelos import Tarefa

CONTEUDO = b"0123456789" * 100


class FtpQueCai:
    """Na primeira transferência entrega metade e derruba a conexão."""

    sessoes = 0
    deslocamentos: list[int | None] = []

    def __init__(self, host, timeout, encoding):
        FtpQueCai.sessoes += 1

    def login(self):
        pass

    def voidcmd(self, comando):
        return "200 OK"

    def close(self):
        pass

    def retrbinary(self, comando, callback, blocksize, rest=None):
        if comando.endswith("inexistente.dbc"):
            raise ftplib.error_perm("550 arquivo não encontrado")
        FtpQueCai.deslocamentos.append(rest)
        inicio = rest or 0
        if len(FtpQueCai.deslocamentos) == 1:
            callback(CONTEUDO[:500])
            raise EOFError("conexão encerrada pelo servidor")
        callback(CONTEUDO[inicio:])


class TestClienteFtp(unittest.TestCase):
    def setUp(self):
        FtpQueCai.sessoes, FtpQueCai.deslocamentos = 0, []
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.parte = Path(self._tmp.name, "x.dbc.parte")
        for alvo, falso in (("coletor.cliente_ftp.ftplib.FTP", FtpQueCai), ("coletor.cliente_ftp.time.sleep", lambda s: None)):
            patcher = mock.patch(alvo, falso)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _tarefa(self, nome: str) -> Tarefa:
        return Tarefa(url=f"ftp://ftp.exemplo/d/{nome}", destino=Path(nome), dataset="T")

    def test_reconecta_e_retoma_com_rest(self):
        with self.assertLogs("coletor.cliente_ftp", "WARNING"):
            ClienteFtp().baixar(self._tarefa("x.dbc"), self.parte, threading.Event())
        self.assertEqual(self.parte.read_bytes(), CONTEUDO)
        self.assertEqual(FtpQueCai.deslocamentos, [None, 500])  # 2ª tentativa pede REST 500
        self.assertEqual(FtpQueCai.sessoes, 2)  # a conexão quebrada foi descartada

    def test_erro_permanente_nao_e_repetido(self):
        with self.assertRaises(ErroColeta):
            ClienteFtp().baixar(self._tarefa("inexistente.dbc"), self.parte, threading.Event())
        self.assertEqual(FtpQueCai.sessoes, 1)


if __name__ == "__main__":
    unittest.main()
