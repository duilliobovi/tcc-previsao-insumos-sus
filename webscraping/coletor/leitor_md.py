"""Leitura do ``datasets_finais.md``: extrai as seções numeradas e suas URLs.

Só as seções ``## N. Título`` descrevem datasets; as demais (comparação,
ferramentas de apoio, script de exemplo) são ignoradas, assim como blocos de
código cercados por ```` ``` ````, cujas URLs contêm variáveis de shell.
"""

import re
from pathlib import Path

from coletor.modelos import Secao

_RE_TITULO = re.compile(r"^##\s+(?P<numero>\d+)\.\s+(?P<titulo>.+?)\s*$")
_RE_URL = re.compile(r"(?:https?|ftp)://[^\s`<>\"')\]|]+")
_PONTUACAO_FINAL = ".,;:"


def extrair_urls(linha: str) -> list[str]:
    """URLs de uma linha, sem pontuação final de frase (``…/dados.``)."""
    return [url.rstrip(_PONTUACAO_FINAL) for url in _RE_URL.findall(linha)]


def ler_secoes(caminho: Path) -> list[Secao]:
    """Lê o Markdown e devolve as seções de dataset na ordem do arquivo."""
    secoes: list[Secao] = []
    cabecalho: re.Match[str] | None = None
    urls: list[str] = []
    em_bloco_de_codigo = False

    def fechar_secao() -> None:
        if cabecalho is not None and urls:
            # dict.fromkeys remove duplicatas preservando a ordem.
            secoes.append(Secao(
                numero=int(cabecalho["numero"]),
                titulo=cabecalho["titulo"],
                urls=tuple(dict.fromkeys(urls)),
            ))

    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if linha.lstrip().startswith("```"):
            em_bloco_de_codigo = not em_bloco_de_codigo
        elif em_bloco_de_codigo:
            continue
        elif linha.startswith("#"):
            fechar_secao()
            cabecalho, urls = _RE_TITULO.match(linha), []
        elif cabecalho is not None:
            urls.extend(extrair_urls(linha))
    fechar_secao()
    return secoes
