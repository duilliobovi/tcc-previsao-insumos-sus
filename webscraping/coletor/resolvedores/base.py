"""Contrato das estratégias de resolução (padrão *Strategy*).

Um resolvedor reconhece um tipo de URL (``aceita``) e a converte nas tarefas
de download dos arquivos que ela representa (``resolver``): um diretório FTP
vira milhares de ``.dbc``, uma página CKAN vira seus recursos, e assim por
diante. O orquestrador percorre os resolvedores em ordem e usa o primeiro que
aceitar a URL (*Chain of Responsibility*).
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from coletor.cliente_ftp import ClienteFtp
from coletor.cliente_http import ClienteHttp
from coletor.config import Configuracao
from coletor.modelos import Secao, Tarefa
from coletor.util import nome_seguro


@dataclass(frozen=True, slots=True)
class Contexto:
    """Dependências injetadas em todos os resolvedores."""

    config: Configuracao
    http: ClienteHttp
    ftp: ClienteFtp


class Resolvedor(ABC):
    """Estratégia que transforma uma URL do Markdown em tarefas de download."""

    nome: ClassVar[str]

    def __init__(self, contexto: Contexto) -> None:
        self._ctx = contexto

    @abstractmethod
    def aceita(self, url: str) -> bool:
        """``True`` se esta estratégia sabe tratar a URL."""

    @abstractmethod
    def resolver(self, url: str, secao: Secao) -> Iterator[Tarefa]:
        """Tarefas de download da URL; pode acessar a rede para descobri-las."""

    def _tarefa(self, url: str, secao: Secao, nome: str, *, subpasta: str = "", **campos: Any) -> Tarefa:
        """Monta a tarefa com destino ``<pasta da seção>/<subpasta>/<nome>``."""
        return Tarefa(
            url=url,
            destino=Path(secao.pasta, subpasta, nome_seguro(nome)),
            dataset=secao.codigo or secao.pasta,
            **campos,
        )
