"""Cliente HTTP "educado": robots.txt, intervalo por host, retentativas e retomada.

Cada thread usa a própria :class:`requests.Session` (a classe não garante
segurança entre threads), mas todas compartilham o cache de ``robots.txt`` e o
limitador de taxa.
"""

import logging
import re
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from coletor.erros import BloqueadoPorRobots, ErroColeta
from coletor.modelos import Tarefa

logger = logging.getLogger(__name__)

_TAMANHO_BLOCO = 1 << 20  # 1 MiB por escrita em disco
_EXTENSOES_HTML = frozenset({".htm", ".html"})


def charset_declarado(resposta: requests.Response) -> str | None:
    """Charset explícito no ``Content-Type``.

    Não usamos ``resposta.encoding`` porque o requests assume ISO-8859-1 para
    ``text/*`` sem charset; nesse caso é melhor deixar o BeautifulSoup detectar
    pelo ``<meta charset>`` do próprio documento.
    """
    casamento = re.search(r"charset=([\w.-]+)", resposta.headers.get("Content-Type", ""), re.I)
    return casamento[1] if casamento else None


def _politica_retentativas() -> Retry:
    """Retentativa com recuo exponencial para falhas transitórias do servidor."""
    return Retry(
        total=5,
        connect=2,  # DNS/conexão recusada raramente se resolvem sozinhos em segundos
        backoff_factor=1.5,  # 0 s, 3 s, 6 s, 12 s, 24 s
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),  # o POST do TabNet é idempotente
        respect_retry_after_header=True,
        raise_on_status=False,
    )


class LimitadorPorHost:
    """Impõe um intervalo mínimo entre requisições ao mesmo host (thread-safe).

    Cada chamada *reserva* o próximo horário livre do host antes de dormir, de
    modo que várias threads nunca disparam juntas contra o mesmo servidor.
    """

    def __init__(self, padrao: float, especificos: Mapping[str, float]) -> None:
        self._padrao = padrao
        self._intervalos = dict(especificos)
        self._proximo_horario: dict[str, float] = {}
        self._trava = threading.Lock()

    def ajustar(self, host: str, intervalo: float) -> None:
        """Aumenta o intervalo de um host (ex.: ``Crawl-delay`` do robots.txt)."""
        with self._trava:
            self._intervalos[host] = max(self._intervalos.get(host, self._padrao), intervalo)

    def aguardar(self, host: str) -> None:
        with self._trava:
            agora = time.monotonic()
            horario = max(agora, self._proximo_horario.get(host, 0.0))
            self._proximo_horario[host] = horario + self._intervalos.get(host, self._padrao)
        if (espera := horario - agora) > 0:
            time.sleep(espera)


class ClienteHttp:
    """Fachada sobre ``requests`` com as regras de cortesia do coletor."""

    def __init__(
        self,
        *,
        user_agent: str,
        limitador: LimitadorPorHost,
        respeitar_robots: bool = True,
        timeout: tuple[float, float] = (20.0, 300.0),
    ) -> None:
        self._user_agent = user_agent
        self._limitador = limitador
        self._respeitar_robots = respeitar_robots
        self._timeout = timeout  # (conexão, leitura); o TabNet demora a responder
        self._local = threading.local()
        self._robots: dict[str, RobotFileParser] = {}
        self._trava_robots = threading.Lock()

    # ------------------------------------------------------------------ sessão
    def _sessao(self) -> requests.Session:
        sessao: requests.Session | None = getattr(self._local, "sessao", None)
        if sessao is None:
            sessao = requests.Session()
            sessao.headers["User-Agent"] = self._user_agent
            adaptador = HTTPAdapter(max_retries=_politica_retentativas())
            sessao.mount("http://", adaptador)
            sessao.mount("https://", adaptador)
            self._local.sessao = sessao
        return sessao

    # ------------------------------------------------------------- robots.txt
    def _regras_robots(self, base: str) -> RobotFileParser:
        """Baixa e interpreta o robots.txt do site uma única vez (RFC 9309)."""
        with self._trava_robots:
            if (regras := self._robots.get(base)) is not None:
                return regras
            regras = RobotFileParser(f"{base}/robots.txt")
            resposta = self._sessao().get(f"{base}/robots.txt", timeout=self._timeout)
            if resposta.ok:
                regras.parse(resposta.text.splitlines())
            elif resposta.status_code < 500:
                regras.allow_all = True  # RFC 9309 §2.3.1.3: 4xx → sem restrições
            else:
                regras.disallow_all = True  # RFC 9309 §2.3.1.4: 5xx → tudo proibido
                logger.warning("robots.txt de %s indisponível (HTTP %s)", base, resposta.status_code)
            if atraso := regras.crawl_delay(self._user_agent):
                self._limitador.ajustar(urlsplit(base).hostname or "", float(atraso))
                logger.info("%s pede Crawl-delay de %s s", base, atraso)
            self._robots[base] = regras
            return regras

    def _verificar_robots(self, url: str) -> None:
        if not self._respeitar_robots:
            return
        partes = urlsplit(url)
        if not self._regras_robots(f"{partes.scheme}://{partes.netloc}").can_fetch(self._user_agent, url):
            raise BloqueadoPorRobots(f"robots.txt proíbe o acesso a {url}")

    # ------------------------------------------------------------- requisições
    def requisitar(self, metodo: str, url: str, **kwargs) -> requests.Response:
        """Requisição com robots.txt, limitação de taxa e retentativas."""
        self._verificar_robots(url)
        self._limitador.aguardar(urlsplit(url).hostname or "")
        kwargs.setdefault("timeout", self._timeout)
        return self._sessao().request(metodo, url, **kwargs)

    def sopa(self, url: str) -> tuple[BeautifulSoup, requests.Response]:
        """Baixa uma página e devolve a árvore BeautifulSoup e a resposta.

        O parser ``lxml`` é o mais rápido e tolera bem o HTML malformado dos
        portais públicos. Os bytes (e não ``.text``) vão para o BeautifulSoup
        para ele respeitar o ``<meta charset>`` quando o cabeçalho é omisso.
        """
        resposta = self.requisitar("GET", url)
        resposta.raise_for_status()
        return BeautifulSoup(resposta.content, "lxml", from_encoding=charset_declarado(resposta)), resposta

    def json(self, url: str) -> object:
        resposta = self.requisitar("GET", url, headers={"Accept": "application/json"})
        resposta.raise_for_status()
        return resposta.json()

    def tamanho_remoto(self, url: str) -> int | None:
        """Tamanho do arquivo sem baixá-lo.

        Usa GET com ``Range: bytes=0-0`` em vez de HEAD, porque alguns
        servidores (ex.: gov.br) respondem 403 a HEAD. O corpo não é lido.
        """
        with self.requisitar("GET", url, headers={"Range": "bytes=0-0"}, stream=True) as resposta:
            if resposta.status_code == 206:
                casamento = re.search(r"/(\d+)$", resposta.headers.get("Content-Range", ""))
                return int(casamento[1]) if casamento else None
            comprimido = "Content-Encoding" in resposta.headers  # Content-Length seria do gzip
            if resposta.ok and not comprimido and (comprimento := resposta.headers.get("Content-Length")):
                return int(comprimento)
        return None

    # --------------------------------------------------------------- download
    def baixar(self, tarefa: Tarefa, parte: Path, parar: threading.Event) -> None:
        """Grava o conteúdo de ``tarefa`` em ``parte``, retomando se já existir."""
        if tarefa.extrator is not None:
            self._baixar_extraindo(tarefa, parte)
            return

        # Respostas dinâmicas mudam a cada chamada: retomar misturaria versões.
        inicio = parte.stat().st_size if tarefa.estatico and parte.exists() else 0
        cabecalhos = {"Range": f"bytes={inicio}-"} if inicio else {}
        with self.requisitar("GET", tarefa.url, headers=cabecalhos, stream=True) as resposta:
            if resposta.status_code == 416:  # Range além do fim: a parte já está completa
                return
            resposta.raise_for_status()
            _rejeitar_html_inesperado(resposta, tarefa)
            # 206 = o servidor aceitou retomar; 200 = recomeça do zero.
            modo = "ab" if resposta.status_code == 206 else "wb"
            with parte.open(modo) as arquivo:
                for bloco in resposta.iter_content(_TAMANHO_BLOCO):
                    if parar.is_set():
                        raise InterruptedError("coleta interrompida pelo usuário")
                    arquivo.write(bloco)

    def _baixar_extraindo(self, tarefa: Tarefa, parte: Path) -> None:
        """Respostas pequenas que precisam de conversão (ex.: tabela do TabNet)."""
        if tarefa.corpo is None:
            resposta = self.requisitar("GET", tarefa.url)
        else:
            resposta = self.requisitar(
                "POST", tarefa.url, data=tarefa.corpo,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        resposta.raise_for_status()
        assert tarefa.extrator is not None
        parte.write_bytes(tarefa.extrator(resposta))


def _rejeitar_html_inesperado(resposta: requests.Response, tarefa: Tarefa) -> None:
    """Evita gravar uma página de erro HTML com nome de ``.zip``/``.csv``."""
    tipo = resposta.headers.get("Content-Type", "")
    if tipo.startswith("text/html") and tarefa.destino.suffix.lower() not in _EXTENSOES_HTML:
        raise ErroColeta(f"o servidor devolveu HTML em vez do arquivo ({resposta.url})")
