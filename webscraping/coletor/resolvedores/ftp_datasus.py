"""Coleções de arquivos nos FTPs do DATASUS (SIH, SIA, CNES e SIGTAP).

O Markdown cita os *diretórios* (``…/SIHSUS/200801_/Dados/``). Cada coleção
sabe interpretar os nomes dos seus arquivos e filtrá-los por tipo, UF e
competência, porque baixar o diretório inteiro (todas as UFs, todos os tipos)
ocuparia centenas de GB.
"""

import posixpath
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from coletor.cliente_ftp import EntradaFtp, dividir_url_ftp
from coletor.config import Configuracao
from coletor.erros import ErroColeta, UrlIgnorada
from coletor.modelos import Competencia, Secao, Tarefa
from coletor.resolvedores.base import Resolvedor


@dataclass(frozen=True, slots=True)
class ArquivoSelecionado:
    nome: str
    tamanho: int | None
    subpasta: str
    competencia: Competencia


class Colecao(ABC):
    """Diretório FTP com convenção de nomes própria."""

    def __init__(self, host: str, diretorio: str, *, apelidos: Iterable[str] = ()) -> None:
        self.host = host
        self.diretorio = diretorio
        # Outros caminhos citados no Markdown que levam à mesma coleção.
        self._apelidos = frozenset(apelidos)

    def corresponde_diretorio(self, host: str, caminho: str) -> bool:
        caminho = caminho.rstrip("/")
        return host == self.host and (caminho == self.diretorio or caminho in self._apelidos)

    def contem_arquivo(self, host: str, caminho: str) -> bool:
        return host == self.host and posixpath.dirname(caminho) == self.diretorio

    @abstractmethod
    def selecionar(self, entradas: Iterable[EntradaFtp], config: Configuracao) -> Iterator[ArquivoSelecionado]:
        """Arquivos do diretório que atendem aos filtros da configuração."""


class ColecaoDbc(Colecao):
    """Arquivos ``{TIPO}{UF}{AAMM}[fatia].dbc`` (ex.: ``PASP2601a.dbc``)."""

    _RE_NOME = re.compile(r"^(?P<tipo>[A-Z]{2})(?P<uf>[A-Z]{2})(?P<aamm>\d{4})[a-z]?\.dbc$", re.I)

    def __init__(self, host: str, diretorio: str, tipos: Iterable[str], *, apelidos: Iterable[str] = ()) -> None:
        super().__init__(host, diretorio, apelidos=apelidos)
        self._tipos = frozenset(tipos)

    def selecionar(self, entradas: Iterable[EntradaFtp], config: Configuracao) -> Iterator[ArquivoSelecionado]:
        tipos_desejados = self._tipos & config.prefixos
        for entrada in entradas:
            casamento = None if entrada.eh_diretorio else self._RE_NOME.match(entrada.nome)
            if casamento is None:
                continue
            tipo, uf = casamento["tipo"].upper(), casamento["uf"].upper()
            competencia = Competencia.de_aamm(casamento["aamm"])
            if tipo in tipos_desejados and uf in config.ufs and config.no_periodo(competencia):
                yield ArquivoSelecionado(entrada.nome, entrada.tamanho, tipo, competencia)


class ColecaoSigtap(Colecao):
    """``TabelaUnificada_AAAAMM[_vVERSAO].zip``: mantém a última versão de cada mês."""

    _RE_NOME = re.compile(r"^TabelaUnificada_(?P<aaaamm>\d{6})(?:_v(?P<versao>\d+))?\.zip$", re.I)

    def selecionar(self, entradas: Iterable[EntradaFtp], config: Configuracao) -> Iterator[ArquivoSelecionado]:
        ultimas: dict[Competencia, tuple[int, EntradaFtp]] = {}
        for entrada in entradas:
            if not (casamento := self._RE_NOME.match(entrada.nome)):
                continue
            competencia = Competencia.de_texto(casamento["aaaamm"])
            versao = int(casamento["versao"] or 0)
            if config.no_periodo(competencia) and versao >= ultimas.get(competencia, (-1, entrada))[0]:
                ultimas[competencia] = (versao, entrada)
        for competencia, (_, entrada) in sorted(ultimas.items()):
            yield ArquivoSelecionado(entrada.nome, entrada.tamanho, "", competencia)


COLECOES: tuple[Colecao, ...] = (
    ColecaoDbc("ftp.datasus.gov.br", "/dissemin/publicos/SIHSUS/200801_/Dados", ("RD", "SP")),
    ColecaoDbc("ftp.datasus.gov.br", "/dissemin/publicos/SIASUS/200801_/Dados", ("PA", "AM")),
    ColecaoDbc("ftp.datasus.gov.br", "/dissemin/publicos/CNES/200508_/Dados/LT", ("LT",),
               apelidos=("/dissemin/publicos/CNES",)),
    ColecaoSigtap("ftp2.datasus.gov.br", "/pub/sistemas/tup/downloads"),
)


class ResolvedorFtpDatasus(Resolvedor):
    """Expande diretórios das coleções e localiza arquivos avulsos nelas."""

    nome = "FTP DATASUS"

    def aceita(self, url: str) -> bool:
        return self._colecao(url) is not None

    def resolver(self, url: str, secao: Secao) -> Iterator[Tarefa]:
        colecao = self._colecao(url)
        assert colecao is not None  # garantido por aceita()
        host, caminho = dividir_url_ftp(url)
        entradas = self._ctx.ftp.listar(host, colecao.diretorio)

        if colecao.corresponde_diretorio(host, caminho):
            for arquivo in colecao.selecionar(entradas, self._ctx.config):
                yield self._tarefa(
                    f"ftp://{host}{colecao.diretorio}/{arquivo.nome}", secao, arquivo.nome,
                    subpasta=arquivo.subpasta, tamanho_esperado=arquivo.tamanho,
                    competencia=str(arquivo.competencia),
                )
            return

        # Arquivo citado como exemplo: obedece aos mesmos filtros do diretório,
        # para --ufs/--tipos/--inicio/--fim delimitarem de fato o volume baixado.
        nome = posixpath.basename(caminho)
        entrada = next((e for e in entradas if e.nome == nome), None)
        if entrada is None:
            raise ErroColeta(f"{nome} não existe em ftp://{host}{colecao.diretorio}")
        arquivo = next(colecao.selecionar([entrada], self._ctx.config), None)
        if arquivo is None:
            raise UrlIgnorada("fora dos filtros de UF, tipo ou período")
        yield self._tarefa(
            url, secao, nome, subpasta=arquivo.subpasta, tamanho_esperado=arquivo.tamanho,
            competencia=str(arquivo.competencia),
        )

    @staticmethod
    def _colecao(url: str) -> Colecao | None:
        if not url.startswith("ftp://"):
            return None
        host, caminho = dividir_url_ftp(url)
        return next(
            (c for c in COLECOES if c.corresponde_diretorio(host, caminho) or c.contem_arquivo(host, caminho)),
            None,
        )
