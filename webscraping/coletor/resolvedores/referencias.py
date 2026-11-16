"""URLs que não são dados baixáveis: portais, documentação e buscadores.

Ficam no início da cadeia para que estratégias genéricas (ex.: páginas do
gov.br) não tentem raspar uma página institucional. O motivo de cada exclusão
fica registrado no log, para a decisão ficar rastreável.
"""

import re
from typing import NoReturn

from coletor.erros import UrlIgnorada
from coletor.modelos import Secao
from coletor.resolvedores.base import Resolvedor

_REFERENCIAS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(padrao), motivo)
    for padrao, motivo in (
        (r"^https?://datasus\.saude\.gov\.br/",
         "portal institucional do DATASUS; os arquivos são baixados direto do FTP"),
        (r"^https?://sigtap\.datasus\.gov\.br/",
         "aplicação web de consulta; as tabelas por competência vêm do FTP ftp2.datasus.gov.br"),
        (r"^https?://dadosabertos\.compras\.gov\.br/swagger",
         "documentação Swagger; a API exige códigos CATMAT e não oferece download em lote"),
        (r"^https?://catalogo\.compras\.gov\.br/",
         "buscador interativo do catálogo CATMAT, sem arquivo para baixar"),
        (r"^https?://info\.dengue\.mat\.br/services/api/doc",
         "documentação da API InfoDengue"),
        (r"^https?://servicodados\.ibge\.gov\.br/api/docs",
         "documentação da API do IBGE; os dados vêm da API SIDRA"),
        (r"^https?://sidra\.ibge\.gov\.br/tabela/",
         "interface web do SIDRA; os mesmos dados vêm da API SIDRA"),
        (r"^https?://www\.ibge\.gov\.br/",
         "página institucional do IBGE"),
        (r"^https?://forecastingdata\.org/?$",
         "índice do Monash Archive; o subconjunto recomendado (car_parts) vem do Zenodo"),
        (r"^https?://www\.gov\.br/saude/pt-br/acesso-a-informacao/banco-de-precos/?$",
         "página institucional e painel interativo do BPS"),
        (r"^https?://opendatasus\.saude\.gov\.br/dataset/?$",
         "listagem geral do portal, sem conjunto específico"),
        (r"^https?://github\.com/",
         "repositório de código, não é dataset"),
    )
)


class ResolvedorReferencia(Resolvedor):
    """Ignora, com motivo registrado, URLs que sabidamente não têm dados."""

    nome = "referência"

    def aceita(self, url: str) -> bool:
        return any(padrao.search(url) for padrao, _ in _REFERENCIAS)

    def resolver(self, url: str, secao: Secao) -> NoReturn:
        motivo = next(motivo for padrao, motivo in _REFERENCIAS if padrao.search(url))
        raise UrlIgnorada(motivo)


class ResolvedorSemEstrategia(Resolvedor):
    """Último elo da cadeia: aceita tudo e avisa que a URL não foi tratada."""

    nome = "sem estratégia"

    def aceita(self, url: str) -> bool:
        return True

    def resolver(self, url: str, secao: Secao) -> NoReturn:
        raise UrlIgnorada("nenhuma estratégia de download reconhece esta URL", aviso=True)
