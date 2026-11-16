"""Pastas de arquivos do portal gov.br (CMS Plone).

Ex.: *Bases Anuais Compiladas* do BPS. A pasta é paginada
(``?b_start:int=15``) e lista cada arquivo como ``…/<item>/view``; a página do
item, por sua vez, traz o link real ``…/<item>/@@download/file`` com o nome
original do arquivo (``BPS_2022.xlsx``).
"""

import logging
from collections import deque
from collections.abc import Iterator
from urllib.parse import urljoin, urlsplit

import requests

from coletor.erros import ErroColeta
from coletor.modelos import Secao, Tarefa
from coletor.resolvedores.base import Resolvedor
from coletor.util import extensao_de_url, nome_de_url

logger = logging.getLogger(__name__)

_SELETOR_CONTEUDO = "#content-core"  # miolo da página Plone, sem menus e rodapé


class ResolvedorPlone(Resolvedor):
    nome = "gov.br (Plone)"

    def aceita(self, url: str) -> bool:
        return urlsplit(url).hostname == "www.gov.br" and not extensao_de_url(url)

    def resolver(self, url: str, secao: Secao) -> Iterator[Tarefa]:
        itens = self._itens_da_pasta(url.rstrip("/"))
        if not itens:
            raise ErroColeta(f"nenhum arquivo listado em {url}")
        # Sem filtro de formato: cada item é um arquivo único (anos antigos só
        # existem em .xls), e não uma versão alternativa do mesmo dado.
        for pagina_item in itens:
            try:
                link, nome = self._link_de_download(pagina_item)
            except (requests.RequestException, ErroColeta) as erro:
                logger.warning("gov.br: item %s ignorado: %s", pagina_item, erro)
                continue
            yield self._tarefa(link, secao, nome)

    def _itens_da_pasta(self, pasta: str) -> list[str]:
        """Percorre todas as páginas da listagem e coleta os links ``/view``."""
        caminho_pasta = urlsplit(pasta).path
        pendentes, visitadas = deque([pasta]), set()
        itens: dict[str, None] = {}  # dict como conjunto ordenado
        while pendentes:
            pagina = pendentes.popleft()
            if pagina in visitadas:
                continue
            visitadas.add(pagina)
            sopa, resposta = self._ctx.http.sopa(pagina)
            conteudo = sopa.select_one(_SELETOR_CONTEUDO) or sopa
            for ancora in conteudo.find_all("a", href=True):
                link = urljoin(resposta.url, ancora["href"])
                partes = urlsplit(link)
                if partes.path.rstrip("/") == caminho_pasta and "b_start" in partes.query:
                    pendentes.append(link)  # outra página da paginação
                elif partes.path.startswith(caminho_pasta + "/") and partes.path.endswith("/view"):
                    itens.setdefault(f"{partes.scheme}://{partes.netloc}{partes.path}", None)
        return list(itens)

    def _link_de_download(self, pagina_item: str) -> tuple[str, str]:
        """Link ``@@download`` e nome original do arquivo, lidos na página do item."""
        sopa, resposta = self._ctx.http.sopa(pagina_item)
        conteudo = sopa.select_one(_SELETOR_CONTEUDO) or sopa
        ancora = conteudo.select_one('a[href*="@@download"]')
        if ancora is None:
            raise ErroColeta("página do item sem link de download")
        link = urljoin(resposta.url, ancora["href"])
        # O texto do link é o nome do arquivo; o fim da URL é só "file".
        nome = ancora.get_text(strip=True) or nome_de_url(link)
        return link, nome
