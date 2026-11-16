"""URLs que já apontam para um arquivo (``.zip``, ``.csv``, ``.pdf``…)."""

from collections.abc import Iterator
from urllib.parse import urlsplit

from coletor.config import EXTENSOES_DADOS
from coletor.modelos import Secao, Tarefa
from coletor.resolvedores.base import Resolvedor
from coletor.util import extensao_de_url, nome_de_url


class ResolvedorArquivoDireto(Resolvedor):
    nome = "arquivo direto"

    def aceita(self, url: str) -> bool:
        return urlsplit(url).scheme in {"http", "https", "ftp"} and extensao_de_url(url) in EXTENSOES_DADOS

    def resolver(self, url: str, secao: Secao) -> Iterator[Tarefa]:
        yield self._tarefa(url, secao, nome_de_url(url))
