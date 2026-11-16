"""Configuração imutável do experimento.

Todos os parâmetros que afetam os resultados ficam aqui, em um único objeto
congelado, para que o experimento seja descrito por inteiro no texto do TCC
e reproduzido sem ambiguidade. O plano registrado (docs/plano_analise.md)
remete a estes valores.
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
    # Período tratado como intervenção (indicador) e não removido.
    covid_inicio: str = "2020-03"
    covid_fim: str = "2021-12"
    # Zero em série de volume alto é falta de registro, não demanda nula.
    limiar_zero_como_ausente: float = 50.0

    # Protocolo: origem rolante expansiva; previsões para h = 1..6 em toda origem.
    horizontes: tuple[int, ...] = (1, 3, 6)          # horizontes avaliados nas hipóteses
    horizonte_maximo: int = 6                        # prazo máximo de compra considerado
    origens_validacao: int = 12
    # Intervalo entre a última origem de validação e a primeira de teste: nenhum
    # mês-alvo da validação (até o + 6) coincide com um mês-alvo do teste.
    intervalo_validacao_teste: int = 6
    origens_teste: int = 18
    periodo_sazonal: int = 12
    # Calibração conformal em janela móvel: resíduos das últimas N origens já observadas.
    janela_conformal: int = 12

    # Avaliação decisória: prazos de compra (meses) e níveis de serviço-alvo.
    prazos_compra: tuple[int, ...] = (3, 6)
    taus_estoque: tuple[float, ...] = (0.9, 0.95)

    # Inferência.
    alfa: float = 0.05
    reamostragens_bootstrap: int = 10_000
    bloco_bootstrap_origens: int = 3
    semente: int = 2026
    sementes_variabilidade: tuple[int, ...] = tuple(range(2026, 2036))

    @property
    def horizontes_previstos(self) -> tuple[int, ...]:
        return tuple(range(1, self.horizonte_maximo + 1))

    @property
    def dir_tabnet_grupos(self) -> Path:
        return self.dir_dados / "DS-03_sia_sus_agregado_tabnet" / "tabnet_grupos"
