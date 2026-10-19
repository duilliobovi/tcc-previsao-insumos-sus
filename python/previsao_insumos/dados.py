"""Leitura e limpeza das séries mensais do TabNet (SIA/SUS) por UF.

As tabelas ``prn`` do TabNet trazem uma linha por ano (total) seguida de uma
linha por mês ("..Janeiro/2008" ou "Janeiro/2024"), uma coluna por UF e o
traço "-" para célula sem registro. As regras de limpeza são:

* "-" vira 0 e, se a série tem volume alto, o 0 é tratado como **falta de
  registro** (NA). Exemplos reais: RS em abril e maio de 2024 (enchentes) e AP
  em meses de 2024. O limiar usa só o passado (mediana dos 12 meses
  anteriores), para não haver vazamento;
* picos não são suavizados: eles fazem parte do fenômeno a prever;
* as últimas competências, ainda não consolidadas, são descartadas.
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd

from previsao_insumos.config import Configuracao

MESES = {m: i for i, m in enumerate(
    ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
     "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"], start=1)}
_RE_MES = re.compile(r"^(?:\.\.)?(?P<mes>[A-Za-zçÇ]+)/(?P<ano>\d{4})$")

UF_REGIAO = {
    "RO": "Norte", "AC": "Norte", "AM": "Norte", "RR": "Norte", "PA": "Norte", "AP": "Norte", "TO": "Norte",
    "MA": "Nordeste", "PI": "Nordeste", "CE": "Nordeste", "RN": "Nordeste", "PB": "Nordeste",
    "PE": "Nordeste", "AL": "Nordeste", "SE": "Nordeste", "BA": "Nordeste",
    "MG": "Sudeste", "ES": "Sudeste", "RJ": "Sudeste", "SP": "Sudeste",
    "PR": "Sul", "SC": "Sul", "RS": "Sul",
    "MS": "Centro-Oeste", "MT": "Centro-Oeste", "GO": "Centro-Oeste", "DF": "Centro-Oeste",
}


def ler_tabnet_mensal(caminho: Path) -> pd.DataFrame:
    """Tabela larga: índice = mês (Period), colunas = UFs, valores float (NaN = "-")."""
    tabela = pd.read_csv(caminho, sep=";", dtype=str, encoding="utf-8")
    rotulo = tabela.columns[0]
    datas = tabela[rotulo].str.strip().str.extract(_RE_MES)
    mensal = tabela[datas["mes"].isin(MESES)].copy()
    meses = datas.loc[mensal.index]
    mensal.index = pd.PeriodIndex(
        [pd.Period(year=int(a), month=MESES[m], freq="M") for m, a in zip(meses["mes"], meses["ano"])], name="data")
    ufs = [c for c in mensal.columns if c in UF_REGIAO]

    def numero(serie: pd.Series) -> pd.Series:
        texto = serie.str.strip().replace("-", np.nan).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
        return pd.to_numeric(texto, errors="coerce")

    return mensal[ufs].apply(numero).sort_index()


def marcar_ausencias(serie: pd.Series, limiar: float) -> pd.Series:
    """Zero/NA em série com mediana passada ≥ ``limiar`` vira NA; nos demais casos, zero verdadeiro.

    A mediana é a dos 12 meses anteriores (``shift(1)``), portanto não usa o mês avaliado.
    NA antes do primeiro registro positivo é mantido como NA (série ainda não existia).
    """
    valores = serie.fillna(0.0)
    mediana_passada = valores.shift(1).rolling(12, min_periods=3).median()
    ausente = (valores <= 0) & (mediana_passada >= limiar)
    limpa = valores.mask(ausente)
    primeiro = (valores > 0).idxmax() if (valores > 0).any() else None
    if primeiro is not None:
        limpa[limpa.index < primeiro] = np.nan
    return limpa


def carregar_painel(config: Configuracao) -> pd.DataFrame:
    """Painel longo ``[serie, grupo, uf, regiao, data, y, y_bruto, ausente]`` já limpo."""
    partes = []
    for grupo in config.grupos:
        arquivo = config.dir_tabnet_grupos / f"tabnet_qauf__grupo_{grupo}__qtd_aprovada.csv"
        larga = ler_tabnet_mensal(arquivo)
        larga = larga.iloc[: len(larga) - config.meses_descartados_no_fim]
        for uf in larga.columns:
            bruto = larga[uf]
            limpo = marcar_ausencias(bruto, config.limiar_zero_como_ausente)
            iniciada = (bruto.fillna(0) > 0).cummax()  # depois do primeiro registro positivo
            partes.append(pd.DataFrame({
                "serie": f"{grupo}|{uf}", "grupo": grupo, "uf": uf, "regiao": UF_REGIAO[uf],
                "data": larga.index, "y": limpo.to_numpy(), "y_bruto": bruto.to_numpy(),
                "ausente": (limpo.isna() & iniciada).to_numpy(),
            }))
    painel = pd.concat(partes, ignore_index=True)
    inicio, fim = pd.Period(config.covid_inicio, "M"), pd.Period(config.covid_fim, "M")
    painel["covid"] = ((painel["data"] >= inicio) & (painel["data"] <= fim)).astype(int)
    return painel


def matriz_series(painel: pd.DataFrame) -> pd.DataFrame:
    """Tabela larga ``data × serie`` com os valores limpos (NaN = ausente)."""
    return painel.pivot(index="data", columns="serie", values="y").sort_index()
