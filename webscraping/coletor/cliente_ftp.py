"""Cliente FTP para os servidores do DATASUS: listagem, download e retomada.

Os servidores misturam dois formatos de ``LIST``: o do IIS (``ftp.datasus``) e
o Unix (``ftp2.datasus``); :func:`interpretar_listagem` entende os dois. As
conexões são reaproveitadas por thread, porque abrir uma sessão FTP por
arquivo (são milhares de ``.dbc``) é lento e incomoda o servidor.
"""

import ftplib
import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from coletor.erros import ErroColeta
from coletor.modelos import Tarefa

logger = logging.getLogger(__name__)

# 03-10-20  02:43PM               196476 CHBR1901.dbc
_RE_LIST_IIS = re.compile(
    r"^\d{2}-\d{2}-\d{2,4}\s+\d{1,2}:\d{2}(?:AM|PM)?\s+(?P<tipo><DIR>|\d+)\s+(?P<nome>.+)$", re.I
)
# -rwxrwxr-x    1 ftp      ftp        806021 Jan 08  2009 TabelaUnificada_200801.zip
_RE_LIST_UNIX = re.compile(
    r"^(?P<tipo>[-dl])\S{9}\s+\d+\s+\S+\s+\S+\s+(?P<tamanho>\d+)\s+\w{3}\s+\d{1,2}\s+[\d:]{4,5}\s+(?P<nome>.+)$"
)
_TAMANHO_BLOCO = 1 << 16


@dataclass(frozen=True, slots=True)
class EntradaFtp:
    nome: str
    tamanho: int | None
    eh_diretorio: bool


def interpretar_listagem(linhas: list[str]) -> list[EntradaFtp]:
    """Converte a saída do comando ``LIST`` (formato IIS ou Unix) em entradas."""
    entradas = []
    for linha in linhas:
        if casamento := _RE_LIST_IIS.match(linha):
            eh_dir = casamento["tipo"].upper() == "<DIR>"
            tamanho = None if eh_dir else int(casamento["tipo"])
        elif casamento := _RE_LIST_UNIX.match(linha):
            eh_dir = casamento["tipo"] == "d"
            tamanho = None if eh_dir else int(casamento["tamanho"])
        else:
            logger.debug("linha de LIST não reconhecida: %r", linha)
            continue
        entradas.append(EntradaFtp(casamento["nome"].strip(), tamanho, eh_dir))
    return entradas


def dividir_url_ftp(url: str) -> tuple[str, str]:
    """``ftp://host/a/b.dbc`` → ``("host", "/a/b.dbc")``."""
    partes = urlsplit(url)
    if partes.scheme != "ftp" or not partes.hostname:
        raise ValueError(f"URL FTP inválida: {url}")
    return partes.hostname, unquote(partes.path) or "/"


class ClienteFtp:
    """Listagens com cache e downloads retomáveis (comando ``REST``)."""

    def __init__(self, *, timeout: float = 90.0, tentativas: int = 4) -> None:
        self._timeout = timeout
        self._tentativas = tentativas
        self._local = threading.local()
        self._listagens: dict[tuple[str, str], list[EntradaFtp]] = {}
        self._trava_listagens = threading.Lock()

    # ---------------------------------------------------------------- conexões
    def _conexoes(self) -> dict[str, ftplib.FTP]:
        """Conexões abertas pela thread atual, indexadas por host."""
        if not hasattr(self._local, "conexoes"):
            self._local.conexoes = {}
        return self._local.conexoes

    def _conexao(self, host: str) -> ftplib.FTP:
        conexoes = self._conexoes()
        if (ftp := conexoes.get(host)) is not None:
            try:
                ftp.voidcmd("NOOP")  # confirma que o servidor não derrubou a sessão
                return ftp
            except ftplib.all_errors:
                self._descartar(host)
        # latin-1 decodifica qualquer byte, então nomes estranhos não quebram a listagem.
        ftp = ftplib.FTP(host, timeout=self._timeout, encoding="latin-1")
        ftp.login()  # acesso anônimo
        ftp.voidcmd("TYPE I")  # modo binário: necessário para SIZE e para não corromper .dbc
        conexoes[host] = ftp
        return ftp

    def _descartar(self, host: str) -> None:
        if (ftp := self._conexoes().pop(host, None)) is not None:
            try:
                ftp.close()
            except ftplib.all_errors:
                pass

    def _com_retentativas(self, host: str, descricao: str, operacao):
        """Executa ``operacao(ftp)`` reconectando com recuo exponencial.

        Erros permanentes (5xx, ex.: 550 arquivo inexistente) não são repetidos.
        """
        for tentativa in range(1, self._tentativas + 1):
            try:
                return operacao(self._conexao(host))
            except ftplib.error_perm as erro:
                raise ErroColeta(f"FTP recusou {descricao}: {erro}") from erro
            except InterruptedError:
                self._descartar(host)  # transferência pela metade deixa a sessão inutilizável
                raise
            except ftplib.all_errors as erro:
                self._descartar(host)
                if tentativa == self._tentativas:
                    raise ErroColeta(f"FTP falhou em {descricao} após {tentativa} tentativas: {erro}") from erro
                espera = 2 ** tentativa
                logger.warning("FTP %s: %s — nova tentativa em %d s", descricao, erro, espera)
                time.sleep(espera)
        raise AssertionError("inalcançável")

    # ---------------------------------------------------------------- operações
    def listar(self, host: str, diretorio: str) -> list[EntradaFtp]:
        """Conteúdo de um diretório; o resultado fica em cache durante a execução."""
        chave = (host, diretorio)
        with self._trava_listagens:
            if chave in self._listagens:
                return self._listagens[chave]

        def operacao(ftp: ftplib.FTP) -> list[EntradaFtp]:
            linhas: list[str] = []
            ftp.retrlines(f"LIST {diretorio}", linhas.append)
            return interpretar_listagem(linhas)

        entradas = self._com_retentativas(host, f"LIST {diretorio}", operacao)
        with self._trava_listagens:
            self._listagens[chave] = entradas
        return entradas

    def tamanho_remoto(self, url: str) -> int | None:
        """Tamanho via comando ``SIZE`` (exige modo binário, já ativado)."""
        host, caminho = dividir_url_ftp(url)
        return self._com_retentativas(host, f"SIZE {caminho}", lambda ftp: ftp.size(caminho))

    def baixar(self, tarefa: Tarefa, parte: Path, parar: threading.Event) -> None:
        """Baixa o arquivo para ``parte``, continuando do ponto em que parou."""
        host, caminho = dividir_url_ftp(tarefa.url)

        def operacao(ftp: ftplib.FTP) -> None:
            inicio = parte.stat().st_size if parte.exists() else 0
            with parte.open("ab" if inicio else "wb") as arquivo:
                def gravar(bloco: bytes) -> None:
                    if parar.is_set():
                        raise InterruptedError("coleta interrompida pelo usuário")
                    arquivo.write(bloco)

                ftp.retrbinary(f"RETR {caminho}", gravar, blocksize=_TAMANHO_BLOCO, rest=inicio or None)

        self._com_retentativas(host, f"RETR {caminho}", operacao)
