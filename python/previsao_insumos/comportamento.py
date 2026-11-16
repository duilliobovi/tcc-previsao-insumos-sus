"""Por que os algoritmos se comportam como se comportam (análise exploratória, achados A7, A9 e A10).

Executa, sobre as mesmas origens de teste e com os hiperparâmetros escolhidos na validação:
    * ablações do conjunto de atributos do ML* (sem normalização pelo nível e sem cada grupo);
    * curvas de aprendizado: número de UFs no treino e tamanho do histórico;
    * escala: tempo de CPU de um ajuste em função do número de séries (projeção para milhares);
    * interpretabilidade: importância por permutação (por grupo de atributos, no teste), dependência
      parcial e coeficientes padronizados do Ridge, com uma previsão explicada termo a termo.

Uso:  python -m previsao_insumos.comportamento   (depois de previsao_insumos.pipeline)
"""

import json
import logging
import time

import numpy as np
import pandas as pd
from sklearn.inspection import partial_dependence

from previsao_insumos.avaliacao import Contexto, Desenho, montar_previsoes, prever_ml_global
from previsao_insumos.config import Configuracao
from previsao_insumos.dados import carregar_painel, matriz_series
from previsao_insumos.modelos import GRUPOS_ATRIBUTOS, NOMES_ATRIBUTOS, Atributos, criar_modelo, precisa_imputar
from previsao_insumos.pipeline import grupo_de, selecionar_series, uf_de

logger = logging.getLogger("previsao_insumos.comportamento")


def mase_por_modelo(Y, prev, desenho, config) -> pd.DataFrame:
    d = montar_previsoes(Y, prev, desenho, config).dropna(subset=["observado"])
    d["ae"] = (d["observado"] - d["previsto"]).abs() / d["escala"]
    return d.groupby(["modelo", "h", "serie"])["ae"].mean().reset_index(name="mase")


def main() -> None:  # noqa: PLR0915
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    config = Configuracao()
    saida = config.dir_saida
    resumo = json.loads((saida / "resumo_experimento.json").read_text(encoding="utf-8"))
    hp = resumo["hiperparametros_escolhidos"]
    ml_estrela = pd.Series([v["ML*"] for k, v in resumo["vencedores_validacao"].items() if k.startswith("h")]).mode()[0]
    painel = carregar_painel(config)
    Y = matriz_series(painel)
    desenho = Desenho.criar(Y.index, config)
    Y = selecionar_series(Y, desenho)
    painel = painel[painel["serie"].isin(Y.columns)]
    covid = painel.drop_duplicates("data").set_index("data")["covid"].reindex(Y.index).to_numpy()
    ctx = Contexto.criar(Y, grupo_de(Y.columns), covid, desenho)
    esp = {ml_estrela: (ml_estrela, hp[ml_estrela])}
    origens, hs = desenho.origens_teste, config.horizontes

    # 1. Ablações ---------------------------------------------------------------
    variantes = {"completo": Atributos(), "sem normalização pelo nível": Atributos(normalizar=False)}
    variantes.update({f"sem {g.replace('_', ' ')}": Atributos(excluir=(g,)) for g in GRUPOS_ATRIBUTOS})
    ablacoes = []
    for rotulo, variante in variantes.items():
        prev, _ = prever_ml_global(ctx, esp, origens, hs, config.semente, variante)
        m = mase_por_modelo(Y, prev, desenho, config)
        ablacoes.append(m.assign(variante=rotulo))
        logger.info("ablação: %s", rotulo)
    ablacoes = pd.concat(ablacoes)
    base = ablacoes[ablacoes["variante"] == "completo"].set_index(["h", "serie"])["mase"]
    tab = []
    for rotulo, g in ablacoes.groupby("variante"):
        r = np.log(g.set_index(["h", "serie"])["mase"] / base)
        for h, rh in r.groupby(level="h"):
            tab.append({"variante": rotulo, "h": h, "mase_media": g[g["h"] == h]["mase"].mean(),
                        "razao_vs_completo": float(np.exp(rh.mean()))})
    pd.DataFrame(tab).to_csv(saida / "ablacoes.csv", index=False)

    # 2. Curvas de aprendizado (h = 1 e 6; 3 sorteios de UFs) ---------------------
    rng = np.random.default_rng(config.semente)
    ufs = uf_de(Y.columns).to_numpy()
    unicas = np.unique(ufs)
    curva = []
    for frac in (0.25, 0.5, 0.75, 1.0):
        for rep in range(3 if frac < 1 else 1):
            escolhidas = rng.choice(unicas, max(2, int(round(frac * len(unicas)))), replace=False)
            idx = np.flatnonzero(np.isin(ufs, escolhidas))
            prev, cpu = prever_ml_global(ctx, esp, origens, (1, 6), config.semente, indices_treino=idx)
            m = mase_por_modelo(Y, prev, desenho, config)
            for h, g in m.groupby("h"):
                curva.append({"dimensao": "UFs no treino", "valor": len(escolhidas), "repeticao": rep, "h": h,
                              "mase_media": g["mase"].mean(), "cpu_s_por_ajuste": cpu[ml_estrela] / (len(origens) * 2)})
    for janela in (36, 60, 96, None):
        prev, cpu = prever_ml_global(ctx, esp, origens, (1, 6), config.semente, janela_treino=janela)
        m = mase_por_modelo(Y, prev, desenho, config)
        for h, g in m.groupby("h"):
            curva.append({"dimensao": "meses de histórico no treino", "valor": janela or "todos", "repeticao": 0, "h": h,
                          "mase_media": g["mase"].mean(), "cpu_s_por_ajuste": cpu[ml_estrela] / (len(origens) * 2)})
    pd.DataFrame(curva).to_csv(saida / "curvas_aprendizado.csv", index=False)

    # 3. Escala: tempo de um ajuste em função do número de séries (réplicas das 54 séries).
    origem, h = origens[-1], 1
    Xs, alvos, Xps, _ = ctx.matrizes(origem, h, Atributos())
    X, alvo = np.vstack(Xs), np.concatenate(alvos)
    escala = []
    for fator in (1, 2, 4, 8):
        Xf = np.vstack([X] * fator) + rng.normal(0, 1e-6, (len(X) * fator, X.shape[1]))
        af = np.concatenate([alvo] * fator)
        for nome in ("Ridge", "Random Forest", "Gradient Boosting"):
            modelo = criar_modelo(nome, hp[nome], config.semente)
            Xu = np.nan_to_num(Xf) if precisa_imputar(nome) else Xf
            c0, w0 = time.process_time(), time.perf_counter()
            modelo.fit(Xu, af)
            escala.append({"series_equivalentes": 54 * fator, "linhas_treino": len(af), "algoritmo": nome,
                           "cpu_s": time.process_time() - c0, "parede_s": time.perf_counter() - w0})
    pd.DataFrame(escala).to_csv(saida / "escala_tempo.csv", index=False)

    # 4. Importância por permutação, no teste, por grupo de atributos ---------------
    perm = []
    for h in hs:
        for origem in origens:
            Xs, alvos, Xps, ancoras = ctx.matrizes(origem, h, Atributos())
            X, alvo = np.vstack(Xs), np.concatenate(alvos)
            Xp, ancora = np.vstack(Xps), np.concatenate(ancoras)
            obs = np.array([Y[s].to_numpy()[origem + h] for s in Y.columns])
            escala_s = montar_previsoes(Y, pd.DataFrame({"serie": Y.columns, "origem": origem, "h": h,
                                                        "modelo": "x", "previsto": 0.0}), desenho, config)["escala"].to_numpy()
            modelo = criar_modelo(ml_estrela, hp[ml_estrela], config.semente)
            Xt = np.nan_to_num(X) if precisa_imputar(ml_estrela) else X
            modelo.fit(Xt, alvo)
            Xpp = np.nan_to_num(Xp) if precisa_imputar(ml_estrela) else Xp
            ok = np.isfinite(obs)

            def erro(matriz):
                p = np.maximum(np.expm1(modelo.predict(matriz) + ancora), 0)
                return np.mean(np.abs(obs[ok] - p[ok]) / escala_s[ok])

            e0 = erro(Xpp)
            for grupo, cols in GRUPOS_ATRIBUTOS.items():
                for rep in range(5):
                    Xperm = Xpp.copy()
                    Xperm[:, list(cols)] = Xperm[rng.permutation(len(Xperm))][:, list(cols)]
                    perm.append({"h": h, "origem": origem, "grupo": grupo, "repeticao": rep,
                                 "aumento_mase": erro(Xperm) - e0})
    perm = pd.DataFrame(perm)
    perm.groupby(["h", "grupo"])["aumento_mase"].agg(["mean", "std"]).reset_index().to_csv(
        saida / "importancia_permutacao.csv", index=False)

    # 5. Dependência parcial (última origem de teste, h = 1) e coeficientes do Ridge ---
    Xs, alvos, Xps, ancoras = ctx.matrizes(origens[-1], 1, Atributos())
    X, alvo = np.vstack(Xs), np.concatenate(alvos)
    modelo = criar_modelo(ml_estrela, hp[ml_estrela], config.semente).fit(np.nan_to_num(X), alvo)
    dp = []
    for nome_atr in ("lag_0", "variacao_anual", "media_3m"):
        j = NOMES_ATRIBUTOS.index(nome_atr)
        res = partial_dependence(modelo, np.nan_to_num(X), [j], grid_resolution=30, percentiles=(0.05, 0.95))
        dp.extend({"atributo": nome_atr, "valor": float(v), "efeito_log": float(e)}
                  for v, e in zip(res["grid_values"][0], res["average"][0]))
    pd.DataFrame(dp).to_csv(saida / "dependencia_parcial.csv", index=False)

    ridge = criar_modelo("Ridge", hp["Ridge"], config.semente).fit(np.nan_to_num(X), alvo)
    coef = ridge[-1].coef_
    pd.DataFrame({"atributo": NOMES_ATRIBUTOS, "coeficiente_padronizado": coef}).to_csv(
        saida / "coeficientes_ridge.csv", index=False)
    # Previsão explicada: São Paulo, medicamentos, última origem de teste, h = 1 (decomposição exata do Ridge).
    s = list(Y.columns).index("06_medicamentos|SP")
    xs = np.nan_to_num(np.vstack(Xps))[s]
    padronizado = (xs - ridge[0].mean_) / ridge[0].scale_
    contrib = padronizado * coef
    nivel = float(ancoras[s][0])
    explicada = pd.DataFrame({"atributo": NOMES_ATRIBUTOS, "valor": xs, "contribuicao_log": contrib})
    explicada.to_csv(saida / "previsao_explicada_sp.csv", index=False)
    (saida / "previsao_explicada_sp.json").write_text(json.dumps({
        "serie": "06_medicamentos|SP", "origem": str(desenho.datas[origens[-1]]),
        "alvo": str(desenho.datas[origens[-1] + 1]), "nivel_log1p": nivel, "intercepto": float(ridge[-1].intercept_),
        "previsao": float(np.expm1(nivel + ridge[-1].intercept_ + contrib.sum())),
        "observado": float(Y.iloc[origens[-1] + 1, s]), "nivel": float(np.expm1(nivel))}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    try:
        from previsao_insumos import numeros
        numeros.gerar()
    except ImportError:
        pass
    logger.info("comportamento concluído")


if __name__ == "__main__":
    main()
