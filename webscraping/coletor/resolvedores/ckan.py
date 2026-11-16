"""Páginas de conjunto de dados CKAN (``/dataset/<slug>``).

O portal do Ministério da Saúde (``dadosabertos.saude.gov.br``) é um front-end
Next.js sobre o CKAN: os links não estão em ``<a>``, e sim no JSON embutido em
``<script id="__NEXT_DATA__">``. Portais CKAN clássicos (``catalog.data.gov``)
usam ``<a class="resource-url-analytics">``. Se nenhum dos dois layouts
funcionar, a API ``package_show`` do CKAN serve de plano C.
"""

import json
import logging
import re
from collections.abc import Iterator
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from coletor.erros import ErroColeta
from coletor.modelos import Secao, Tarefa
from coletor.resolvedores.base import Resolvedor
from coletor.util import extensao_de_url, nome_de_url

logger = logging.getLogger(__name__)

_HOSTS = frozenset({"dadosabertos.saude.gov.br", "opendatasus.saude.gov.br", "catalog.data.gov"})
_RE_CAMINHO = re.compile(r"^/dataset/(?P<slug>[\w-]+)/?$")

Recurso = tuple[str, str]  # (formato declarado, URL)


def recursos_next_data(sopa: BeautifulSoup) -> list[Recurso]:
    """Recursos do pacote CKAN embutido no JSON ``__NEXT_DATA__`` do Next.js."""
    script = sopa.find("script", id="__NEXT_DATA__")
    if script is None or not script.string:
        return []
    pilha: list[object] = [json.loads(script.string)]
    # Busca em profundidade pelo primeiro dicionário com a chave "resources",
    # para não depender do caminho exato (props → pageProps → …) do front-end.
    while pilha:
        no = pilha.pop()
        if isinstance(no, dict):
            if isinstance(recursos := no.get("resources"), list):
                return [(r.get("format") or "", r.get("url") or "") for r in recursos if isinstance(r, dict)]
            pilha.extend(no.values())
        elif isinstance(no, list):
            pilha.extend(no)
    return []


def recursos_html_classico(sopa: BeautifulSoup, base: str) -> list[Recurso]:
    """Recursos de um tema CKAN padrão (``li.resource-item``)."""
    recursos = []
    for ancora in sopa.select("a.resource-url-analytics[href]"):
        item = ancora.find_parent("li", class_="resource-item")
        rotulo = item.select_one("[data-format]") if item else None
        recursos.append((rotulo["data-format"] if rotulo else "", urljoin(base, ancora["href"])))
    return recursos


class ResolvedorCkan(Resolvedor):
    nome = "CKAN"

    def aceita(self, url: str) -> bool:
        partes = urlsplit(url)
        return partes.hostname in _HOSTS and bool(_RE_CAMINHO.match(partes.path))

    def resolver(self, url: str, secao: Secao) -> Iterator[Tarefa]:
        sopa, resposta = self._ctx.http.sopa(url)
        # Conjuntos removidos redirecionam para a página inicial do portal.
        if not _RE_CAMINHO.match(urlsplit(resposta.url).path):
            raise ErroColeta(f"conjunto não encontrado: a página redirecionou para {resposta.url}")

        recursos = (
            recursos_next_data(sopa)
            or recursos_html_classico(sopa, resposta.url)
            or self._recursos_api(url)
        )
        if not recursos:
            raise ErroColeta(f"nenhum recurso encontrado em {url}")

        for formato, link in recursos:
            if not link.startswith(("http://", "https://", "ftp://")) or not nome_de_url(link):
                continue  # recursos do tipo "API" apontam para documentação, não para arquivo
            if self._ctx.config.aceita_formato(formato or extensao_de_url(link)):
                yield self._tarefa(link, secao, nome_de_url(link), parametros=f"formato={formato}")
            else:
                logger.debug("CKAN: %s (%s) ignorado pelo filtro de formatos", link, formato)

    def _recursos_api(self, url: str) -> list[Recurso]:
        partes = urlsplit(url)
        slug = _RE_CAMINHO.match(partes.path)["slug"]
        try:
            dados = self._ctx.http.json(f"{partes.scheme}://{partes.netloc}/api/3/action/package_show?id={slug}")
        except (requests.RequestException, ValueError) as erro:
            logger.debug("CKAN: API package_show indisponível para %s: %s", slug, erro)
            return []
        return [(r.get("format") or "", r.get("url") or "") for r in dados.get("result", {}).get("resources", [])]
