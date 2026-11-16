"""Testes offline: leitura do TabNet, limpeza, métricas, ausência de vazamento, protocolo, inferência e estoque."""

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from previsao_insumos.dados import ler_tabnet_mensal, marcar_ausencias
from previsao_insumos.avaliacao import Desenho, calibrar_quantis_movel
from previsao_insumos.config import Configuracao
from previsao_insumos.estoque import simular_serie
from previsao_insumos.inferencia import (benjamini_hochberg, bootstrap_conglomerado, bootstrap_media,
                                         friedman_nemenyi, holm, wilcoxon_uf)
from previsao_insumos.metricas import escala_ingenua, escala_sazonal, pinball
from previsao_insumos.modelos import Atributos, construir_atributos, deriva, ingenuo, ingenuo_sazonal

TABNET = '''"Ano/mês processamento";"RO";"SP";"Total"
"2008";30;300;330
"..Janeiro/2008";10;100;110
"..Fevereiro/2008";-;100;100
"Março/2009";10;200;210
"Total";30;300;330
'''


class TestDados(unittest.TestCase):
    def test_le_meses_e_ignora_totais(self):
        with tempfile.TemporaryDirectory() as tmp:
            arquivo = Path(tmp, "t.csv")
            arquivo.write_text(TABNET, encoding="utf-8")
            larga = ler_tabnet_mensal(arquivo)
        self.assertEqual(list(larga.columns), ["RO", "SP"])
        self.assertEqual([str(p) for p in larga.index], ["2008-01", "2008-02", "2009-03"])
        self.assertTrue(np.isnan(larga.loc[pd.Period("2008-02", "M"), "RO"]))

    def test_zero_em_serie_grande_vira_ausente_e_em_serie_pequena_nao(self):
        idx = pd.period_range("2020-01", periods=15, freq="M")
        grande = pd.Series([1000.0] * 14 + [0.0], index=idx)
        pequena = pd.Series([3.0, 0.0] * 7 + [0.0], index=idx)
        self.assertTrue(np.isnan(marcar_ausencias(grande, 50).iloc[-1]))
        self.assertEqual(marcar_ausencias(pequena, 50).iloc[-1], 0.0)


class TestMetricas(unittest.TestCase):
    def test_escala_do_ingenuo_sazonal(self):
        y = np.arange(24, dtype=float)       # diferença sazonal constante = 12
        self.assertEqual(escala_sazonal(y), 12.0)

    def test_pinball_penaliza_mais_a_subprevisao(self):
        self.assertGreater(pinball(np.array([10.0]), np.array([8.0]), 0.9)[0],
                           pinball(np.array([10.0]), np.array([12.0]), 0.9)[0])


class TestSemVazamento(unittest.TestCase):
    def test_atributos_nao_dependem_do_futuro(self):
        z = np.log1p(np.arange(1, 61, dtype=float))
        futuro_alterado = z.copy()
        futuro_alterado[41:] = 999.0                  # muda tudo depois da origem t = 40
        t = np.array([40])
        a, _ = construir_atributos(z, t, 3, np.array([5]), np.array([0]), 0)
        b, _ = construir_atributos(futuro_alterado, t, 3, np.array([5]), np.array([0]), 0)
        np.testing.assert_array_equal(a, b)

    def test_variantes_de_atributos_tambem_nao_usam_o_futuro(self):
        z = np.log1p(np.arange(1, 61, dtype=float))
        alterado = z.copy()
        alterado[41:] = -5.0
        for variante in (Atributos(normalizar=False), Atributos(excluir=("defasagens",))):
            a, _ = construir_atributos(z, np.array([40]), 6, np.array([5]), np.array([0]), 1, variante)
            b, _ = construir_atributos(alterado, np.array([40]), 6, np.array([5]), np.array([0]), 1, variante)
            np.testing.assert_array_equal(a, b)

    def test_validacao_e_teste_nao_compartilham_meses_alvo(self):
        config = Configuracao()
        datas = pd.period_range("2008-01", periods=208, freq="M")
        d = Desenho.criar(datas, config)
        alvos_val = {o + h for o in d.origens_validacao for h in config.horizontes_previstos}
        alvos_teste = {o + h for o in d.origens_teste for h in config.horizontes_previstos}
        self.assertFalse(alvos_val & alvos_teste)
        self.assertEqual((len(d.origens_validacao), len(d.origens_teste)), (12, 18))
        self.assertEqual(d.origens_teste[-1] + config.horizonte_maximo, len(datas) - 1)

    def test_conformal_movel_usa_so_residuos_ja_observados(self):
        linhas = [{"serie": "s", "modelo": "m", "h": 1, "origem": o, "observado": 10.0 + (100.0 if o == 9 else 0.0),
                   "previsto": 10.0, "escala_cal": 1.0} for o in range(10)]
        df = calibrar_quantis_movel(pd.DataFrame(linhas), (0.9,), janela=12, minimo=1)
        # Na origem 9, o resíduo grande (alvo 10) ainda não é conhecido: o quantil continua 0.
        self.assertEqual(df.loc[df["origem"] == 9, "q0.9"].iloc[0], 0.0)

    def test_ingenuo_e_deriva(self):
        y = np.array([1.0, 2.0, 4.0, 8.0])
        np.testing.assert_array_equal(ingenuo(y, 2), [8.0, 8.0])
        self.assertGreater(deriva(y, 1)[0], 8.0)
        self.assertEqual(escala_ingenua(np.array([1.0, 3.0, 5.0])), 2.0)

    def test_ingenuo_sazonal_repete_o_ano_anterior(self):
        y = np.arange(1, 25, dtype=float)
        np.testing.assert_array_equal(ingenuo_sazonal(y, 3), [13.0, 14.0, 15.0])


class TestInferencia(unittest.TestCase):
    def test_bootstrap_conglomerado_leva_a_uf_inteira(self):
        r = pd.Series([0.0, 0.0, 1.0, 1.0])
        uf = pd.Series(["A", "A", "B", "B"])
        inf, sup = bootstrap_conglomerado(r, uf, 2000, np.random.default_rng(3))
        self.assertGreaterEqual(inf, 0.0)
        self.assertLessEqual(sup, 1.0)

    def test_wilcoxon_usa_uma_observacao_por_uf(self):
        r = pd.Series(np.r_[np.full(10, -0.2), np.full(10, -0.1)])
        uf = pd.Series(np.repeat([f"U{i}" for i in range(10)], 2))
        _, p, n = wilcoxon_uf(r, uf)
        self.assertEqual(n, 10)
        self.assertLess(p, 0.01)

    def test_benjamini_hochberg(self):
        np.testing.assert_allclose(benjamini_hochberg(np.array([0.01, 0.04, 0.03])), [0.03, 0.04, 0.04])

    def test_holm_e_monotono_e_limitado(self):
        ajustado = holm({"a": 0.01, "b": 0.04, "c": 0.03})
        self.assertAlmostEqual(ajustado["a"], 0.03)
        self.assertAlmostEqual(ajustado["c"], 0.06)
        self.assertAlmostEqual(ajustado["b"], 0.06)

    def test_bootstrap_cobre_a_media(self):
        x = np.random.default_rng(1).normal(0.5, 1, 200)
        inf, sup = bootstrap_media(x, 2000, np.random.default_rng(2))
        self.assertLess(inf, x.mean())
        self.assertGreater(sup, x.mean())

    def test_friedman_ordena_modelos(self):
        perdas = pd.DataFrame({"bom": [1.0, 1.1, 0.9, 1.0], "ruim": [2.0, 2.1, 1.9, 2.2], "medio": [1.5] * 4})
        postos, *_ = friedman_nemenyi(perdas)
        self.assertEqual(list(postos.index), ["bom", "medio", "ruim"])


class TestEstoque(unittest.TestCase):
    def test_previsao_perfeita_atende_toda_a_demanda(self):
        demanda = np.full(30, 10.0)
        L = 3
        alvo = {o: 10.0 * L for o in range(5, 28)}
        r = simular_serie(demanda, demanda, alvo, inicio=5, avaliar=range(10, 25), L=L)
        self.assertAlmostEqual(r["fill_rate"], 1.0)

    def test_nivel_baixo_gera_falta(self):
        demanda = np.full(30, 10.0)
        alvo = {o: 15.0 for o in range(5, 28)}       # metade do necessário para 3 meses
        r = simular_serie(demanda, demanda, alvo, inicio=5, avaliar=range(10, 25), L=3)
        self.assertLess(r["fill_rate"], 0.9)


if __name__ == "__main__":
    unittest.main()
