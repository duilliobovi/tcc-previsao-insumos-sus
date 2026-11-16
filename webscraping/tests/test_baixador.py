"""Baixador: gravação atômica, pulo de existentes, validação e manifesto."""

import csv
import tempfile
import threading
import unittest
from pathlib import Path

from coletor.baixador import Baixador, intercalar_por_host
from coletor.erros import SemDados
from coletor.manifesto import Manifesto
from coletor.modelos import Status, Tarefa


class TransporteFalso:
    """Grava ``conteudo`` (ou levanta ``erro``) e conta as chamadas."""

    def __init__(self, conteudo: bytes = b"dados\n", erro: Exception | None = None) -> None:
        self.conteudo, self.erro, self.chamadas = conteudo, erro, 0

    def baixar(self, tarefa: Tarefa, parte: Path, parar: threading.Event) -> None:
        self.chamadas += 1
        if self.erro:
            raise self.erro
        with parte.open("ab") as arquivo:
            arquivo.write(self.conteudo)


def tarefa(nome: str = "a.csv", tamanho: int | None = None, host: str = "h") -> Tarefa:
    return Tarefa(url=f"https://{host}/{nome}", destino=Path("DS-01_x", nome), dataset="DS-01", tamanho_esperado=tamanho)


class TestBaixador(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.destino = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _baixador(self, transporte) -> Baixador:
        return Baixador(
            destino=self.destino, transportes={"https": transporte},
            manifesto=Manifesto(self.destino / "MANIFEST.csv"), workers=2,
            conexoes_por_host={}, conexoes_padrao=1,
        )

    def test_baixa_registra_e_pula_na_segunda_vez(self):
        transporte = TransporteFalso()
        baixador = self._baixador(transporte)
        [primeiro] = baixador.executar([tarefa(tamanho=6)])
        [segundo] = baixador.executar([tarefa(tamanho=6)])

        self.assertEqual((primeiro.status, segundo.status), (Status.BAIXADO, Status.EXISTENTE))
        self.assertEqual(transporte.chamadas, 1)
        self.assertFalse(any(self.destino.rglob("*.parte")))
        [linha] = list(csv.DictReader((self.destino / "MANIFEST.csv").open(encoding="utf-8")))
        self.assertEqual((linha["nome_arquivo"], linha["tamanho_bytes"], linha["n_linhas"]), ("DS-01_x/a.csv", "6", "1"))

    def test_tamanho_divergente_descarta_parte(self):
        with self.assertLogs("coletor.baixador", "ERROR"):
            [resultado] = self._baixador(TransporteFalso(b"curto")).executar([tarefa(tamanho=999)])
        self.assertEqual(resultado.status, Status.FALHA)
        self.assertIn("tamanho divergente", resultado.mensagem)
        self.assertFalse(any(self.destino.rglob("a.csv*")))

    def test_falha_isolada_e_sem_dados(self):
        transporte = TransporteFalso(erro=SemDados("vazio"))
        with self.assertLogs("coletor.baixador", "WARNING"):
            [resultado] = self._baixador(transporte).executar([tarefa()])
        self.assertEqual(resultado.status, Status.SEM_DADOS)
        self.assertFalse((self.destino / "MANIFEST.csv").exists())

    def test_bytes_pendentes_desconta_parte(self):
        baixador = self._baixador(TransporteFalso())
        parte = self.destino / "DS-01_x" / "a.csv.parte"
        parte.parent.mkdir(parents=True)
        parte.write_bytes(b"1234")
        self.assertEqual(baixador.bytes_pendentes([tarefa(tamanho=10), tarefa("b.csv")]), 6)

    def test_intercala_hosts(self):
        tarefas = [tarefa("1", host="ftp"), tarefa("2", host="ftp"), tarefa("3", host="s3")]
        self.assertEqual([t.destino.name for t in intercalar_por_host(tarefas)], ["1", "3", "2"])


if __name__ == "__main__":
    unittest.main()
