"""Execução concorrente das tarefas de download.

Garantias:

* **Gravação atômica**: o download vai para ``<arquivo>.parte`` e só é
  renomeado (``os.replace``) depois de validado. Um arquivo com o nome final
  está sempre completo.
* **Retomada**: uma ``.parte`` de execução anterior continua de onde parou
  (HTTP ``Range`` / FTP ``REST``). Arquivos já presentes são pulados.
* **Cortesia**: semáforo por host limita as conexões simultâneas a cada servidor.
* **Isolamento de falhas**: um arquivo com erro não interrompe os demais.
"""

import logging
import os
import threading
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import chain, zip_longest
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from coletor.erros import ErroColeta, SemDados
from coletor.manifesto import Manifesto, resumir_arquivo
from coletor.modelos import Resultado, Status, Tarefa
from coletor.util import formatar_bytes

logger = logging.getLogger(__name__)


class Transporte(Protocol):
    """O que o baixador exige de um cliente de protocolo (HTTP, FTP)."""

    def baixar(self, tarefa: Tarefa, parte: Path, parar: threading.Event) -> None: ...


def intercalar_por_host(tarefas: Iterable[Tarefa]) -> list[Tarefa]:
    """Reordena em rodízio entre hosts.

    Sem isso, milhares de ``.dbc`` do mesmo FTP ocupariam todas as threads,
    que ficariam paradas no semáforo do host enquanto os outros servidores
    esperam na fila.
    """
    por_host: dict[str, list[Tarefa]] = defaultdict(list)
    for tarefa in tarefas:
        por_host[tarefa.host].append(tarefa)
    rodadas = zip_longest(*por_host.values())
    return [tarefa for tarefa in chain.from_iterable(rodadas) if tarefa is not None]


class Baixador:
    def __init__(
        self,
        *,
        destino: Path,
        transportes: Mapping[str, Transporte],
        manifesto: Manifesto,
        workers: int,
        conexoes_por_host: Mapping[str, int],
        conexoes_padrao: int,
        forcar: bool = False,
    ) -> None:
        self._destino = destino
        self._transportes = transportes
        self._manifesto = manifesto
        self._workers = workers
        self._forcar = forcar
        self._parar = threading.Event()
        self._semaforos = {host: threading.BoundedSemaphore(n) for host, n in conexoes_por_host.items()}
        self._conexoes_padrao = conexoes_padrao
        self._trava_semaforos = threading.Lock()

    # ----------------------------------------------------------- consultas
    def presente(self, tarefa: Tarefa) -> bool:
        """``True`` se o arquivo final já existe completo (e não há ``--forcar``)."""
        if self._forcar:
            return False
        caminho = self._destino / tarefa.destino
        if not caminho.is_file():
            return False
        tamanho = caminho.stat().st_size
        return tamanho == tarefa.tamanho_esperado if tarefa.tamanho_esperado is not None else tamanho > 0

    def bytes_pendentes(self, tarefas: Iterable[Tarefa]) -> int:
        """Volume conhecido ainda a baixar (descontando ``.parte`` existentes)."""
        total = 0
        for tarefa in tarefas:
            if tarefa.tamanho_esperado is None or self.presente(tarefa):
                continue
            parte = self._caminho_parte(self._destino / tarefa.destino)
            total += tarefa.tamanho_esperado - (parte.stat().st_size if parte.is_file() else 0)
        return total

    # ------------------------------------------------------------ execução
    def executar(self, tarefas: Sequence[Tarefa]) -> list[Resultado]:
        ordenadas = intercalar_por_host(tarefas)
        resultados: list[Resultado] = []
        executor = ThreadPoolExecutor(max_workers=self._workers, thread_name_prefix="baixador")
        try:
            futuros = [executor.submit(self._processar, tarefa) for tarefa in ordenadas]
            for indice, futuro in enumerate(as_completed(futuros), start=1):
                resultado = futuro.result()
                resultados.append(resultado)
                self._registrar_progresso(indice, len(futuros), resultado)
        except KeyboardInterrupt:
            # Sinaliza às threads para abandonarem as transferências; as
            # ".parte" ficam no disco e serão retomadas na próxima execução.
            logger.warning("Interrompido: finalizando transferências em andamento…")
            self._parar.set()
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        executor.shutdown(wait=True)
        return resultados

    def _processar(self, tarefa: Tarefa) -> Resultado:
        """Baixa, valida, registra. Nunca levanta exceção (exceto bugs graves)."""
        final = self._destino / tarefa.destino
        if self.presente(tarefa):
            return Resultado(tarefa, Status.EXISTENTE, final, final.stat().st_size)
        parte = self._caminho_parte(final)
        try:
            final.parent.mkdir(parents=True, exist_ok=True)
            if self._forcar:
                parte.unlink(missing_ok=True)
            transporte = self._transportes[urlsplit(tarefa.url).scheme]
            with self._semaforo(tarefa.host):
                transporte.baixar(tarefa, parte, self._parar)
            self._validar_tamanho(parte, tarefa)
            os.replace(parte, final)  # atômico no mesmo sistema de arquivos
            sha256, linhas = resumir_arquivo(final)
            tamanho = final.stat().st_size
            self._manifesto.registrar(tarefa, tarefa.destino, sha256, tamanho, linhas)
            return Resultado(tarefa, Status.BAIXADO, final, tamanho)
        except InterruptedError:
            return Resultado(tarefa, Status.INTERROMPIDO, final, mensagem="interrompido")
        except SemDados as vazio:
            parte.unlink(missing_ok=True)
            return Resultado(tarefa, Status.SEM_DADOS, final, mensagem=str(vazio))
        except Exception as erro:  # noqa: BLE001 — um arquivo com falha não pode derrubar o lote
            logger.debug("Falha em %s", tarefa.url, exc_info=True)
            return Resultado(tarefa, Status.FALHA, final, mensagem=f"{type(erro).__name__}: {erro}")

    # ------------------------------------------------------------- apoio
    @staticmethod
    def _caminho_parte(final: Path) -> Path:
        return final.with_name(final.name + ".parte")

    @staticmethod
    def _validar_tamanho(parte: Path, tarefa: Tarefa) -> None:
        if tarefa.tamanho_esperado is None:
            if parte.stat().st_size == 0:
                raise ErroColeta("o servidor devolveu um arquivo vazio")
            return
        obtido = parte.stat().st_size
        if obtido != tarefa.tamanho_esperado:
            # O servidor encerrou a transferência, mas o tamanho não bate:
            # arquivo truncado ou trocado no meio da retomada. Descarta a parte
            # para a próxima execução recomeçar do zero.
            parte.unlink()
            raise ErroColeta(f"tamanho divergente: esperado {tarefa.tamanho_esperado} B, obtido {obtido} B")

    def _semaforo(self, host: str) -> threading.BoundedSemaphore:
        with self._trava_semaforos:
            if host not in self._semaforos:
                self._semaforos[host] = threading.BoundedSemaphore(self._conexoes_padrao)
            return self._semaforos[host]

    @staticmethod
    def _registrar_progresso(indice: int, total: int, resultado: Resultado) -> None:
        prefixo = f"[{indice}/{total}]"
        destino = resultado.tarefa.destino.as_posix()
        match resultado.status:
            case Status.BAIXADO:
                logger.info("%s OK      %s (%s)", prefixo, destino, formatar_bytes(resultado.tamanho))
            case Status.EXISTENTE:
                logger.debug("%s já existe %s", prefixo, destino)
            case Status.SEM_DADOS:
                logger.warning("%s vazio   %s — %s", prefixo, destino, resultado.mensagem)
            case Status.FALHA:
                logger.error("%s FALHA   %s — %s", prefixo, destino, resultado.mensagem)
            case Status.INTERROMPIDO:
                logger.warning("%s parado  %s", prefixo, destino)
