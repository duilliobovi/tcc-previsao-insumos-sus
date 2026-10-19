"""Avaliação decisória (objetivo específico 5), em caráter exploratório.

Política de revisão periódica com nível-alvo (order-up-to) e prazo de um mês:
no início de cada mês o estoque é elevado ao quantil previsto ŷ_tau para o
mês seguinte; a demanda acima desse nível é perdida (falta) e a abaixo dele
sobra como estoque. Com h = 1:

    falta_t   = max(y_t − ŷ_tau,t, 0)          sobra_t = max(ŷ_tau,t − y_t, 0)
    fill rate = 1 − Σ falta / Σ y              estoque relativo = Σ sobra / Σ y

Isso traduz o erro de previsão nas duas grandezas que o gestor observa: o nível
de serviço e o capital imobilizado em estoque.
"""

import numpy as np
import pandas as pd


def simular_politica(df: pd.DataFrame, coluna_quantil: str) -> pd.DataFrame:
    """Fill rate e estoque relativo por (série, modelo), no teste, h = 1."""
    teste = df[(df["fase"] == "teste") & (df["h"] == 1) & df["observado"].notna()].copy()
    teste["falta"] = np.maximum(teste["observado"] - teste[coluna_quantil], 0)
    teste["sobra"] = np.maximum(teste[coluna_quantil] - teste["observado"], 0)
    agregado = teste.groupby(["serie", "modelo"])[["falta", "sobra", "observado"]].sum()
    return pd.DataFrame({
        "fill_rate": 1 - agregado["falta"] / agregado["observado"],
        "estoque_relativo": agregado["sobra"] / agregado["observado"],
    }).reset_index()
