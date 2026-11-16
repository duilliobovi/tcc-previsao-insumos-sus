"""APIs públicas com URL de download previsível (sem raspagem de HTML).

* **SIDRA/IBGE**: a própria URL do Markdown já é a consulta (JSON).
* **InfoDengue**: a URL citada é a raiz da API; geramos uma consulta por
  município × doença no período configurado.
* **Socrata (data.usaid.gov)**: página ``/d/<id>`` → exportação CSV.
"""

import re
from collections.abc import Iterator
from urllib.parse import urlsplit

import requests

from coletor.config import DOENCAS_INFODENGUE
from coletor.modelos import Secao, Tarefa
from coletor.resolvedores.base import Resolvedor
from coletor.util import slug


class ResolvedorSidra(Resolvedor):
    nome = "API SIDRA"

    def aceita(self, url: str) -> bool:
        partes = urlsplit(url)
        return partes.hostname == "apisidra.ibge.gov.br" and partes.path.startswith("/values/")

    def resolver(self, url: str, secao: Secao) -> Iterator[Tarefa]:
        # /values/t/6579/n3/all/v/9324/p/all → sidra_t_6579_n3_all_v_9324_p_all.json
        consulta = slug(urlsplit(url).path.removeprefix("/values/"))
        yield self._tarefa(url, secao, f"sidra_{consulta}.json", parametros=urlsplit(url).path, estatico=False)


class ResolvedorInfoDengue(Resolvedor):
    nome = "API InfoDengue"
    _ENDPOINT = "https://info.dengue.mat.br/api/alertcity"

    def aceita(self, url: str) -> bool:
        partes = urlsplit(url)
        return partes.hostname == "info.dengue.mat.br" and partes.path.rstrip("/") == "/services/api"

    def resolver(self, url: str, secao: Secao) -> Iterator[Tarefa]:
        config = self._ctx.config
        ano_inicial, ano_final = config.inicio.ano, config.ano_final
        for geocodigo in config.geocodigos_efetivos:
            for doenca in DOENCAS_INFODENGUE:
                parametros = {
                    "geocode": geocodigo, "disease": doenca, "format": "csv",
                    "ew_start": 1, "ew_end": 53, "ey_start": ano_inicial, "ey_end": ano_final,
                }
                # A URL completa (com a query) fica no manifesto e serve de chave de deduplicação.
                consulta = requests.Request("GET", self._ENDPOINT, params=parametros).prepare().url
                assert consulta is not None
                yield self._tarefa(
                    consulta, secao, f"infodengue_{geocodigo}_{doenca}_{ano_inicial}-{ano_final}.csv",
                    parametros=f"geocode={geocodigo}; disease={doenca}",
                    competencia=f"{ano_inicial}..{ano_final}",
                    estatico=False,
                )


class ResolvedorSocrata(Resolvedor):
    nome = "Socrata"
    _RE_ID = re.compile(r"^/d/(?P<id>[a-z0-9]{4}-[a-z0-9]{4})/?$")

    def aceita(self, url: str) -> bool:
        partes = urlsplit(url)
        return partes.hostname == "data.usaid.gov" and bool(self._RE_ID.match(partes.path))

    def resolver(self, url: str, secao: Secao) -> Iterator[Tarefa]:
        partes = urlsplit(url)
        identificador = self._RE_ID.match(partes.path)["id"]
        exportacao = f"https://{partes.hostname}/api/views/{identificador}/rows.csv?accessType=DOWNLOAD"
        yield self._tarefa(exportacao, secao, f"usaid_{identificador}.csv", estatico=False)
