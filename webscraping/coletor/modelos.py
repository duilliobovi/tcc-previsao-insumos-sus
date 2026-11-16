"""Objetos de valor do domínio: competência, seção do Markdown, tarefa e resultado.

Todos são *dataclasses* imutáveis (``frozen=True``): podem ser compartilhados
entre threads sem trava e usados como chave de dicionário/conjunto.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

import requests

from coletor.util import slug

_RE_CODIGO_DS = re.compile(r"DS-\d{2}")
_RE_PARENTESE_DS = re.compile(r"\s*\([^)]*DS-\d{2}[^)]*\)")

# Transforma a resposta HTTP no conteúdo a gravar (ex.: HTML do TabNet → CSV).
Extrator = Callable[[requests.Response], bytes]


@dataclass(frozen=True, order=True, slots=True)
class Competencia:
    """Mês de referência de um dado (``ano``/``mes``), comparável e ordenável."""

    ano: int
    mes: int

    def __post_init__(self) -> None:
        if not 1 <= self.mes <= 12:
            raise ValueError(f"mês inválido: {self.mes}")

    @classmethod
    def de_texto(cls, texto: str) -> "Competencia":
        """Aceita ``AAAA-MM`` ou ``AAAAMM``."""
        casamento = re.fullmatch(r"(\d{4})-?(\d{2})", texto.strip())
        if casamento is None:
            raise ValueError(f"competência inválida: {texto!r} (use AAAA-MM)")
        return cls(int(casamento[1]), int(casamento[2]))

    @classmethod
    def de_aamm(cls, aamm: str) -> "Competencia":
        """Converte o sufixo ``AAMM`` dos arquivos do DATASUS (séries desde 2005)."""
        return cls(2000 + int(aamm[:2]), int(aamm[2:4]))

    def __str__(self) -> str:
        return f"{self.ano:04d}-{self.mes:02d}"


@dataclass(frozen=True, slots=True)
class Secao:
    """Seção numerada do ``datasets_finais.md`` (um dataset) e suas URLs."""

    numero: int
    titulo: str
    urls: tuple[str, ...]

    @property
    def codigo(self) -> str | None:
        """Primeiro código ``DS-xx`` citado no título, se houver."""
        casamento = _RE_CODIGO_DS.search(self.titulo)
        return casamento.group(0) if casamento else None

    @property
    def pasta(self) -> str:
        """Nome do subdiretório em ``DATASETS/`` (ex.: ``DS-05_bps_banco_de_precos_em_saude``)."""
        nome = slug(_RE_PARENTESE_DS.sub("", self.titulo))
        return f"{self.codigo}_{nome}" if self.codigo else f"{self.numero:02d}_{nome}"


@dataclass(frozen=True, slots=True)
class Tarefa:
    """Um arquivo a ser baixado: de onde (``url``) para onde (``destino``).

    ``destino`` é relativo ao diretório de saída. ``corpo`` não nulo indica
    requisição POST (formulários do TabNet). ``extrator`` é usado quando a
    resposta precisa ser convertida antes de gravar. ``estatico=False`` marca
    respostas geradas sob demanda (APIs, TabNet): não têm tamanho conhecido
    de antemão nem podem ser retomadas pela metade.
    """

    url: str
    destino: Path
    dataset: str
    tamanho_esperado: int | None = None
    corpo: bytes | None = None
    extrator: Extrator | None = field(default=None, compare=False, repr=False)
    parametros: str = ""
    competencia: str = ""
    estatico: bool = True

    @property
    def chave(self) -> tuple[str, bytes | None]:
        """Identidade da requisição, usada para eliminar duplicatas."""
        return self.url, self.corpo

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or ""


class Status(StrEnum):
    BAIXADO = "baixado"
    EXISTENTE = "existente"
    SEM_DADOS = "sem dados"
    FALHA = "falha"
    INTERROMPIDO = "interrompido"


@dataclass(frozen=True, slots=True)
class Resultado:
    """Desfecho do processamento de uma :class:`Tarefa`."""

    tarefa: Tarefa
    status: Status
    caminho: Path
    tamanho: int | None = None
    mensagem: str = ""
