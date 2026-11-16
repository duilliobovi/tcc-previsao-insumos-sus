"""Registros do Zenodo: arquivos listados na página do registro."""

import logging
from collections.abc import Iterator
from urllib.parse import unquote, urljoin, urlsplit

import requests

from coletor.erros import ErroColeta
from coletor.modelos import Secao, Tarefa
from coletor.resolvedores.base import Resolvedor

logger = logging.getLogger(__name__)


class ResolvedorZenodo(Resolvedor):
    nome = "Zenodo"

    def aceita(self, url: str) -> bool:
        return self._identificador(url) is not None

    def resolver(self, url: str, secao: Secao) -> Iterator[Tarefa]:
        identificador = self._identificador(url)
        arquivos = self._da_pagina(identificador) or self._da_api(identificador)
        if not arquivos:
            raise ErroColeta(f"registro Zenodo {identificador} sem arquivos")
        for nome, link in arquivos.items():
            yield self._tarefa(link, secao, nome)

    def _da_pagina(self, identificador: str) -> dict[str, str]:
        """Raspa os links ``/records/<id>/files/<nome>?download=1`` da página."""
        try:
            sopa, resposta = self._ctx.http.sopa(f"https://zenodo.org/records/{identificador}")
        except requests.RequestException as erro:
            logger.info("Zenodo: página do registro indisponível (%s); usando a API", erro)
            return {}
        marcador = f"/records/{identificador}/files/"
        arquivos: dict[str, str] = {}
        for ancora in sopa.select(f'a[href*="{marcador}"]'):
            partes = urlsplit(urljoin(resposta.url, ancora["href"]))
            nome = unquote(partes.path.split(marcador, 1)[1]).removesuffix("/content")
            if nome:
                arquivos.setdefault(nome, f"{partes.scheme}://{partes.netloc}{partes.path}?download=1")
        return arquivos

    def _da_api(self, identificador: str) -> dict[str, str]:
        """Plano B caso o layout da página mude: API REST pública do Zenodo."""
        dados = self._ctx.http.json(f"https://zenodo.org/api/records/{identificador}")
        return {arquivo["key"]: arquivo["links"]["self"] for arquivo in dados.get("files", [])}

    @staticmethod
    def _identificador(url: str) -> str | None:
        partes = urlsplit(url)
        segmentos = partes.path.strip("/").split("/")
        if partes.hostname in {"zenodo.org", "www.zenodo.org"} and len(segmentos) == 2 \
                and segmentos[0] in {"record", "records"} and segmentos[1].isdigit():
            return segmentos[1]
        return None
