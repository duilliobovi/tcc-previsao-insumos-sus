"""Simulação de estoque com prazo de compra (achados M5 e P3; objetivo da simulação de estoques).

Política de revisão mensal com nível-alvo (order-up-to) e prazo de entrega de L meses
(L = 3 e 6, faixa do processo de compra pública). Na revisão do fim do mês o:

    S_o = D̂_o + q_tau · escala · L,  com D̂_o = Σ_{h=1..L} ŷ(o + h | o)
    pedido_o = max(0, S_o − posição),  posição = estoque em mãos + pedidos em trânsito
    o pedido chega no início do mês o + L

S_o cobre a demanda dos L meses até a chegada do pedido seguinte. q_tau é o quantil conformal do
erro acumulado em L meses (escalado), calculado na mesma janela móvel das previsões: só origens o'
com o' + L ≤ o entram. A demanda não atendida é perdida (venda perdida). A simulação começa L meses
antes do teste, com o estoque igual a S, e os indicadores são medidos nos 18 meses seguintes à
primeira origem de teste. Todos os modelos partem da mesma regra e diferem só nas previsões.

Custos (por unidade): manutenção c_m = (r/12)·v por mês em estoque; falta c_f = c_m·tau/(1 − tau),
a penalidade que torna tau o nível ótimo pela razão crítica. A razão de custo entre dois modelos
não depende de r nem de v; os valores em reais dependem de v (valor aprovado por unidade no SIA)
e de r (taxa anual de custo de capital, premissa com cenários).
"""

import numpy as np
import pandas as pd


def quantis_acumulados(df: pd.DataFrame, L: int, taus: tuple[float, ...], janela: int, minimo: int = 6) -> pd.DataFrame:
    """Previsão acumulada D̂ em L meses e quantis conformais do erro acumulado, por (série, modelo, origem)."""
    sub = df[df["h"] <= L]
    acum = sub.groupby(["modelo", "serie", "origem"]).agg(
        D_prev=("previsto", "sum"), D_obs=("observado", lambda x: x.sum(min_count=len(x))),
        n=("h", "size"), escala=("escala_cal", "first")).reset_index()
    acum = acum[acum["n"] == L]
    acum["r"] = (acum["D_obs"] - acum["D_prev"]) / (acum["escala"] * L)
    linhas = []
    for modelo, g in acum.groupby("modelo"):
        por_origem = {o: x["r"].dropna().to_numpy() for o, x in g.groupby("origem")}
        for o in sorted(por_origem):
            usadas = [oo for oo in por_origem if o - L - janela < oo <= o - L]
            if len(usadas) >= minimo:
                r = np.concatenate([por_origem[oo] for oo in usadas])
                linhas.append({"modelo": modelo, "origem": o, **{f"qa{t:g}": np.quantile(r, t) for t in taus}})
    return acum.merge(pd.DataFrame(linhas), on=["modelo", "origem"], how="inner")


def simular_serie(demanda: np.ndarray, observado: np.ndarray, alvo: dict[int, float], inicio: int, avaliar: range,
                  L: int) -> dict[str, float]:
    """Simula uma série. ``demanda`` é a demanda consumida (ausências interpoladas); ``alvo[o] = S_o``."""
    em_maos = alvo[inicio]
    chegadas: dict[int, float] = {}
    estoque_fim, faltas, demandas = [], [], []
    for m in range(inicio + 1, avaliar.stop):
        em_maos += chegadas.pop(m, 0.0)
        atendida = min(em_maos, demanda[m])
        em_maos -= atendida
        if m in avaliar and np.isfinite(observado[m]):
            estoque_fim.append(em_maos)
            faltas.append(demanda[m] - atendida)
            demandas.append(demanda[m])
        if m in alvo:
            posicao = em_maos + sum(chegadas.values())
            pedido = max(0.0, alvo[m] - posicao)
            chegadas[m + L] = chegadas.get(m + L, 0.0) + pedido
    estoque_fim, faltas, demandas = map(np.asarray, (estoque_fim, faltas, demandas))
    return {"fill_rate": 1 - faltas.sum() / demandas.sum(), "estoque_medio": estoque_fim.mean(),
            "falta_total": faltas.sum(), "demanda_media": demandas.mean(),
            "cobertura_meses": estoque_fim.mean() / demandas.mean(), "meses": len(demandas)}


def simular_politicas(df: pd.DataFrame, Y: pd.DataFrame, origens_teste: tuple[int, ...], prazos: tuple[int, ...],
                      taus: tuple[float, ...], janela: int, valor_unitario: pd.Series) -> pd.DataFrame:
    """Indicadores por (série, modelo, L, tau) nos 18 meses após a primeira origem de teste."""
    demanda_total = Y.interpolate(limit_direction="both")
    avaliar = range(origens_teste[0] + 1, origens_teste[-1] + 2)
    linhas = []
    for L in prazos:
        acum = quantis_acumulados(df, L, taus, janela)
        inicio = origens_teste[0] - L
        for (modelo, serie), g in acum.groupby(["modelo", "serie"]):
            g = g.set_index("origem")
            if inicio not in g.index:
                continue
            for tau in taus:
                S = (g["D_prev"] + g[f"qa{tau:g}"] * g["escala"] * L).clip(lower=0).to_dict()
                alvo = {o: s for o, s in S.items() if o >= inicio}
                r = simular_serie(demanda_total[serie].to_numpy(), Y[serie].to_numpy(), alvo, inicio, avaliar, L)
                linhas.append({"serie": serie, "modelo": modelo, "L": L, "tau": tau, **r})
    saida = pd.DataFrame(linhas)
    saida["valor_unitario"] = saida["serie"].map(valor_unitario)
    # Custo em "unidades de manutenção": c_m = 1 por unidade-mês; c_f = tau/(1 − tau).
    saida["custo_relativo"] = (saida["estoque_medio"] * saida["meses"]
                               + saida["falta_total"] * saida["tau"] / (1 - saida["tau"]))
    saida["valor_estoque_medio"] = saida["estoque_medio"] * saida["valor_unitario"]
    saida["valor_falta_mensal"] = saida["falta_total"] / saida["meses"] * saida["valor_unitario"]
    return saida
