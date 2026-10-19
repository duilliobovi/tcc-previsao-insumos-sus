"""Configuração imutável do experimento.

Todos os parâmetros que afetam os resultados ficam aqui, em um único objeto
congelado, para que o experimento seja descrito por inteiro no texto do TCC
e reproduzido sem ambiguidade.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

RAIZ_REPOSITORIO = Path(__file__).resolve().parents[2]


def _diretorio_dados() -> Path:
    """Os dados ficam fora do Git: ``$TCC_DADOS`` ou ``<repositório>/DATASETS`` (ver dados/FONTES_DE_DADOS.md)."""
    return Path(os.environ.get("TCC_DADOS", RAIZ_REPOSITORIO / "DATASETS"))


@dataclass(frozen=True, slots=True)
class Configuracao:
    dir_dados: Path = field(default_factory=_diretorio_dados)
    dir_saida: Path = RAIZ_REPOSITORIO / "resultados" / "tabelas"

    # Séries-alvo: grupos da Tabela SUS no SIA/TabNet (quantidade aprovada).
    grupos: tuple[str, ...] = ("06_medicamentos", "07_opm")

    # Competências não consolidadas descartadas no fim da série.
    meses_descartados_no_fim: int = 3
    # Período tratado como intervenção (dummy) e não removido.
    covid_inicio: str = "2020-03"
    covid_fim: str = "2021-12"
    # Zero em série de volume alto é falta de registro, não demanda nula.
    limiar_zero_como_ausente: float = 50.0

    # Protocolo de validação: origem rolante expansiva em dois níveis.
    horizontes: tuple[int, ...] = (1, 3, 6)
    origens_validacao: int = 6
    origens_teste: int = 18
    periodo_sazonal: int = 12

    # Inferência.
    alfa: float = 0.05
    reamostragens_bootstrap: int = 10_000
    bloco_bootstrap_origens: int = 3
    tau_quantil: float = 0.9
    semente: int = 2026

    @property
    def horizonte_maximo(self) -> int:
        return max(self.horizontes)

    @property
    def dir_tabnet_grupos(self) -> Path:
        return self.dir_dados / "DS-03_sia_sus_agregado_tabnet" / "tabnet_grupos"
