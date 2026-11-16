"""Registro de proveniência (``MANIFEST.csv``), conforme a seção 5 do DATASETS.md.

Cada arquivo baixado ganha uma linha com URL, parâmetros, data/hora do acesso,
competência, SHA-256 e tamanho. Com isso a análise pode ser auditada mesmo que
o DATASUS reprocesse competências antigas.
"""

import csv
import hashlib
import threading
from datetime import datetime, timezone
from pathlib import Path

from coletor.modelos import Tarefa

_EXTENSOES_TEXTO = frozenset({".csv", ".json", ".txt"})
_TAMANHO_BLOCO = 1 << 20


def resumir_arquivo(caminho: Path) -> tuple[str, int | None]:
    """SHA-256 e número de linhas (só para arquivos de texto), em uma leitura."""
    digest = hashlib.sha256()
    contar = caminho.suffix.lower() in _EXTENSOES_TEXTO
    quebras, ultimo_byte = 0, b"\n"
    with caminho.open("rb") as arquivo:
        while bloco := arquivo.read(_TAMANHO_BLOCO):
            digest.update(bloco)
            if contar:
                quebras += bloco.count(b"\n")
                ultimo_byte = bloco[-1:]
    # Arquivo sem "\n" no final ainda tem uma última linha a contar.
    linhas = quebras + (ultimo_byte != b"\n") if contar else None
    return digest.hexdigest(), linhas


class Manifesto:
    """Escrita concorrente (thread-safe) e incremental do MANIFEST.csv."""

    CAMPOS = (
        "dataset_id", "url_origem", "parametros", "data_hora_acesso", "versao_ou_competencia",
        "nome_arquivo", "sha256", "tamanho_bytes", "n_linhas",
    )

    def __init__(self, caminho: Path) -> None:
        self._caminho = caminho
        self._trava = threading.Lock()

    def registrar(self, tarefa: Tarefa, relativo: Path, sha256: str, tamanho: int, n_linhas: int | None) -> None:
        linha = {
            "dataset_id": tarefa.dataset,
            "url_origem": tarefa.url,
            "parametros": tarefa.parametros,
            "data_hora_acesso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "versao_ou_competencia": tarefa.competencia,
            "nome_arquivo": relativo.as_posix(),
            "sha256": sha256,
            "tamanho_bytes": tamanho,
            "n_linhas": "" if n_linhas is None else n_linhas,
        }
        with self._trava:
            novo = not self._caminho.exists() or self._caminho.stat().st_size == 0
            self._caminho.parent.mkdir(parents=True, exist_ok=True)
            with self._caminho.open("a", newline="", encoding="utf-8") as arquivo:
                escritor = csv.DictWriter(arquivo, fieldnames=self.CAMPOS)
                if novo:
                    escritor.writeheader()
                escritor.writerow(linha)
