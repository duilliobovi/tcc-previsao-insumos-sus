"""Fase 1 da coleta: transformar as URLs do Markdown em um plano de downloads.

Separar *planejar* de *baixar* permite o modo ``--simular`` (mostra arquivos e
volume sem baixar nada) e a verificação de espaço em disco antes de começar.
"""

import ftplib
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import requests

from coletor.erros import ErroColeta, UrlIgnorada
from coletor.modelos import Secao, Tarefa
from coletor.resolvedores import Resolvedor

logger = logging.getLogger(__name__)

# Falhas esperadas na resolução: rede, servidor, layout de página alterado.
_ERROS_DE_RESOLUCAO = (ErroColeta, requests.RequestException, ValueError, KeyError, *ftplib.all_errors)


@dataclass
class Plano:
    tarefas: list[Tarefa] = field(default_factory=list)
    ignoradas: list[tuple[str, str]] = field(default_factory=list)
    falhas: list[tuple[str, str]] = field(default_factory=list)


def planejar(
    secoes: Iterable[Secao],
    resolvedores: Sequence[Resolvedor],
    filtro: re.Pattern[str] | None = None,
) -> Plano:
    """Resolve cada URL com o primeiro resolvedor que a aceitar.

    Remove duplicatas: a mesma URL pode aparecer em mais de uma seção, e um
    diretório FTP expandido inclui os arquivos de exemplo citados à parte.
    """
    plano = Plano()
    chaves_vistas: set[tuple[str, bytes | None]] = set()
    destinos_vistos: dict[Path, str] = {}

    for secao in secoes:
        if filtro and not filtro.search(f"{secao.pasta} {secao.titulo}"):
            continue
        logger.info("── %s (%d URLs)", secao.titulo, len(secao.urls))
        for url in secao.urls:
            resolvedor = next(r for r in resolvedores if r.aceita(url))
            try:
                novas = list(resolvedor.resolver(url, secao))
            except UrlIgnorada as ignorada:
                plano.ignoradas.append((url, ignorada.motivo))
                nivel = logging.WARNING if ignorada.aviso else logging.INFO
                logger.log(nivel, "   ignorada  %s — %s", url, ignorada.motivo)
                continue
            except _ERROS_DE_RESOLUCAO as erro:
                plano.falhas.append((url, f"{type(erro).__name__}: {erro}"))
                logger.error("   FALHA     %s — %s", url, erro)
                continue

            adicionadas = repetidas = 0
            for tarefa in novas:
                if tarefa.chave in chaves_vistas:
                    repetidas += 1
                    continue
                if (anterior := destinos_vistos.get(tarefa.destino)) is not None:
                    logger.warning("   conflito de destino %s (%s × %s); mantida a primeira",
                                   tarefa.destino, anterior, tarefa.url)
                    continue
                chaves_vistas.add(tarefa.chave)
                destinos_vistos[tarefa.destino] = tarefa.url
                plano.tarefas.append(tarefa)
                adicionadas += 1
            sufixo = f" (+{repetidas} já no plano)" if repetidas else ""
            logger.info("   %-15s %s → %d arquivo(s)%s", resolvedor.nome, url, adicionadas, sufixo)
    return plano


class Sondavel(Protocol):
    """Cliente capaz de informar o tamanho de um arquivo remoto."""

    def tamanho_remoto(self, url: str) -> int | None: ...


def sondar_tamanhos(tarefas: Sequence[Tarefa], clientes: Mapping[str, Sondavel], workers: int) -> list[Tarefa]:
    """Preenche o tamanho dos arquivos estáticos que ainda não o têm.

    As coleções FTP já trazem o tamanho na listagem; os demais arquivos são
    consultados no servidor. Com o tamanho, o plano mostra o volume real, o
    espaço em disco é verificado antes de começar e downloads truncados são
    detectados.
    """
    def sondar(tarefa: Tarefa) -> Tarefa:
        cliente = clientes.get(urlsplit(tarefa.url).scheme)
        if cliente is None or not tarefa.estatico or tarefa.tamanho_esperado is not None:
            return tarefa
        try:
            return replace(tarefa, tamanho_esperado=cliente.tamanho_remoto(tarefa.url))
        except _ERROS_DE_RESOLUCAO as erro:
            logger.debug("sem tamanho para %s: %s", tarefa.url, erro)
            return tarefa

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sonda") as executor:
        return list(executor.map(sondar, tarefas))  # map preserva a ordem do plano
