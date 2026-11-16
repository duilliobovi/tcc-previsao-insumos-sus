"""Configuração imutável da coleta e constantes das fontes de dados."""

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from coletor.modelos import Competencia

# webscraping/coletor/config.py → parents[2] é a raiz do TCC.
RAIZ_PROJETO = Path(__file__).resolve().parents[2]
ARQUIVO_MD_PADRAO = RAIZ_PROJETO / "datasets_finais.md"
DESTINO_PADRAO = RAIZ_PROJETO / "DATASETS"

# User-Agent honesto: identifica o coletor. Alguns servidores (ex.: Zenodo)
# bloqueiam User-Agents que fingem ser navegador.
USER_AGENT = "coletor-datasets-tcc/1.0 (TCC UNIVESP Ciencia de Dados; python-requests)"

UFS = (
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
)

# Código IBGE da capital de cada UF. O InfoDengue publica por município; sem
# geocódigos explícitos, a coleta usa as capitais das UFs selecionadas.
CAPITAIS_IBGE = {
    "AC": 1200401, "AL": 2704302, "AM": 1302603, "AP": 1600303, "BA": 2927408,
    "CE": 2304400, "DF": 5300108, "ES": 3205309, "GO": 5208707, "MA": 2111300,
    "MG": 3106200, "MS": 5002704, "MT": 5103403, "PA": 1501402, "PB": 2507507,
    "PE": 2611606, "PI": 2211001, "PR": 4106902, "RJ": 3304557, "RN": 2408102,
    "RO": 1100205, "RR": 1400100, "RS": 4314902, "SC": 4205407, "SE": 2800308,
    "SP": 3550308, "TO": 1721000,
}

# Tipos de arquivo .dbc do DATASUS usados no TCC (ver datasets_finais.md).
PREFIXOS_DATASUS = ("RD", "SP", "PA", "AM", "LT")

# Formatos aceitos quando a fonte oferece o mesmo dado em várias versões
# (CKAN: CSV/JSON/XML/Parquet). URLs explícitas do Markdown não são filtradas.
FORMATOS_PADRAO = ("csv", "zip", "pdf", "xlsx")

LINHAS_TABNET_PADRAO = ("Ano/mês_processamento", "Ano/mês_atendimento")
DOENCAS_INFODENGUE = ("dengue", "chikungunya", "zika")

# Extensões que identificam uma URL como arquivo (e não como página).
EXTENSOES_DADOS = frozenset({
    "csv", "dbc", "dbf", "doc", "gz", "json", "parquet", "pdf", "txt", "xls", "xlsx", "xml", "zip",
})

# Política de cortesia por servidor: conexões simultâneas e intervalo mínimo
# (segundos) entre requisições. O FTP do DATASUS derruba clientes agressivos.
CONEXOES_POR_HOST = {
    "ftp.datasus.gov.br": 3,
    "ftp2.datasus.gov.br": 2,
    "tabnet.datasus.gov.br": 1,
    "zenodo.org": 1,
}
CONEXOES_POR_HOST_PADRAO = 4
INTERVALO_POR_HOST = {"tabnet.datasus.gov.br": 2.0}
INTERVALO_PADRAO = 0.5


@dataclass(frozen=True, slots=True)
class Configuracao:
    """Parâmetros de uma execução, montados pela CLI."""

    arquivo_md: Path = ARQUIVO_MD_PADRAO
    destino: Path = DESTINO_PADRAO
    ufs: frozenset[str] = frozenset({"SP"})
    inicio: Competencia = Competencia(2008, 1)
    fim: Competencia | None = None
    prefixos: frozenset[str] = frozenset(PREFIXOS_DATASUS)
    formatos: frozenset[str] = frozenset(FORMATOS_PADRAO)
    linhas_tabnet: tuple[str, ...] = LINHAS_TABNET_PADRAO
    geocodigos: tuple[int, ...] = ()
    secoes: re.Pattern[str] | None = None
    workers: int = 6
    simular: bool = False
    forcar: bool = False
    respeitar_robots: bool = True

    def no_periodo(self, competencia: Competencia) -> bool:
        """``True`` se a competência está entre ``inicio`` e ``fim`` (inclusive)."""
        return competencia >= self.inicio and (self.fim is None or competencia <= self.fim)

    def aceita_formato(self, formato: str) -> bool:
        return formato.strip().lower() in self.formatos

    @property
    def ano_final(self) -> int:
        return self.fim.ano if self.fim else date.today().year

    @property
    def geocodigos_efetivos(self) -> tuple[int, ...]:
        """Geocódigos do InfoDengue: os informados ou as capitais das UFs."""
        return self.geocodigos or tuple(CAPITAIS_IBGE[uf] for uf in sorted(self.ufs))
