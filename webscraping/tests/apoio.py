"""Dublês de teste: substituem rede e FTP por respostas fixas."""

from bs4 import BeautifulSoup
import requests

from coletor.config import Configuracao
from coletor.resolvedores.base import Contexto


def resposta(conteudo: bytes, *, url: str = "http://exemplo/", tipo: str = "text/html; charset=utf-8",
             status: int = 200) -> requests.Response:
    """Monta um ``requests.Response`` sem tocar a rede."""
    r = requests.Response()
    r._content = conteudo
    r.status_code = status
    r.url = url
    r.headers["Content-Type"] = tipo
    return r


class HttpFalso:
    """Implementa só ``sopa``/``json``: cada URL devolve o HTML cadastrado."""

    def __init__(self, paginas: dict[str, str]) -> None:
        self.paginas = paginas
        self.pedidas: list[str] = []

    def sopa(self, url: str):
        self.pedidas.append(url)
        if url not in self.paginas:
            raise requests.HTTPError(f"404 {url}")
        return BeautifulSoup(self.paginas[url], "lxml"), resposta(self.paginas[url].encode(), url=url)

    def json(self, url: str):
        raise requests.HTTPError(f"404 {url}")


class FtpFalso:
    def __init__(self, listagens: dict[tuple[str, str], list]) -> None:
        self.listagens = listagens

    def listar(self, host: str, diretorio: str):
        return self.listagens[(host, diretorio)]


def contexto(config: Configuracao | None = None, *, http=None, ftp=None) -> Contexto:
    return Contexto(config or Configuracao(), http or HttpFalso({}), ftp or FtpFalso({}))
