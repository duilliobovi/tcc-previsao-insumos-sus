"""Raspagem do TabNet (DATASUS): formulário → séries mensais por UF em CSV.

O TabNet não publica arquivos: ele monta tabelas sob demanda a partir de um
formulário HTML (``deftohtm.exe``) enviado por POST para ``tabcgi.exe``. O
resolvedor lê o formulário com BeautifulSoup e reproduz o que o navegador
enviaria, com três escolhas fixas:

* **Linha** = ano/mês (processamento e/ou atendimento, ver ``--tabnet-linhas``);
* **Coluna** = Unidade da Federação;
* **Arquivos** = todas as competências do período configurado.

Gera uma consulta por medida (``Incremento``), porque o TabNet só aceita uma
medida quando há coluna ativa. A resposta em formato ``prn`` traz a tabela em
texto separado por ``;`` dentro de ``<pre>``, que é gravado como CSV UTF-8.
"""

import logging
import re
from collections.abc import Iterator
from urllib.parse import urlencode, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

from coletor.cliente_http import charset_declarado
from coletor.erros import ErroColeta, SemDados
from coletor.modelos import Competencia, Secao, Tarefa
from coletor.resolvedores.base import Resolvedor
from coletor.util import slug

logger = logging.getLogger(__name__)

_CAMPOS_VARIAVEIS = frozenset({"Linha", "Coluna", "Incremento", "Arquivos"})
_COLUNA_PREFERIDA = "Unidade_da_Federação"
_COLUNA_INATIVA = "--Não-Ativa--"
_RE_ARQUIVO = re.compile(r"(?P<aamm>\d{4})\.dbf$", re.I)  # qiuf2607.dbf → 2026-07


def extrair_tabela_prn(resposta: requests.Response) -> bytes:
    """Extrai a tabela ``prn`` (texto com ``;``) da página de resultado."""
    # O TabNet responde em ISO-8859-1; o charset do cabeçalho tem prioridade.
    # Use o nome canônico: o libxml2 (via lxml) não reconhece o apelido "latin-1".
    sopa = BeautifulSoup(resposta.content, "lxml", from_encoding=charset_declarado(resposta) or "iso-8859-1")
    pre = sopa.find("pre")
    if pre is None or ";" not in pre.get_text():
        # Erros do TabNet (consulta grande demais, sessão expirada) vêm como texto solto.
        texto = " ".join(sopa.get_text(" ", strip=True).split())
        if "Nenhum registro selecionado" in texto:
            raise SemDados("o TabNet não tem registros para esta medida no período")
        raise ErroColeta(f"o TabNet não devolveu tabela: {texto[:300]}")
    return (pre.get_text().strip() + "\n").encode("utf-8")


def _opcoes(formulario: Tag, nome: str) -> list[str]:
    """Valores das opções de um ``<select name=nome>``."""
    campo = formulario.find("select", attrs={"name": nome})
    if campo is None:
        return []
    return [opcao.get("value", opcao.get_text(strip=True)) for opcao in campo.find_all("option")]


def _campos_fixos(formulario: Tag) -> Iterator[tuple[str, str]]:
    """Filtros com o valor padrão (``Todas as categorias``) e campos ocultos.

    Reproduz a serialização do navegador: em ``<select>`` vão as opções
    marcadas; em ``<select>`` simples sem marcação, a primeira opção.
    """
    for campo in formulario.find_all("select"):
        nome = campo.get("name")
        if not nome or nome in _CAMPOS_VARIAVEIS:
            continue
        opcoes = campo.find_all("option")
        marcadas = [o for o in opcoes if o.has_attr("selected")]
        if not marcadas and opcoes and not campo.has_attr("multiple"):
            marcadas = opcoes[:1]
        for opcao in marcadas:
            yield nome, opcao.get("value", opcao.get_text(strip=True))
    for campo in formulario.find_all("input", attrs={"type": "hidden"}):
        if campo.get("name"):
            yield campo["name"], campo.get("value", "")


class ResolvedorTabnet(Resolvedor):
    nome = "TabNet"

    def aceita(self, url: str) -> bool:
        partes = urlsplit(url)
        return (
            partes.hostname == "tabnet.datasus.gov.br"
            and partes.path.lower().endswith("/deftohtm.exe")
            and bool(partes.query)
        )

    def resolver(self, url: str, secao: Secao) -> Iterator[Tarefa]:
        sopa, resposta = self._ctx.http.sopa(url)
        formulario = sopa.find("form", action=re.compile(r"tabcgi", re.I))
        if formulario is None:
            raise ErroColeta(f"formulário do TabNet não encontrado em {url}")

        acao = urljoin(resposta.url, formulario["action"])
        definicao = slug(urlsplit(url).query.rsplit("/", 1)[-1].removesuffix(".def"))  # "qiuf"
        linhas_disponiveis = _opcoes(formulario, "Linha")
        coluna = _COLUNA_PREFERIDA if _COLUNA_PREFERIDA in _opcoes(formulario, "Coluna") else _COLUNA_INATIVA
        arquivos = [a for a in _opcoes(formulario, "Arquivos") if self._no_periodo(a)]
        if not arquivos:
            raise ErroColeta(f"nenhuma competência do TabNet no período em {url}")
        competencias = sorted(Competencia.de_aamm(_RE_ARQUIVO.search(a)["aamm"]) for a in arquivos)
        fixos = list(_campos_fixos(formulario))

        for linha in self._ctx.config.linhas_tabnet:
            if linha not in linhas_disponiveis:
                logger.warning("TabNet %s não oferece a linha %r", definicao, linha)
                continue
            for incremento in _opcoes(formulario, "Incremento"):
                campos = [
                    ("Linha", linha), ("Coluna", coluna), ("Incremento", incremento),
                    *(("Arquivos", arquivo) for arquivo in arquivos),
                    *fixos,
                    ("formato", "prn"), ("mostre", "Mostra"),
                ]
                yield self._tarefa(
                    acao, secao, f"tabnet_{definicao}__{slug(linha)}__{slug(incremento)}.csv",
                    subpasta="tabnet",
                    # O CGI do TabNet espera o formulário codificado em ISO-8859-1.
                    corpo=urlencode(campos, encoding="latin-1").encode("ascii"),
                    extrator=extrair_tabela_prn,
                    parametros=f"def={definicao}; Linha={linha}; Coluna={coluna}; Incremento={incremento}",
                    competencia=f"{competencias[0]}..{competencias[-1]}",
                    estatico=False,
                )

    def _no_periodo(self, arquivo: str) -> bool:
        casamento = _RE_ARQUIVO.search(arquivo)
        return bool(casamento) and self._ctx.config.no_periodo(Competencia.de_aamm(casamento["aamm"]))
